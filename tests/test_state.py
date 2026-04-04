import pytest
from claude_monitor.state import (
    PaneState,
    detect_state,
    StateTracker,
    StateTransition,
)

# --- Fixtures: real terminal captures from Claude Code ---

IDLE_SCREEN = """\
● 完成。重启服务后，所有错误会追加写入 error.log

✻ Brewed for 40s

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""

WORKING_SCREEN = """\
● Agent(Quick novelty check 8 ideas)
  ⎿  Done (54 tool uses · 47.0k tokens · 6m 44s)

● Now let me run a quick check on the most promising ideas.

● Agent(Deep validation of ABC)
  ⎿  Web Search: arXiv debate collaboration...

✢ Verifying novelty… (5m 41s · ↓ 8.6k tokens)"""

NEEDS_INPUT_SCREEN = """\
● Which ideas should I validate further? My recommendation: ABC has
  the best novelty-to-risk ratio.

✻ Crunched for 10m 16s

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""

PERMISSION_SCREEN = """\
● I need to run this command:

  npm install express

  Allow? (y/n)

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""

# Another permission pattern: tool approval
PERMISSION_SCREEN_2 = """\
● Bash(rm -rf /tmp/old-cache)

  Allow this command? Press Enter to approve, Esc to deny."""


def test_detect_idle():
    assert detect_state(IDLE_SCREEN) == PaneState.IDLE


def test_detect_working():
    assert detect_state(WORKING_SCREEN) == PaneState.WORKING


def test_detect_needs_input():
    assert detect_state(NEEDS_INPUT_SCREEN) == PaneState.NEEDS_INPUT


def test_detect_permission():
    assert detect_state(PERMISSION_SCREEN) == PaneState.PERMISSION


def test_detect_permission_2():
    assert detect_state(PERMISSION_SCREEN_2) == PaneState.PERMISSION


def test_detect_empty_content():
    assert detect_state("") == PaneState.UNKNOWN


# --- StateTracker tests ---


def test_tracker_no_notification_on_first_poll():
    tracker = StateTracker(stable_threshold=2)
    transition = tracker.update("pane1", WORKING_SCREEN)
    assert transition is None


def test_tracker_notification_after_stable_threshold():
    tracker = StateTracker(stable_threshold=2)
    tracker.update("pane1", WORKING_SCREEN)
    tracker.update("pane1", WORKING_SCREEN)
    # Now transition to idle
    tracker.update("pane1", IDLE_SCREEN)
    transition = tracker.update("pane1", IDLE_SCREEN)
    assert transition is not None
    assert transition.old_state == PaneState.WORKING
    assert transition.new_state == PaneState.IDLE


def test_tracker_no_repeat_notification():
    tracker = StateTracker(stable_threshold=2)
    # Become stable in working
    tracker.update("pane1", WORKING_SCREEN)
    tracker.update("pane1", WORKING_SCREEN)
    # Transition to idle (2 polls)
    tracker.update("pane1", IDLE_SCREEN)
    t1 = tracker.update("pane1", IDLE_SCREEN)
    assert t1 is not None
    # Stay idle — no more notifications
    t2 = tracker.update("pane1", IDLE_SCREEN)
    assert t2 is None


def test_tracker_flapping_no_notification():
    """State flaps between working and idle — should not notify."""
    tracker = StateTracker(stable_threshold=2)
    tracker.update("pane1", WORKING_SCREEN)
    tracker.update("pane1", WORKING_SCREEN)
    # Flap: idle then back to working before stable_threshold
    tracker.update("pane1", IDLE_SCREEN)
    transition = tracker.update("pane1", WORKING_SCREEN)
    assert transition is None


def test_tracker_independent_panes():
    tracker = StateTracker(stable_threshold=2)
    tracker.update("pane1", WORKING_SCREEN)
    tracker.update("pane1", WORKING_SCREEN)
    tracker.update("pane2", IDLE_SCREEN)
    tracker.update("pane2", IDLE_SCREEN)
    # Transition pane1 to idle
    tracker.update("pane1", IDLE_SCREEN)
    t = tracker.update("pane1", IDLE_SCREEN)
    assert t is not None
    assert t.old_state == PaneState.WORKING


def test_tracker_get_state():
    tracker = StateTracker(stable_threshold=2)
    assert tracker.get_state("pane1") == PaneState.UNKNOWN
    tracker.update("pane1", WORKING_SCREEN)
    tracker.update("pane1", WORKING_SCREEN)
    assert tracker.get_state("pane1") == PaneState.WORKING


def test_tracker_remove_pane():
    tracker = StateTracker(stable_threshold=2)
    tracker.update("pane1", WORKING_SCREEN)
    tracker.remove_pane("pane1")
    assert tracker.get_state("pane1") == PaneState.UNKNOWN
