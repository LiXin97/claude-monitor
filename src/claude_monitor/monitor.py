import asyncio
import logging
import signal

from claude_monitor.config import Config
from claude_monitor.hook_server import HookServer
from claude_monitor.scraper import discover_panes, capture_pane
from claude_monitor.state import StateTracker
from claude_monitor.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)


class Monitor:
    """Main monitor loop: scrape → detect state → notify."""

    def __init__(self, config: Config):
        self._config = config
        self._state_tracker = StateTracker(stable_threshold=config.stable_threshold)
        self._telegram = TelegramBot(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            machine_name=config.machine_name,
            state_tracker=self._state_tracker,
            notification_silence_seconds=config.notification_silence_seconds,
        )
        self._running = False
        self._known_panes: set[str] = set()
        self._hook_server: HookServer | None = None

    async def _poll_once(self) -> None:
        """Run one poll cycle."""
        sessions = self._config.sessions or None
        panes = discover_panes(sessions=sessions)
        current_pane_ids = {p.pane_id for p in panes}

        # Clean up removed panes
        removed = self._known_panes - current_pane_ids
        for pane_id in removed:
            self._state_tracker.remove_pane(pane_id)
            logger.info("Pane removed: %s", pane_id)
        self._known_panes = current_pane_ids

        # Capture and update state for each pane
        for pane in panes:
            content = capture_pane(pane.pane_id, context_lines=self._config.context_lines)
            if not content:
                continue

            transition = self._state_tracker.update(pane.pane_id, content)
            if transition is not None:
                logger.info(
                    "State transition: %s %s → %s",
                    pane.pane_id,
                    transition.old_state.value,
                    transition.new_state.value,
                )
                await self._telegram.send_notification(transition)

        # Update waiting panes list and aliases
        self._telegram.update_pane_aliases([p.pane_id for p in panes])
        self._telegram.update_pane_cwds({p.pane_id: p.cwd for p in panes if p.cwd})
        self._telegram.update_waiting_panes(self._state_tracker.get_all_states())

    async def run(self) -> None:
        """Run the monitor loop."""
        self._running = True
        logger.info(
            "Starting monitor on [%s], polling every %ds",
            self._config.machine_name,
            self._config.poll_interval,
        )

        await self._telegram.initialize()

        # Start hook server if enabled
        if self._config.hooks_enabled:
            self._hook_server = HookServer(
                telegram_bot=self._telegram,
                port=self._config.hook_server_port,
                machine_name=self._config.machine_name,
            )
            self._telegram._hook_server = self._hook_server
            await self._hook_server.start()
            logger.info("Hook server started on port %d", self._hook_server.port)

        # Send startup message
        try:
            await self._telegram.send_message(
                f"🚀 [{self._config.machine_name}] Claude Monitor started"
            )
        except Exception as e:
            logger.error("Failed to send startup message: %s", e)

        try:
            while self._running:
                try:
                    await self._poll_once()
                except Exception as e:
                    logger.error("Poll error: %s", e)
                await asyncio.sleep(self._config.poll_interval)
        finally:
            try:
                await self._telegram.send_message(
                    f"🛑 [{self._config.machine_name}] Claude Monitor stopped"
                )
            except Exception:
                pass
            if self._hook_server:
                await self._hook_server.stop()
            await self._telegram.shutdown()

    def stop(self) -> None:
        self._running = False
