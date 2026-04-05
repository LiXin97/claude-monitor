from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_monitor.telegram_bot import (
    format_notification,
    parse_send_command,
    extract_context_lines,
    extract_pane_from_notification,
    TelegramBot,
)
from claude_monitor.state import PaneState, StateTransition, StateTracker


IDLE_CONTENT = """\
● Fixed the authentication bug in login.py

✻ Brewed for 40s

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on"""

NEEDS_INPUT_CONTENT = """\
● Which approach should I take?
  1. Refactor the existing code
  2. Write a new implementation

✻ Crunched for 2m 30s

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on"""

PERMISSION_CONTENT = """\
● Bash(npm install express)

  Allow? (y/n)

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────"""


def test_format_idle_notification():
    transition = StateTransition(
        pane_id="work:0.0",
        old_state=PaneState.WORKING,
        new_state=PaneState.IDLE,
        content=IDLE_CONTENT,
    )
    msg = format_notification("lab-server", transition)
    assert "🟢" in msg
    assert "[lab-server]" in msg
    assert "finished" in msg.lower()
    assert "work:0.0" in msg
    assert "Brewed for 40s" in msg


def test_format_notification_with_alias():
    transition = StateTransition(
        pane_id="work:0.0",
        old_state=PaneState.WORKING,
        new_state=PaneState.IDLE,
        content=IDLE_CONTENT,
    )
    msg = format_notification("lab-server", transition, alias=3)
    assert "3: work:0.0" in msg


def test_format_needs_input_notification():
    transition = StateTransition(
        pane_id="work:0.0",
        old_state=PaneState.WORKING,
        new_state=PaneState.NEEDS_INPUT,
        content=NEEDS_INPUT_CONTENT,
    )
    msg = format_notification("lab-server", transition)
    assert "🟡" in msg
    assert "waiting" in msg.lower() or "input" in msg.lower()
    assert "Which approach" in msg


def test_format_permission_notification():
    transition = StateTransition(
        pane_id="work:0.0",
        old_state=PaneState.WORKING,
        new_state=PaneState.PERMISSION,
        content=PERMISSION_CONTENT,
    )
    msg = format_notification("lab-server", transition)
    assert "🔴" in msg
    assert "permission" in msg.lower()
    assert "npm install express" in msg


def test_parse_send_command_simple():
    machine, pane, text = parse_send_command("lab-server hello world")
    assert machine == "lab-server"
    assert pane is None
    assert text == "hello world"


def test_parse_send_command_with_pane():
    machine, pane, text = parse_send_command("lab-server:work:0.0 yes")
    assert machine == "lab-server"
    assert pane == "work:0.0"
    assert text == "yes"


def test_parse_send_command_empty():
    result = parse_send_command("")
    assert result is None


def test_extract_context_lines():
    lines = extract_context_lines(NEEDS_INPUT_CONTENT, max_lines=5)
    assert len(lines) <= 5
    # Should not include separator lines or status bar
    for line in lines:
        assert "────" not in line
        assert "bypass permissions" not in line


def test_parse_send_command_with_alias():
    machine, pane, text = parse_send_command("lab-server 2 hello world")
    assert machine == "lab-server"
    assert pane == "2"
    assert text == "hello world"


def test_parse_send_command_alias_single_word():
    """Numeric alias requires text after it."""
    machine, pane, text = parse_send_command("lab-server 2")
    # "2" is treated as text (no pane), not alias (needs text after)
    assert machine == "lab-server"
    assert pane is None
    assert text == "2"


# --- Pane alias tests ---

def test_update_pane_aliases_assigns_numbers():
    from claude_monitor.state import StateTracker
    bot = TelegramBot("token", 123, "test", StateTracker())
    bot.update_pane_aliases(["work:0.0", "research:1.0"])
    assert bot._pane_aliases == {"work:0.0": 1, "research:1.0": 2}
    assert bot._alias_to_pane == {1: "work:0.0", 2: "research:1.0"}


def test_update_pane_aliases_stable_on_repeat():
    from claude_monitor.state import StateTracker
    bot = TelegramBot("token", 123, "test", StateTracker())
    bot.update_pane_aliases(["work:0.0", "research:1.0"])
    bot.update_pane_aliases(["work:0.0", "research:1.0"])
    # Same aliases after repeated calls
    assert bot._pane_aliases == {"work:0.0": 1, "research:1.0": 2}


def test_update_pane_aliases_removes_gone():
    from claude_monitor.state import StateTracker
    bot = TelegramBot("token", 123, "test", StateTracker())
    bot.update_pane_aliases(["work:0.0", "research:1.0"])
    bot.update_pane_aliases(["research:1.0"])
    assert "work:0.0" not in bot._pane_aliases
    assert 1 not in bot._alias_to_pane
    assert bot._pane_aliases == {"research:1.0": 2}


