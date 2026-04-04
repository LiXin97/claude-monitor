"""
End-to-end smoke test: mock tmux, run a few poll cycles,
verify notifications fire correctly.
"""
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from claude_monitor.config import Config
from claude_monitor.monitor import Monitor
from claude_monitor.state import PaneState
from claude_monitor.scraper import TmuxPane


WORKING = "● Agent(Research task)\n  ⎿  Running...\n\n✢ Searching… (2m 30s)"

IDLE = """\
● Done with the research.

✻ Worked for 5m 12s

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""

NEEDS_INPUT = """\
● Should I proceed with approach A or B?

✻ Crunched for 1m 5s

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""


@pytest.fixture
def config():
    return Config(
        telegram_bot_token="fake:token",
        telegram_chat_id=123,
        machine_name="test-box",
        poll_interval=0,
        stable_threshold=2,
        context_lines=30,
    )


@pytest.mark.asyncio
async def test_full_lifecycle(config):
    """Simulate: working → idle → needs_input, verify 2 notifications."""
    panes = [TmuxPane(pane_id="work:0.0", command="claude", pid=100)]

    # Sequence: 2x working, 2x idle, 2x needs_input
    contents = [WORKING, WORKING, IDLE, IDLE, NEEDS_INPUT, NEEDS_INPUT]
    content_iter = iter(contents)

    def fake_capture(pane_id, context_lines=30):
        return next(content_iter)

    notifications = []

    async def capture_notification(transition):
        notifications.append(transition)

    with (
        patch("claude_monitor.monitor.discover_panes", return_value=panes),
        patch("claude_monitor.monitor.capture_pane", side_effect=fake_capture),
    ):
        monitor = Monitor(config)
        monitor._telegram = AsyncMock()
        monitor._telegram.send_notification = AsyncMock(side_effect=capture_notification)
        monitor._telegram.update_waiting_panes = MagicMock()
        monitor._telegram.update_pane_aliases = MagicMock()

        for _ in range(6):
            await monitor._poll_once()

    assert len(notifications) == 2
    assert notifications[0].old_state == PaneState.WORKING
    assert notifications[0].new_state == PaneState.IDLE
    assert notifications[1].old_state == PaneState.IDLE
    assert notifications[1].new_state == PaneState.NEEDS_INPUT
