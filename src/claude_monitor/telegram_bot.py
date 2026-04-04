import asyncio
import logging
import random
import re
import time
from html import escape as escape_html

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from claude_monitor.state import PaneState, StateTransition, StateTracker
from claude_monitor.scraper import capture_pane, send_keys

logger = logging.getLogger(__name__)

STATE_ICONS = {
    PaneState.WORKING: "🔵",
    PaneState.IDLE: "🟢",
    PaneState.NEEDS_INPUT: "🟡",
    PaneState.PERMISSION: "🔴",
    PaneState.UNKNOWN: "⚪",
}

_NOTIFICATION_HEADERS = {
    PaneState.IDLE: ("🟢", "Claude Code finished task"),
    PaneState.NEEDS_INPUT: ("🟡", "Claude Code waiting for input"),
    PaneState.PERMISSION: ("🔴", "Claude Code asking permission"),
}


def extract_context_lines(content: str, max_lines: int = 10) -> list[str]:
    """Extract meaningful context lines from pane content, filtering noise."""
    lines = content.strip().splitlines()
    filtered = []
    for line in lines:
        stripped = line.strip()
        # Skip separator lines, status bars, empty lines
        if re.match(r"^─+$", stripped):
            continue
        if "bypass permissions" in stripped:
            continue
        if re.match(r"^⏵", stripped):
            continue
        if re.match(r"^❯\s*$", stripped):
            continue
        if stripped == "":
            continue
        filtered.append(line)
    # Return last max_lines meaningful lines
    return filtered[-max_lines:]


def format_notification(
    machine_name: str, transition: StateTransition, alias: int | None = None
) -> str:
    """Format a state transition into a Telegram notification message."""
    context = extract_context_lines(transition.content, max_lines=15)
    context_text = "\n".join(context)

    icon, msg = _NOTIFICATION_HEADERS.get(
        transition.new_state,
        ("ℹ️", f"State → {transition.new_state.value}"),
    )
    header = f"{icon} <b>[{machine_name}] {msg}</b>"

    session_label = transition.pane_id
    if alias is not None:
        session_label = f"{alias}: {transition.pane_id}"

    return (
        f"{header}\n"
        f"Session: <code>{session_label}</code>\n\n"
        f"<pre>{escape_html(context_text)}</pre>"
    )


def parse_send_command(args: str) -> tuple[str, str | None, str] | None:
    """Parse /send command arguments.

    Formats:
        "machine-name some text" -> (machine_name, None, text)
        "machine-name:session:win.pane some text" -> (machine_name, pane_id, text)
        "machine-name 2 some text" -> (machine_name, "2", text)  # numeric alias
    """
    args = args.strip()
    if not args:
        return None

    parts = args.split(None, 1)
    if len(parts) < 2:
        return None

    target, rest = parts

    # Check if target contains a pane specifier (machine:session:win.pane)
    colon_idx = target.find(":")
    if colon_idx > 0:
        machine = target[:colon_idx]
        pane_id = target[colon_idx + 1:]
        return (machine, pane_id, rest)

    # Check if rest starts with a numeric alias: "2 some text"
    rest_parts = rest.split(None, 1)
    if len(rest_parts) == 2 and rest_parts[0].isdigit():
        return (target, rest_parts[0], rest_parts[1])

    return (target, None, rest)


def extract_pane_from_notification(text: str) -> str | None:
    """Extract pane_id from notification message.

    Matches 'Session: <code>pane_id</code>' or 'Session: <code>N: pane_id</code>'
    """
    match = re.search(r"Session:.*?<code>(?:\d+:\s*)?(.+?)</code>", text)
    return match.group(1) if match else None


