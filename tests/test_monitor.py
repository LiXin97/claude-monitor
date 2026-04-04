import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from claude_monitor.monitor import Monitor
from claude_monitor.config import Config
from claude_monitor.state import PaneState
from claude_monitor.scraper import TmuxPane


@pytest.fixture
def config():
    return Config(
        telegram_bot_token="fake:token",
        telegram_chat_id=123,
        machine_name="test-box",
        poll_interval=1,
        stable_threshold=2,
        context_lines=30,
    )


@pytest.mark.asyncio
async def test_monitor_single_poll_cycle(config):
    """Test that one poll cycle discovers panes and updates state."""
    fake_panes = [TmuxPane(pane_id="work:0.0", command="claude", pid=123)]
    fake_content = "● Working on stuff\n✢ building..."

    with (
        patch("claude_monitor.monitor.discover_panes", return_value=fake_panes),
        patch("claude_monitor.monitor.capture_pane", return_value=fake_content),
    ):
        monitor = Monitor(config)
        monitor._telegram = AsyncMock()
        monitor._telegram.send_notification = AsyncMock()
        monitor._telegram.update_waiting_panes = MagicMock()
        monitor._telegram.update_pane_aliases = MagicMock()
        monitor._telegram.update_pane_cwds = MagicMock()

        await monitor._poll_once()

        assert monitor._state_tracker.get_state("work:0.0") != PaneState.UNKNOWN


@pytest.mark.asyncio
async def test_monitor_sends_notification_on_transition(config):
    """After stable_threshold polls, a transition should trigger notification."""
    working_content = "● Working on stuff\n✢ building..."
    idle_content = (
        "● Done\n\n✻ Brewed for 40s\n\n"
        "──────────────────────────\n"
        "❯ \n"
        "──────────────────────────\n"
        "  ⏵⏵ bypass permissions on"
    )

    fake_panes = [TmuxPane(pane_id="work:0.0", command="claude", pid=123)]
    call_count = 0

    def capture_side_effect(pane_id, context_lines=30):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return working_content
        return idle_content

    with (
        patch("claude_monitor.monitor.discover_panes", return_value=fake_panes),
        patch("claude_monitor.monitor.capture_pane", side_effect=capture_side_effect),
    ):
        monitor = Monitor(config)
        monitor._telegram = AsyncMock()
        monitor._telegram.send_notification = AsyncMock()
        monitor._telegram.update_waiting_panes = MagicMock()
        monitor._telegram.update_pane_aliases = MagicMock()
        monitor._telegram.update_pane_cwds = MagicMock()

        # 2 working polls to establish state
        await monitor._poll_once()
        await monitor._poll_once()
        # 2 idle polls to trigger transition
        await monitor._poll_once()
        await monitor._poll_once()

        monitor._telegram.send_notification.assert_called_once()
        call_args = monitor._telegram.send_notification.call_args
        transition = call_args[0][0]
        assert transition.new_state == PaneState.IDLE


@pytest.mark.asyncio
async def test_monitor_cleans_up_removed_panes(config):
    pane = TmuxPane(pane_id="work:0.0", command="claude", pid=123)

    with (
        patch("claude_monitor.monitor.discover_panes") as mock_discover,
        patch("claude_monitor.monitor.capture_pane", return_value="● Bash(ls)\n  ⎿  Running..."),
    ):
        monitor = Monitor(config)
        monitor._telegram = AsyncMock()
        monitor._telegram.send_notification = AsyncMock()
        monitor._telegram.update_waiting_panes = MagicMock()
        monitor._telegram.update_pane_aliases = MagicMock()
        monitor._telegram.update_pane_cwds = MagicMock()

        # Pane exists
        mock_discover.return_value = [pane]
        await monitor._poll_once()
        assert monitor._state_tracker.get_state("work:0.0") != PaneState.UNKNOWN

        # Pane removed
        mock_discover.return_value = []
        await monitor._poll_once()
        assert monitor._state_tracker.get_state("work:0.0") == PaneState.UNKNOWN