def test_update_pane_aliases_new_pane_gets_next_number():
    from claude_monitor.state import StateTracker
    bot = TelegramBot("token", 123, "test", StateTracker())
    bot.update_pane_aliases(["work:0.0"])
    bot.update_pane_aliases(["work:0.0", "new:0.0"])
    assert bot._pane_aliases["new:0.0"] == 2


def test_resolve_pane_numeric():
    from claude_monitor.state import StateTracker
    bot = TelegramBot("token", 123, "test", StateTracker())
    bot.update_pane_aliases(["work:0.0", "research:1.0"])
    assert bot._resolve_pane("1") == "work:0.0"
    assert bot._resolve_pane("2") == "research:1.0"
    assert bot._resolve_pane("99") is None


def test_resolve_pane_passthrough():
    from claude_monitor.state import StateTracker
    bot = TelegramBot("token", 123, "test", StateTracker())
    assert bot._resolve_pane("work:0.0") == "work:0.0"


def test_format_pane_label():
    from claude_monitor.state import StateTracker
    bot = TelegramBot("token", 123, "test", StateTracker())
    bot.update_pane_aliases(["work:0.0"])
    assert bot._format_pane_label("work:0.0") == "1: work:0.0"
    assert bot._format_pane_label("unknown:0.0") == "unknown:0.0"


# --- Inline button tests ---

def test_notification_permission_has_approve_deny_buttons():
    """PERMISSION notification includes Approve/Deny inline buttons."""
    from telegram import InlineKeyboardMarkup

    bot = TelegramBot("token", 123, "test-machine", StateTracker())
    bot._app = MagicMock()
    bot._app.bot.send_message = AsyncMock()

    transition = StateTransition(
        pane_id="work:0.0",
        old_state=PaneState.WORKING,
        new_state=PaneState.PERMISSION,
        content=PERMISSION_CONTENT,
    )

    import asyncio
    asyncio.get_event_loop().run_until_complete(bot.send_notification(transition))

    bot._app.bot.send_message.assert_called_once()
    call_kwargs = bot._app.bot.send_message.call_args
    reply_markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
    assert reply_markup is not None
    assert isinstance(reply_markup, InlineKeyboardMarkup)

    buttons = reply_markup.inline_keyboard[0]
    assert len(buttons) == 2
    assert "Approve" in buttons[0].text
    assert "Deny" in buttons[1].text
    assert buttons[0].callback_data == "approve:test-machine:work:0.0"
    assert buttons[1].callback_data == "deny:test-machine:work:0.0"


def test_notification_idle_has_view_button():
    """IDLE notification includes a View inline button."""
    from telegram import InlineKeyboardMarkup

    bot = TelegramBot("token", 123, "test-machine", StateTracker())
    bot._app = MagicMock()
    bot._app.bot.send_message = AsyncMock()

    transition = StateTransition(
        pane_id="work:0.0",
        old_state=PaneState.WORKING,
        new_state=PaneState.IDLE,
        content=IDLE_CONTENT,
    )

    import asyncio
    asyncio.get_event_loop().run_until_complete(bot.send_notification(transition))

    bot._app.bot.send_message.assert_called_once()
    call_kwargs = bot._app.bot.send_message.call_args
    reply_markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
    assert reply_markup is not None
    assert isinstance(reply_markup, InlineKeyboardMarkup)

    buttons = reply_markup.inline_keyboard[0]
    assert len(buttons) == 1
    assert "View" in buttons[0].text
    assert buttons[0].callback_data == "view:test-machine:work:0.0"


@pytest.mark.asyncio
async def test_callback_approve_sends_y_to_pane():
    """Approve button sends 'y' to the correct pane."""
    bot = TelegramBot("token", 123, "test-machine", StateTracker())

    query = AsyncMock()
    query.data = "approve:test-machine:work:0.0"
    query.message = AsyncMock()
    query.message.text = "Some notification text"
    query.message.text_html = "Some notification text"

    update = MagicMock()
    update.callback_query = query

    with patch("claude_monitor.telegram_bot.send_keys", return_value=True) as mock_send_keys:
        await bot._handle_button_press(update, MagicMock())

    mock_send_keys.assert_called_once_with("work:0.0", "y")
    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once()
    edit_text = query.edit_message_text.call_args.kwargs.get("text", query.edit_message_text.call_args[1].get("text", ""))
    assert "Approved" in edit_text


@pytest.mark.asyncio
async def test_callback_deny_sends_n_to_pane():
    """Deny button sends 'n' to the correct pane."""
    bot = TelegramBot("token", 123, "test-machine", StateTracker())

    query = AsyncMock()
    query.data = "deny:test-machine:work:0.0"
    query.message = AsyncMock()
    query.message.text = "Some notification text"
    query.message.text_html = "Some notification text"

    update = MagicMock()
    update.callback_query = query

    with patch("claude_monitor.telegram_bot.send_keys", return_value=True) as mock_send_keys:
        await bot._handle_button_press(update, MagicMock())

    mock_send_keys.assert_called_once_with("work:0.0", "n")
    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once()
    edit_text = query.edit_message_text.call_args.kwargs.get("text", query.edit_message_text.call_args[1].get("text", ""))
    assert "Denied" in edit_text