class TelegramBot:
    """Telegram bot for notifications and remote control."""

    def __init__(
        self,
        bot_token: str,
        chat_id: int,
        machine_name: str,
        state_tracker: StateTracker,
        notification_silence_seconds: int = 300,
    ):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._machine_name = machine_name
        self._state_tracker = state_tracker
        self._app: Application | None = None
        self._poll_task: asyncio.Task | None = None
        # Track which panes are awaiting input (for quick reply)
        self._waiting_panes: list[str] = []
        # Pane aliases: pane_id → numeric alias (1, 2, 3...)
        self._pane_aliases: dict[str, int] = {}
        self._alias_to_pane: dict[int, str] = {}
        self._next_alias: int = 1
        # Smart Silence: suppress notifications when user recently interacted
        self._last_interaction: float = 0.0
        self._silence_seconds = notification_silence_seconds
        # Hook server reference (set by Monitor when hooks are enabled)
        self._hook_server = None
        # cwd → pane_id mapping (updated each poll cycle)
        self._cwd_to_pane: dict[str, str] = {}

    async def initialize(self) -> None:
        self._app = (
            Application.builder()
            .token(self._bot_token)
            .build()
        )
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(CommandHandler("view", self._handle_view))
        self._app.add_handler(CommandHandler("send", self._handle_send))
        self._app.add_handler(CommandHandler("machines", self._handle_machines))
        self._app.add_handler(CallbackQueryHandler(self._handle_button_press))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_quick_reply)
        )
        await self._app.initialize()
        await self._app.start()

        # Start our own polling loop instead of the library's start_polling().
        # This lets us catch Conflict errors (409) silently when another
        # machine is already polling the same bot token.
        self._poll_task = asyncio.create_task(self._poll_updates())

        # Set bot menu commands
        try:
            await self._app.bot.set_my_commands([
                BotCommand("status", "Show Claude Code pane states"),
                BotCommand("view", "View last 30 lines of a pane"),
                BotCommand("send", "Send input to a pane"),
                BotCommand("machines", "List all connected machines"),
            ])
        except Exception:
            pass  # Non-critical, other instance may have set them

    async def _poll_updates(self) -> None:
        """Poll for updates with conflict handling for multi-instance sharing.

        Uses timeout=0 (non-blocking) so multiple machines sharing the same
        bot token take turns fetching updates.  Each Telegram update is only
        delivered to one machine — use `/status <machine>` to target a
        specific one, or bare `/status` to get whichever machine grabs it.
        """
        offset = 0
        while True:
            try:
                updates = await self._app.bot.get_updates(
                    offset=offset, timeout=0
                )
                for update in updates:
                    offset = update.update_id + 1
                    await self._app.process_update(update)
            except (Conflict, TimedOut):
                pass  # Expected in multi-instance setup, retry next cycle
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Polling error: %s", e)
            await asyncio.sleep(1.5 + random.random())

    async def shutdown(self) -> None:
        if self._app:
            if self._poll_task:
                self._poll_task.cancel()
                try:
                    await self._poll_task
                except asyncio.CancelledError:
                    pass
            await self._app.stop()
            await self._app.shutdown()

    def _is_authorized(self, update: Update) -> bool:
        return (
            update.effective_chat is not None
            and update.effective_chat.id == self._chat_id
        )

    async def send_message(self, text: str, parse_mode: str | None = None) -> None:
        """Send a plain text message to the configured chat."""
        if self._app:
            await self._app.bot.send_message(
                chat_id=self._chat_id, text=text, parse_mode=parse_mode,
            )

    async def send_notification(self, transition: StateTransition) -> None:
        """Send a notification message for a state transition."""
        if not self._app:
            return
        if self._should_suppress_notification():
            logger.debug("Notification suppressed (user active on Telegram)")
            return
        alias = self._pane_aliases.get(transition.pane_id)
        msg = format_notification(self._machine_name, transition, alias=alias)

        # Build inline keyboard based on transition state
        pane_id = transition.pane_id
        reply_markup = None
        if transition.new_state == PaneState.PERMISSION:
            keyboard = [[
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{pane_id}"),
                InlineKeyboardButton("❌ Deny", callback_data=f"deny:{pane_id}"),
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        elif transition.new_state in (PaneState.IDLE, PaneState.NEEDS_INPUT):
            keyboard = [[
                InlineKeyboardButton("📺 View", callback_data=f"view:{pane_id}"),
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)

        await self._app.bot.send_message(
            chat_id=self._chat_id, text=msg, parse_mode="HTML",
            reply_markup=reply_markup,
        )

    def _should_suppress_notification(self) -> bool:
        """Check if notifications should be suppressed due to recent user interaction."""
        if self._silence_seconds <= 0:
            return False
        return (time.time() - self._last_interaction) < self._silence_seconds

    async def send_hook_permission(
        self,
        request_id: str,
        label: str,
        tool_name: str,
        input_preview: str,
        project: str = "",
        pane_label: str = "",
    ) -> None:
        """Send a hook permission request with Approve/Deny buttons."""
        if not self._app:
            return
        parts = [f"🔐 {label}Claude Code permission request"]
        if pane_label:
            parts.append(f"Session: <code>{escape_html(pane_label)}</code>")
        if project:
            parts.append(f"Project: <code>{escape_html(project)}</code>")
        parts.append(f"Tool: <code>{escape_html(tool_name)}</code>")
        parts.append(f"\n<pre>{escape_html(input_preview)}</pre>")
        msg = "\n".join(parts)
        keyboard = [[
            InlineKeyboardButton("✅ Allow", callback_data=f"hook_approve:{request_id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"hook_deny:{request_id}"),
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self._app.bot.send_message(
            chat_id=self._chat_id, text=msg, parse_mode="HTML",
            reply_markup=reply_markup,
        )

    def update_waiting_panes(self, pane_states: dict[str, PaneState]) -> None:
        """Update the list of panes waiting for input."""
        self._waiting_panes = [
            pid for pid, state in pane_states.items()
            if state in (PaneState.NEEDS_INPUT, PaneState.PERMISSION)
        ]

    def update_pane_aliases(self, pane_ids: list[str]) -> None:
        """Assign stable numeric aliases to discovered panes.

        New panes get the next available number.  Removed panes lose their
        alias (the number is not reused until restart).
        """
        current = set(pane_ids)
        # Remove aliases for gone panes
        gone = set(self._pane_aliases) - current
        for pid in gone:
            alias = self._pane_aliases.pop(pid)
            self._alias_to_pane.pop(alias, None)
        # Assign aliases to new panes
        for pid in pane_ids:
            if pid not in self._pane_aliases:
                self._pane_aliases[pid] = self._next_alias
                self._alias_to_pane[self._next_alias] = pid
                self._next_alias += 1

    def update_pane_cwds(self, pane_cwds: dict[str, str]) -> None:
        """Update the cwd → pane_id mapping (called each poll cycle)."""
        self._cwd_to_pane = {cwd: pid for pid, cwd in pane_cwds.items()}

    def _resolve_pane(self, alias_or_id: str) -> str | None:
        """Resolve a numeric alias or raw pane_id to a pane_id."""
        if alias_or_id.isdigit():
            return self._alias_to_pane.get(int(alias_or_id))
        return alias_or_id

    def _format_pane_label(self, pane_id: str) -> str:
        """Format pane_id with its alias for display, e.g. '1: copilot-api:1.0'."""
        alias = self._pane_aliases.get(pane_id)
        if alias is not None:
            return f"{alias}: {pane_id}"
        return pane_id

    async def _handle_button_press(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle inline keyboard button presses."""
        query = update.callback_query
        await query.answer()

        if not query.data:
            return

        parts = query.data.split(":", 1)
        if len(parts) != 2:
            return
        action, pane_id = parts[0], parts[1]

        if action == "approve":
            ok = send_keys(pane_id, "y")
            status = "✅ Approved" if ok else "❌ Failed to approve"
            try:
                await query.edit_message_text(
                    text=query.message.text_html + f"\n\n{status}", parse_mode="HTML"
                )
            except Exception:
                pass
        elif action == "deny":
            ok = send_keys(pane_id, "n")
            status = "❌ Denied" if ok else "❌ Failed to deny"
            try:
                await query.edit_message_text(
                    text=query.message.text_html + f"\n\n{status}", parse_mode="HTML"
                )
            except Exception:
                pass
        elif action == "view":
            content = capture_pane(pane_id, context_lines=30)
            if content:
                if len(content) > 3900:
                    content = content[-3900:]
                label = self._format_pane_label(pane_id)
                await query.message.reply_text(
                    f"📺 {label}:\n```\n{content}\n```", parse_mode="Markdown"
                )
            else:
                await query.message.reply_text(f"Could not capture pane {pane_id}")
        elif action == "hook_approve" and self._hook_server:
            self._hook_server.resolve_permission(pane_id, allow=True)
            try:
                await query.edit_message_text(
                    text=query.message.text_html + "\n\n✅ Allowed", parse_mode="HTML"
                )
            except Exception:
                pass
        elif action == "hook_deny" and self._hook_server:
            self._hook_server.resolve_permission(pane_id, allow=False)
            try:
                await query.edit_message_text(
                    text=query.message.text_html + "\n\n❌ Denied", parse_mode="HTML"
                )
            except Exception:
                pass

    async def _handle_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return
        self._last_interaction = time.time()

        args = context.args or []
        # /status with a different machine name: ignore (not for us)
        if args and args[0] != self._machine_name:
            return

        states = self._state_tracker.get_all_states()
        if not states:
            await update.message.reply_text(
                f"[{self._machine_name}] No Claude Code sessions found."
            )
            return

        lines = [f"📊 [{self._machine_name}] Status:"]
        for pane_id, state in states.items():
            icon = STATE_ICONS.get(state, "⚪")
            label = self._format_pane_label(pane_id)
            lines.append(f"  {icon} {label}: {state.value}")
        await update.message.reply_text("\n".join(lines))

    async def _handle_view(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return
        self._last_interaction = time.time()

        args = context.args or []
        if not args:
            await update.message.reply_text(
                f"Usage: /view {self._machine_name} [pane_alias]\n"
                f"Example: /view {self._machine_name} 1"
            )
            return

        target_machine = args[0]
        if target_machine != self._machine_name:
            return  # Not for this machine

        # Determine which pane to view
        states = self._state_tracker.get_all_states()
        if not states:
            await update.message.reply_text(
                f"[{self._machine_name}] No active sessions."
            )
            return

        pane_id = args[1] if len(args) > 1 else next(iter(states))
        # Resolve numeric alias to pane_id
        resolved = self._resolve_pane(pane_id)
        if resolved is None:
            await update.message.reply_text(f"Unknown pane alias: {pane_id}")
            return
        pane_id = resolved
        content = capture_pane(pane_id, context_lines=30)
        if not content:
            await update.message.reply_text(f"Could not capture pane {pane_id}")
            return

        # Truncate for Telegram message limit (4096 chars)
        if len(content) > 3900:
            content = content[-3900:]
        label = self._format_pane_label(pane_id)
        await update.message.reply_text(
            f"📺 [{self._machine_name}] {label}:\n```\n{content}\n```",
            parse_mode="Markdown",
        )

    async def _handle_send(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return
        self._last_interaction = time.time()

        raw_args = " ".join(context.args) if context.args else ""
        parsed = parse_send_command(raw_args)
        if parsed is None:
            await update.message.reply_text(
                f"Usage: /send {self._machine_name} <text>\n"
                f"       /send {self._machine_name} <alias> <text>\n"
                f"Example: /send {self._machine_name} 1 yes"
            )
            return

        machine, pane_id, text = parsed
        if machine != self._machine_name:
            return  # Not for this machine

        # If no pane specified, use the first waiting pane or first known pane
        if pane_id is None:
            if self._waiting_panes:
                pane_id = self._waiting_panes[0]
            else:
                states = self._state_tracker.get_all_states()
                if states:
                    pane_id = next(iter(states))
                else:
                    await update.message.reply_text("No active sessions found.")
                    return
        else:
            # Resolve numeric alias
            resolved = self._resolve_pane(pane_id)
            if resolved is None:
                await update.message.reply_text(f"Unknown pane alias: {pane_id}")
                return
            pane_id = resolved

        ok = send_keys(pane_id, text)
        label = self._format_pane_label(pane_id)
        if ok:
            await update.message.reply_text(f"✅ Sent to {label}")
        else:
            await update.message.reply_text(f"❌ Failed to send to {label}")

    async def _handle_machines(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return
        self._last_interaction = time.time()

        # This machine only knows about itself
        states = self._state_tracker.get_all_states()
        pane_count = len(states)
        active = sum(1 for s in states.values() if s == PaneState.WORKING)
        await update.message.reply_text(
            f"🖥 {self._machine_name}: {pane_count} pane(s), {active} working"
        )

    async def _handle_quick_reply(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Forward plain text to the sole waiting pane (quick reply shortcut)."""
        if not self._is_authorized(update):
            return
        self._last_interaction = time.time()

        # Reply routing: if replying to a notification, route to that pane
        if update.message.reply_to_message is not None:
            original_text = update.message.reply_to_message.text or ""
            pane_id = extract_pane_from_notification(original_text)
            if pane_id:
                text = update.message.text
                ok = send_keys(pane_id, text)
                label = self._format_pane_label(pane_id)
                if ok:
                    await update.message.reply_text(f"✅ → {label}")
                else:
                    await update.message.reply_text(f"❌ Failed to send to {label}")
                return

        if len(self._waiting_panes) != 1:
            if len(self._waiting_panes) == 0:
                await update.message.reply_text(
                    "No pane is waiting for input. Use /send <machine> <text>."
                )
            else:
                pane_list = ", ".join(
                    self._format_pane_label(p) for p in self._waiting_panes
                )
                await update.message.reply_text(
                    f"Multiple panes waiting: {pane_list}\n"
                    "Use /send <machine> <alias> <text> to specify."
                )
            return

        pane_id = self._waiting_panes[0]
        text = update.message.text
        ok = send_keys(pane_id, text)
        label = self._format_pane_label(pane_id)
        if ok:
            await update.message.reply_text(f"✅ → {label}")
        else:
            await update.message.reply_text(f"❌ Failed to send to {label}")
