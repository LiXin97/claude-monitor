import pytest
from claude_monitor.telegram_bot import (
    format_notification,
    parse_send_command,
    extract_context_lines,
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