@pytest.mark.asyncio
async def test_callback_view_captures_pane():
    """View button captures pane content and sends it as reply."""
    bot = TelegramBot("token", 123, "test-machine", StateTracker())

    query = AsyncMock()
    query.data = "view:test-machine:work:0.0"
    query.message = AsyncMock()
    query.message.text = "Some notification text"

    update = MagicMock()
    update.callback_query = query

    with patch("claude_monitor.telegram_bot.capture_pane", return_value="pane content here") as mock_capture:
        await bot._handle_button_press(update, MagicMock())

    mock_capture.assert_called_once_with("work:0.0", context_lines=30)
    query.answer.assert_called_once()
    query.message.reply_text.assert_called_once()
    reply_text = query.message.reply_text.call_args[0][0]
    assert "pane content here" in reply_text


# --- Reply routing tests ---

def test_extract_pane_from_notification_with_alias():
    assert extract_pane_from_notification("Session: <code>1: work:0.0</code>") == "work:0.0"


def test_extract_pane_from_notification_without_alias():
    assert extract_pane_from_notification("Session: <code>work:0.0</code>") == "work:0.0"


def test_extract_pane_from_notification_no_match():
    assert extract_pane_from_notification("random text") is None


def test_extract_pane_from_notification_in_full_message():
    msg = '🟢 <b>[xin-4090] Claude Code finished task</b>\nSession: <code>2: copilot-api:1.0</code>\n\n<pre>some output</pre>'
    assert extract_pane_from_notification(msg) == "copilot-api:1.0"


# --- Smart Silence tests ---

def test_silence_suppresses_recent_interaction():
    import time
    bot = TelegramBot("token", 123, "test", StateTracker(), notification_silence_seconds=300)
    bot._last_interaction = time.time()
    assert bot._should_suppress_notification() is True


def test_silence_expires_after_threshold():
    import time
    bot = TelegramBot("token", 123, "test", StateTracker(), notification_silence_seconds=1)
    bot._last_interaction = time.time() - 2
    assert bot._should_suppress_notification() is False


def test_silence_disabled_when_zero():
    import time
    bot = TelegramBot("token", 123, "test", StateTracker(), notification_silence_seconds=0)
    bot._last_interaction = time.time()
    assert bot._should_suppress_notification() is False


def test_silence_no_interaction_yet():
    bot = TelegramBot("token", 123, "test", StateTracker(), notification_silence_seconds=300)
    assert bot._should_suppress_notification() is False


# --- Hook permission button tests ---

@pytest.mark.asyncio
async def test_send_hook_permission_sends_message_with_buttons():
    """send_hook_permission sends an Allow/Deny message."""
    from telegram import InlineKeyboardMarkup

    bot = TelegramBot("token", 123, "test-machine", StateTracker())
    bot._app = MagicMock()
    bot._app.bot.send_message = AsyncMock()

    await bot.send_hook_permission(
        request_id="abc123",
        tool_name="Bash",
        input_preview='{"command": "ls"}',
        project="my-project",
        pane_label="1: work:0.0",
    )

    bot._app.bot.send_message.assert_called_once()
    call_kwargs = bot._app.bot.send_message.call_args
    reply_markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
    assert isinstance(reply_markup, InlineKeyboardMarkup)
    buttons = reply_markup.inline_keyboard[0]
    assert len(buttons) == 2
    assert "Allow" in buttons[0].text
    assert "Deny" in buttons[1].text
    assert buttons[0].callback_data == "hook_approve:abc123"
    assert buttons[1].callback_data == "hook_deny:abc123"


@pytest.mark.asyncio
async def test_callback_hook_approve_resolves_permission():
    """hook_approve button resolves the permission request."""
    bot = TelegramBot("token", 123, "test-machine", StateTracker())
    mock_hook_server = MagicMock()
    bot._hook_server = mock_hook_server

    query = AsyncMock()
    query.data = "hook_approve:req123"
    query.message = AsyncMock()
    query.message.text = "Permission request text"

    update = MagicMock()
    update.callback_query = query

    await bot._handle_button_press(update, MagicMock())

    mock_hook_server.resolve_permission.assert_called_once_with("req123", allow=True)
    query.edit_message_text.assert_called_once()


@pytest.mark.asyncio
async def test_callback_hook_deny_resolves_permission():
    """hook_deny button resolves the permission request as denied."""
    bot = TelegramBot("token", 123, "test-machine", StateTracker())
    mock_hook_server = MagicMock()
    bot._hook_server = mock_hook_server

    query = AsyncMock()
    query.data = "hook_deny:req456"
    query.message = AsyncMock()
    query.message.text = "Permission request text"

    update = MagicMock()
    update.callback_query = query

    await bot._handle_button_press(update, MagicMock())

    mock_hook_server.resolve_permission.assert_called_once_with("req456", allow=False)
    query.edit_message_text.assert_called_once()
