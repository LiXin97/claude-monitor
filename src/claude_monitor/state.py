import re
from dataclasses import dataclass, field
from enum import Enum


class PaneState(Enum):
    UNKNOWN = "unknown"
    WORKING = "working"
    IDLE = "idle"
    NEEDS_INPUT = "needs_input"
    PERMISSION = "permission"


ACTIONABLE_STATES = (PaneState.IDLE, PaneState.NEEDS_INPUT, PaneState.PERMISSION)


@dataclass
class StateTransition:
    pane_id: str
    old_state: PaneState
    new_state: PaneState
    content: str


# Pre-compiled regex patterns for state detection
_PERMISSION_PATTERNS = [
    re.compile(r"Allow\?"),
    re.compile(r"Allow this command\?"),
    re.compile(r"Press Enter to approve"),
    re.compile(r"\(y/n\)"),
    re.compile(r"Allow .+\?"),
]

_WORKING_PATTERNS = [
    re.compile(r"^● \w+\(.*\)", re.MULTILINE),
    re.compile(r"^[✢✽·] ", re.MULTILINE),  # · is tmux rendering of ✢
    re.compile(r"Running \d+ agents"),
    re.compile(r"✻ Running scheduled task"),
    re.compile(r"Will check again in"),
    re.compile(r"Running…"),
    re.compile(r"↓ [\d.]+k tokens"),
    re.compile(r"\d+ tasks? \(.*\d+ in progress"),  # task list with in-progress
    re.compile(r"^\s*◼ ", re.MULTILINE),  # in-progress task indicator
]

_TIMING_PATTERN = re.compile(r"\(\d+[hms]\s+\d+[hms].*?·")

_PROMPT_PATTERN = re.compile(r"^❯\s*$", re.MULTILINE)

_QUESTION_PATTERNS = [
    re.compile(r"\?\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"Which .+ should", re.MULTILINE | re.IGNORECASE),
    re.compile(r"Does this .+ look", re.MULTILINE | re.IGNORECASE),
    re.compile(r"Should I", re.MULTILINE | re.IGNORECASE),
    re.compile(r"Do you want", re.MULTILINE | re.IGNORECASE),
    re.compile(r"checkpoint", re.MULTILINE | re.IGNORECASE),
    re.compile(r"Proceed\?", re.MULTILINE | re.IGNORECASE),
]


def _has_working_indicator(text: str) -> bool:
    """Check if text contains any working indicator pattern."""
    for pattern in _WORKING_PATTERNS:
        if pattern.search(text):
            return True
    return bool(_TIMING_PATTERN.search(text))


def _find_prompt_idx(lines: list[str]) -> int | None:
    """Find the index of the last ❯ prompt line."""
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r"^❯\s*$", lines[i]):
            return i
    return None


def detect_state(content: str) -> PaneState:
    """Detect Claude Code state from captured tmux pane content."""
    if not content.strip():
        return PaneState.UNKNOWN

    lines = content.strip().splitlines()
    last_lines = lines[-15:]
    last_text = "\n".join(last_lines)

    # Permission prompts (highest priority)
    for pattern in _PERMISSION_PATTERNS:
        if pattern.search(last_text):
            return PaneState.PERMISSION

    has_prompt = bool(_PROMPT_PATTERN.search(last_text))
    has_working = _has_working_indicator(last_text)

    # Working indicator + prompt: check if indicator is ABOVE the prompt
    if has_working and has_prompt:
        prompt_idx = _find_prompt_idx(last_lines)
        if prompt_idx is not None:
            above_prompt = "\n".join(last_lines[:prompt_idx])
            if _has_working_indicator(above_prompt):
                return PaneState.WORKING

    # No prompt + working indicator = definitely working
    if not has_prompt and has_working:
        return PaneState.WORKING

    # Prompt visible — idle or needs_input
    if has_prompt:
        prompt_idx = _find_prompt_idx(last_lines)
        if prompt_idx is not None:
            above_prompt = "\n".join(last_lines[:prompt_idx])
            if any(p.search(above_prompt) for p in _QUESTION_PATTERNS):
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
