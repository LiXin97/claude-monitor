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

# Working state with prompt visible (sub-agents running, prompt at bottom)
WORKING_WITH_PROMPT = """\
● Now launch two parallel sub-agents: one for API layer, one for Data layer.

✽ Building API and data foundation… (12m 53s · ↓ 26.5k tokens)
  ⎿  ◻ Phase 6: Debate + Block 0.2 + analysis › blocked by #18
     ◻ Phase 4: Block 0.1 experiment script › blocked by #16
     ◼ Phase 1+2: Build API layer + Data layer foundation
     ◻ Phase 5: Pilot test (10 questions) › blocked by #17
     ◻ Phase 3: Alpha measurement core › blocked by #17

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""

# Scheduled task monitoring pause (cron fires periodically, idle between checks)
SCHEDULED_TASK_PAUSE = """\
● Still running. Step 4: Utility Probe — 474/1200 rollouts (40%), ~48 min remaining. Will check again in 3 minutes.

  7 tasks (3 done, 1 in progress, 3 open)
  ✔ Set up TCAD project structure and conda environment
  ◻ Paper writing pipeline for NeurIPS submission › blocked by #4
  ◻ Run Block 2: TCAD training + baselines (Gate G2) › blocked by #5
  ◼ Run Block 1: Warm-start + Utility Probe (Gate G1)
  ✔ Implement TCAD core pipeline
  ✔ Download missing models (14B teacher, datasets)
  ◻ Run ablations + analysis + multi-seed significance › blocked by #4

──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""


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


def test_detect_working_with_prompt_visible():
    """Claude Code shows prompt at bottom while sub-agents are running."""
    assert detect_state(WORKING_WITH_PROMPT) == PaneState.WORKING


def test_detect_working_scheduled_task_pause():
    """Scheduled task monitoring: idle prompt but 'Will check again' visible."""
    assert detect_state(SCHEDULED_TASK_PAUSE) == PaneState.WORKING


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
