import pytest
from claude_monitor.telegram_bot import (
    format_notification,
    parse_send_command,
    extract_context_lines,
    TelegramBot,
)
from claude_monitor.state import PaneState, StateTransition


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
