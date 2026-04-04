import asyncio
import logging
import random
import re
from html import escape as escape_html

from telegram import BotCommand, Update
from telegram.error import Conflict
from telegram.ext import (
    Application,
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


def format_notification(machine_name: str, transition: StateTransition) -> str:
    """Format a state transition into a Telegram notification message."""
    context = extract_context_lines(transition.content, max_lines=15)
    context_text = "\n".join(context)

    icon, msg = _NOTIFICATION_HEADERS.get(
        transition.new_state,
        ("ℹ️", f"State → {transition.new_state.value}"),
    )
    header = f"{icon} <b>[{machine_name}] {msg}</b>"

    return (
        f"{header}\n"
        f"Session: <code>{transition.pane_id}</code>\n\n"
        f"<pre>{escape_html(context_text)}</pre>"
    )


def parse_send_command(args: str) -> tuple[str, str | None, str] | None:
    """Parse /send command arguments.

    Formats:
        "machine-name some text" -> (machine_name, None, text)
        "machine-name:session:win.pane some text" -> (machine_name, pane_id, text)
    """
    args = args.strip()
    if not args:
        return None

    parts = args.split(None, 1)
    if len(parts) < 2:
        return None

    target, text = parts

    # Check if target contains a pane specifier (machine:session:win.pane)
    # Machine names don't contain colons; pane IDs have format session:win.pane
    colon_idx = target.find(":")
    if colon_idx > 0:
        # Could be machine:session:win.pane
        machine = target[:colon_idx]
        pane_id = target[colon_idx + 1:]
        return (machine, pane_id, text)

    return (target, None, text)


class TelegramBot:
    """Telegram bot for notifications and remote control."""

    def __init__(
        self,
        bot_token: str,
        chat_id: int,
        machine_name: str,
        state_tracker: StateTracker,
    ):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._machine_name = machine_name
        self._state_tracker = state_tracker
        self._app: Application | None = None
        self._poll_task: asyncio.Task | None = None
        # Track which panes are awaiting input (for quick reply)
        self._waiting_panes: list[str] = []

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
            except Conflict:
                pass  # Another instance grabbed this poll, try again next cycle
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

    async def send_message(self, text: str) -> None:
        """Send a plain text message to the configured chat."""
        if self._app:
            await self._app.bot.send_message(chat_id=self._chat_id, text=text)

    async def send_notification(self, transition: StateTransition) -> None:
        """Send a notification message for a state transition."""
        if not self._app:
            return
        msg = format_notification(self._machine_name, transition)
        await self._app.bot.send_message(
            chat_id=self._chat_id, text=msg, parse_mode="HTML"
        )

    def update_waiting_panes(self, pane_states: dict[str, PaneState]) -> None:
        """Update the list of panes waiting for input."""
        self._waiting_panes = [
            pid for pid, state in pane_states.items()
            if state in (PaneState.NEEDS_INPUT, PaneState.PERMISSION)
        ]

    async def _handle_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

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
            lines.append(f"  {icon} {pane_id}: {state.value}")
        await update.message.reply_text("\n".join(lines))

    async def _handle_view(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /view <machine> [pane]")
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
        content = capture_pane(pane_id, context_lines=30)
        if not content:
            await update.message.reply_text(f"Could not capture pane {pane_id}")
            return

        # Truncate for Telegram message limit (4096 chars)
        if len(content) > 3900:
            content = content[-3900:]
        await update.message.reply_text(
            f"📺 [{self._machine_name}] {pane_id}:\n```\n{content}\n```",
            parse_mode="Markdown",
        )

    async def _handle_send(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        raw_args = " ".join(context.args) if context.args else ""
        parsed = parse_send_command(raw_args)
        if parsed is None:
            await update.message.reply_text(
                "Usage: /send <machine> <text>\n"
                "       /send <machine>:<pane> <text>"
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

        ok = send_keys(pane_id, text)
        if ok:
            await update.message.reply_text(f"✅ Sent to {pane_id}")
        else:
            await update.message.reply_text(f"❌ Failed to send to {pane_id}")

    async def _handle_machines(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

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

        if len(self._waiting_panes) != 1:
            if len(self._waiting_panes) == 0:
                await update.message.reply_text(
                    "No pane is waiting for input. Use /send <machine> <text>."
                )
            else:
                pane_list = ", ".join(self._waiting_panes)
                await update.message.reply_text(
                    f"Multiple panes waiting: {pane_list}\n"
                    "Use /send <machine>:<pane> <text> to specify."
                )
            return

        pane_id = self._waiting_panes[0]
        text = update.message.text
        ok = send_keys(pane_id, text)
        if ok:
            await update.message.reply_text(f"✅ → {pane_id}")
        else:
            await update.message.reply_text(f"❌ Failed to send to {pane_id}")
