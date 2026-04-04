import re
from dataclasses import dataclass, field
from enum import Enum


class PaneState(Enum):
    UNKNOWN = "unknown"
    WORKING = "working"
    IDLE = "idle"
    NEEDS_INPUT = "needs_input"
    PERMISSION = "permission"


@dataclass
class StateTransition:
    pane_id: str
    old_state: PaneState
    new_state: PaneState
    content: str


def detect_state(content: str) -> PaneState:
    """Detect Claude Code state from captured tmux pane content."""
    if not content.strip():
        return PaneState.UNKNOWN

    lines = content.strip().splitlines()
    last_lines = lines[-15:]  # Look at last 15 lines for patterns
    last_text = "\n".join(last_lines)

    # Check for permission prompts first (highest priority)
    permission_patterns = [
        r"Allow\?",
        r"Allow this command\?",
        r"Press Enter to approve",
        r"\(y/n\)",
        r"Allow .+\?",
    ]
    for pattern in permission_patterns:
        if re.search(pattern, last_text):
            return PaneState.PERMISSION

    # Check for active work indicators (spinners, tool execution)
    working_patterns = [
        r"^● \w+\(.*\)",          # Tool execution: ● Bash(...), ● Agent(...)
        r"^[✢✽] ",                # Active spinners: ✢ Verifying..., ✽ Building...
        r"Running \d+ agents",
        r"✻ Running scheduled task",  # Cron task active
        r"Will check again in",       # Monitoring pause between cron checks
    ]

    # Task panel indicators (only count as working if combined with spinner)
    task_panel_pattern = r"^[◻◼] "

    has_prompt = bool(re.search(r"^❯\s*$", last_text, re.MULTILINE))

    # Check for working indicators anywhere in the last lines
    has_working_indicator = False
    for pattern in working_patterns:
        if re.search(pattern, last_text, re.MULTILINE):
            has_working_indicator = True
            break

    # Also detect working by spinner with timing pattern: "... (5m 41s · ↓ 8.6k tokens)"
    if re.search(r"\(\d+[ms]\s+\d+s\s*·", last_text):
        has_working_indicator = True

    # If there's a spinner/working indicator AND prompt visible, check if the
    # spinner is ABOVE the prompt (Claude Code shows prompt at bottom while working)
    if has_working_indicator and has_prompt:
        # Find the prompt position
        prompt_idx = None
        for i in range(len(last_lines) - 1, -1, -1):
            if re.match(r"^❯\s*$", last_lines[i]):
                prompt_idx = i
                break

        if prompt_idx is not None:
            above_prompt = "\n".join(last_lines[:prompt_idx])
            # If there's a spinner or active task indicator above the prompt,
            # Claude Code is working (prompt visible but waiting for sub-agent)
            for pattern in working_patterns:
                if re.search(pattern, above_prompt, re.MULTILINE):
                    return PaneState.WORKING
            if re.search(r"\(\d+[ms]\s+\d+s\s*·", above_prompt):
                return PaneState.WORKING

    # No prompt visible + working indicator = definitely working
    if not has_prompt and has_working_indicator:
        return PaneState.WORKING

    # Check for prompt (❯) — indicates idle or needs_input
    if has_prompt:
        # Find content above the last ❯ prompt
        prompt_idx = None
        for i in range(len(last_lines) - 1, -1, -1):
            if re.match(r"^❯\s*$", last_lines[i]):
                prompt_idx = i
                break

        if prompt_idx is not None:
            above_prompt = "\n".join(last_lines[:prompt_idx])

            # Check for question-like content above prompt
            question_patterns = [
                r"\?\s*$",            # Line ending with ?
                r"Which .+ should",   # Choice question
                r"Does this .+ look",
                r"Should I",
                r"Do you want",
                r"checkpoint",
                r"Proceed\?",
            ]
            has_question = any(
                re.search(p, above_prompt, re.MULTILINE | re.IGNORECASE)
                for p in question_patterns
            )

            if has_question:
                return PaneState.NEEDS_INPUT

            return PaneState.IDLE

    return PaneState.UNKNOWN


@dataclass
class _PaneTracker:
    """Internal tracker for a single pane."""
    confirmed_state: PaneState = PaneState.UNKNOWN
    pending_state: PaneState = PaneState.UNKNOWN
    pending_count: int = 0
    notified: bool = False  # Have we already notified for the confirmed state?


class StateTracker:
    """Tracks state across multiple panes with debounce."""

    def __init__(self, stable_threshold: int = 2):
        self._threshold = stable_threshold
        self._panes: dict[str, _PaneTracker] = {}

    def _get_tracker(self, pane_id: str) -> _PaneTracker:
        if pane_id not in self._panes:
            self._panes[pane_id] = _PaneTracker()
        return self._panes[pane_id]

    def update(self, pane_id: str, content: str) -> "StateTransition | None":
        """Update pane state. Returns a StateTransition if a notification should fire."""
        detected = detect_state(content)
        tracker = self._get_tracker(pane_id)

        if detected == tracker.pending_state:
            tracker.pending_count += 1
        else:
            tracker.pending_state = detected
            tracker.pending_count = 1

        # State becomes confirmed after stable_threshold consecutive polls
        if tracker.pending_count >= self._threshold:
            if tracker.pending_state != tracker.confirmed_state:
                old_state = tracker.confirmed_state
                tracker.confirmed_state = tracker.pending_state
                tracker.notified = False

                # Only notify on meaningful transitions:
                # - Not from UNKNOWN (initial state)
                # - Only when new state needs user attention (idle, needs_input, permission)
                # - Skip transitions to working/unknown (not actionable)
                if (
                    old_state != PaneState.UNKNOWN
                    and tracker.confirmed_state in (
                        PaneState.IDLE,
                        PaneState.NEEDS_INPUT,
                        PaneState.PERMISSION,
                    )
                ):
                    tracker.notified = True
                    return StateTransition(
                        pane_id=pane_id,
                        old_state=old_state,
                        new_state=tracker.confirmed_state,
                        content=content,
                    )

        return None

    def get_state(self, pane_id: str) -> PaneState:
        if pane_id not in self._panes:
            return PaneState.UNKNOWN
        tracker = self._panes[pane_id]
        # Return pending state as a best-effort when no confirmed state yet
        if tracker.confirmed_state == PaneState.UNKNOWN and tracker.pending_state != PaneState.UNKNOWN:
            return tracker.pending_state
        return tracker.confirmed_state

    def get_all_states(self) -> dict[str, PaneState]:
        return {pid: t.confirmed_state for pid, t in self._panes.items()}

    def remove_pane(self, pane_id: str) -> None:
        self._panes.pop(pane_id, None)
