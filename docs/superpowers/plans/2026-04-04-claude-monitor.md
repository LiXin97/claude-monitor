# Claude Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python daemon that monitors Claude Code in tmux, sends Telegram notifications on state changes, and allows remote input via Telegram bot.

**Architecture:** One Python process per machine. A scraper captures tmux pane content every 5s. A state machine detects idle/working/needs_input/permission states via regex. A Telegram bot sends notifications on state transitions and accepts commands to view output and send input back to tmux panes.

**Tech Stack:** Python 3.10+, python-telegram-bot (async), PyYAML, click, subprocess (tmux)

**Spec:** `docs/superpowers/specs/2026-04-04-claude-monitor-design.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `pyproject.toml` | Package metadata, dependencies, entry point |
| `src/claude_monitor/__init__.py` | Package version |
| `src/claude_monitor/config.py` | Load & validate `~/.claude-monitor/config.yaml` |
| `src/claude_monitor/scraper.py` | Discover tmux panes running Claude Code, capture content |
| `src/claude_monitor/state.py` | State enum, detection rules, per-pane state machine with debounce |
| `src/claude_monitor/telegram_bot.py` | Telegram bot: notifications, commands (/status, /view, /send, /machines), quick reply |
| `src/claude_monitor/monitor.py` | Main async loop tying scraper → state → telegram |
| `src/claude_monitor/cli.py` | Click CLI: init, run, start, stop, install-service, status |
| `src/claude_monitor/service.py` | Systemd user service file generation & install |
| `tests/test_config.py` | Config loading tests |
| `tests/test_scraper.py` | Scraper tests (mocked subprocess) |
| `tests/test_state.py` | State detection & transition tests |
| `tests/test_telegram_bot.py` | Telegram bot command handler tests |
| `tests/test_monitor.py` | Integration test for monitor loop |

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/claude_monitor/__init__.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "claude-monitor"
version = "0.1.0"
description = "Monitor Claude Code in tmux, get Telegram notifications, send input remotely"
requires-python = ">=3.10"
dependencies = [
    "python-telegram-bot>=21.0",
    "pyyaml>=6.0",
    "click>=8.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]

[project.scripts]
claude-monitor = "claude_monitor.cli:main"
```

- [ ] **Step 2: Create package init**

```python
# src/claude_monitor/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 3: Install in dev mode**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && pip install -e '.[dev]'`
Expected: Successfully installed claude-monitor

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/claude_monitor/__init__.py
git commit -m "feat: project scaffolding with pyproject.toml"
```

---

### Task 2: Config Module

**Files:**
- Create: `src/claude_monitor/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for config loading**

```python
# tests/test_config.py
import os
import pytest
import yaml
from claude_monitor.config import load_config, ConfigError

VALID_CONFIG = {
    "telegram": {"bot_token": "123:ABC", "chat_id": 999},
    "machine": {"name": "test-box"},
    "monitor": {"poll_interval": 5, "stable_threshold": 2, "context_lines": 30},
}


def test_load_valid_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(VALID_CONFIG))
    cfg = load_config(str(config_path))
    assert cfg.telegram_bot_token == "123:ABC"
    assert cfg.telegram_chat_id == 999
    assert cfg.machine_name == "test-box"
    assert cfg.poll_interval == 5
    assert cfg.stable_threshold == 2
    assert cfg.context_lines == 30
    assert cfg.sessions == []


def test_load_config_with_defaults(tmp_path):
    minimal = {
        "telegram": {"bot_token": "123:ABC", "chat_id": 999},
        "machine": {"name": "box"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(minimal))
    cfg = load_config(str(config_path))
    assert cfg.poll_interval == 5
    assert cfg.stable_threshold == 2
    assert cfg.context_lines == 30


def test_load_config_missing_token(tmp_path):
    bad = {"telegram": {"chat_id": 999}, "machine": {"name": "box"}}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(bad))
    with pytest.raises(ConfigError, match="bot_token"):
        load_config(str(config_path))


def test_load_config_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/config.yaml")


def test_load_config_with_sessions(tmp_path):
    cfg_dict = {**VALID_CONFIG, "sessions": ["mysession:0.0"]}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(cfg_dict))
    cfg = load_config(str(config_path))
    assert cfg.sessions == ["mysession:0.0"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_config.py -v`
Expected: FAIL — cannot import `load_config`

- [ ] **Step 3: Implement config module**

```python
# src/claude_monitor/config.py
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


@dataclass
class Config:
    telegram_bot_token: str
    telegram_chat_id: int
    machine_name: str
    poll_interval: int = 5
    stable_threshold: int = 2
    context_lines: int = 30
    sessions: list[str] = field(default_factory=list)


DEFAULT_CONFIG_PATH = Path.home() / ".claude-monitor" / "config.yaml"


def load_config(path: str | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError("Config file must be a YAML mapping")

    telegram = raw.get("telegram", {})
    machine = raw.get("machine", {})
    monitor = raw.get("monitor", {})

    bot_token = telegram.get("bot_token")
    if not bot_token:
        raise ConfigError("telegram.bot_token is required")

    chat_id = telegram.get("chat_id")
    if chat_id is None:
        raise ConfigError("telegram.chat_id is required")

    machine_name = machine.get("name")
    if not machine_name:
        raise ConfigError("machine.name is required")

    return Config(
        telegram_bot_token=str(bot_token),
        telegram_chat_id=int(chat_id),
        machine_name=str(machine_name),
        poll_interval=int(monitor.get("poll_interval", 5)),
        stable_threshold=int(monitor.get("stable_threshold", 2)),
        context_lines=int(monitor.get("context_lines", 30)),
        sessions=raw.get("sessions", []),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_config.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_monitor/config.py tests/test_config.py
git commit -m "feat: config module with YAML loading and validation"
```

---

### Task 3: Scraper Module

**Files:**
- Create: `src/claude_monitor/scraper.py`
- Create: `tests/test_scraper.py`

- [ ] **Step 1: Write failing tests for scraper**

```python
# tests/test_scraper.py
from unittest.mock import patch, MagicMock
import subprocess
import pytest
from claude_monitor.scraper import TmuxPane, discover_panes, capture_pane


PANE_LIST_OUTPUT = (
    "copilot-api:1.0 claude 21873\n"
    "opd-idea:0.0 claude 3665260\n"
    "copilot-api:0.0 node 367127\n"
    "mywork:0.0 vim 12345\n"
)


def test_discover_panes_filters_claude():
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=PANE_LIST_OUTPUT, returncode=0
        )
        panes = discover_panes()
        assert len(panes) == 2
        assert panes[0].pane_id == "copilot-api:1.0"
        assert panes[0].command == "claude"
        assert panes[1].pane_id == "opd-idea:0.0"


def test_discover_panes_with_session_filter():
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=PANE_LIST_OUTPUT, returncode=0
        )
        panes = discover_panes(sessions=["opd-idea:0.0"])
        assert len(panes) == 1
        assert panes[0].pane_id == "opd-idea:0.0"


def test_discover_panes_no_tmux():
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("tmux not found")
        panes = discover_panes()
        assert panes == []


def test_capture_pane_returns_content():
    fake_output = "● Working on something\n❯ \n───\n"
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
        content = capture_pane("copilot-api:1.0", context_lines=30)
        assert "● Working" in content
        assert "❯" in content


def test_capture_pane_failure_returns_empty():
    with patch("claude_monitor.scraper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=1)
        content = capture_pane("bad:0.0", context_lines=30)
        assert content == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_scraper.py -v`
Expected: FAIL — cannot import `scraper`

- [ ] **Step 3: Implement scraper module**

```python
# src/claude_monitor/scraper.py
import subprocess
from dataclasses import dataclass


@dataclass
class TmuxPane:
    pane_id: str  # e.g. "copilot-api:1.0"
    command: str  # e.g. "claude"
    pid: int
    content: str = ""


def discover_panes(sessions: list[str] | None = None) -> list[TmuxPane]:
    """Discover tmux panes running Claude Code."""
    try:
        result = subprocess.run(
            [
                "tmux", "list-panes", "-a", "-F",
                "#{session_name}:#{window_index}.#{pane_index} #{pane_current_command} #{pane_pid}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    panes = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        pane_id, command, pid_str = parts[0], parts[1], parts[2]

        # Filter: only claude panes
        if command != "claude":
            continue

        # If session filter is set, only include matching panes
        if sessions and pane_id not in sessions:
            continue

        panes.append(TmuxPane(pane_id=pane_id, command=command, pid=int(pid_str)))

    return panes


def capture_pane(pane_id: str, context_lines: int = 30) -> str:
    """Capture the visible content of a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane_id, "-p", "-S", f"-{context_lines}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""

    if result.returncode != 0:
        return ""

    return result.stdout


def send_keys(pane_id: str, text: str) -> bool:
    """Send keystrokes to a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "send-keys", "-t", pane_id, text, "Enter"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_scraper.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_monitor/scraper.py tests/test_scraper.py
git commit -m "feat: tmux scraper with pane discovery and capture"
```

---

### Task 4: State Machine Module

**Files:**
- Create: `src/claude_monitor/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write failing tests for state detection**

```python
# tests/test_state.py
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
❯ \n\
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
❯ \n\
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""

PERMISSION_SCREEN = """\
● I need to run this command:

  npm install express

  Allow? (y/n)

──────────────────────────────────────────────────────
❯ \n\
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_state.py -v`
Expected: FAIL — cannot import `state`

- [ ] **Step 3: Implement state module**

```python
# src/claude_monitor/state.py
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

    # Check for active work indicators
    working_patterns = [
        r"^● \w",          # Tool execution: ● Bash(...), ● Agent(...)
        r"^✢ ",             # Spinner: ✢ Verifying...
        r"Running \d+ agents",
    ]
    # Only match working if there's no prompt below the working indicator
    has_prompt = bool(re.search(r"^❯\s*$", last_text, re.MULTILINE))

    if not has_prompt:
        for pattern in working_patterns:
            if re.search(pattern, last_text, re.MULTILINE):
                return PaneState.WORKING

    # Check for prompt (❯) — indicates idle or needs_input
    if has_prompt:
        # Look above the prompt for question indicators
        # Find content above the last ❯ prompt
        prompt_idx = None
        for i in range(len(last_lines) - 1, -1, -1):
            if re.match(r"^❯\s*$", last_lines[i]):
                prompt_idx = i
                break

        if prompt_idx is not None:
            above_prompt = "\n".join(last_lines[:prompt_idx])

            # Check for "Brewed for" / "Worked for" / "Crunched for"
            # immediately above the prompt with no question = idle
            has_completion = bool(
                re.search(r"✻ (Brewed|Worked|Crunched) for", above_prompt)
            )

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

    def update(self, pane_id: str, content: str) -> StateTransition | None:
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

                # Only notify on meaningful transitions (not from UNKNOWN)
                if old_state != PaneState.UNKNOWN:
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
        return self._panes[pane_id].confirmed_state

    def get_all_states(self) -> dict[str, PaneState]:
        return {pid: t.confirmed_state for pid, t in self._panes.items()}

    def remove_pane(self, pane_id: str) -> None:
        self._panes.pop(pane_id, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_state.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_monitor/state.py tests/test_state.py
git commit -m "feat: state machine with detection rules and debounce"
```

---

### Task 5: Telegram Bot Module

**Files:**
- Create: `src/claude_monitor/telegram_bot.py`
- Create: `tests/test_telegram_bot.py`

- [ ] **Step 1: Write failing tests for notification formatting**

```python
# tests/test_telegram_bot.py
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
❯ \n\
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on"""

NEEDS_INPUT_CONTENT = """\
● Which approach should I take?
  1. Refactor the existing code
  2. Write a new implementation

✻ Crunched for 2m 30s

──────────────────────────────────────────────────────
❯ \n\
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on"""

PERMISSION_CONTENT = """\
● Bash(npm install express)

  Allow? (y/n)

──────────────────────────────────────────────────────
❯ \n\
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_telegram_bot.py -v`
Expected: FAIL — cannot import `telegram_bot`

- [ ] **Step 3: Implement telegram bot module**

```python
# src/claude_monitor/telegram_bot.py
import logging
import re
from dataclasses import dataclass

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from claude_monitor.state import PaneState, StateTransition, StateTracker
from claude_monitor.scraper import capture_pane, send_keys

logger = logging.getLogger(__name__)


def extract_context_lines(content: str, max_lines: int = 10) -> list[str]:
    """Extract meaningful context lines from pane content, filtering noise."""
    lines = content.strip().splitlines()
    filtered = []
    for line in lines:
        stripped = line.strip()
        # Skip separator lines, status bars, empty lines
        if re.match(r"^─+$", stripped):
            continue
        if "bypass permissions" in stripped:
            continue
        if re.match(r"^⏵", stripped):
            continue
        if re.match(r"^❯\s*$", stripped):
            continue
        if stripped == "":
            continue
        filtered.append(line)
    # Return last max_lines meaningful lines
    return filtered[-max_lines:]


def format_notification(machine_name: str, transition: StateTransition) -> str:
    """Format a state transition into a Telegram notification message."""
    context = extract_context_lines(transition.content, max_lines=8)
    context_text = "\n".join(f"> {line}" for line in context)

    if transition.new_state == PaneState.IDLE:
        return (
            f"🟢 [{machine_name}] Claude Code finished task\n"
            f"Session: {transition.pane_id}\n"
            f"Last output:\n{context_text}"
        )
    elif transition.new_state == PaneState.NEEDS_INPUT:
        return (
            f"🟡 [{machine_name}] Claude Code waiting for input\n"
            f"Session: {transition.pane_id}\n"
            f"Context:\n{context_text}"
        )
    elif transition.new_state == PaneState.PERMISSION:
        return (
            f"🔴 [{machine_name}] Claude Code asking permission\n"
            f"Session: {transition.pane_id}\n"
            f"{context_text}"
        )
    else:
        return (
            f"ℹ️ [{machine_name}] State changed to {transition.new_state.value}\n"
            f"Session: {transition.pane_id}"
        )


def parse_send_command(args: str) -> tuple[str, str | None, str] | None:
    """Parse /send command arguments.

    Formats:
        "machine-name some text" -> (machine_name, None, text)
        "machine-name:session:win.pane some text" -> (machine_name, pane_id, text)
    """
    args = args.strip()
    if not args:
        return None

    parts = args.split(None, 1)
    if len(parts) < 2:
        return None

    target, text = parts

    # Check if target contains a pane specifier (machine:session:win.pane)
    # Machine names don't contain colons; pane IDs have format session:win.pane
    colon_idx = target.find(":")
    if colon_idx > 0:
        # Could be machine:session:win.pane
        machine = target[:colon_idx]
        pane_id = target[colon_idx + 1:]
        return (machine, pane_id, text)

    return (target, None, text)


class TelegramBot:
    """Telegram bot for notifications and remote control."""

    def __init__(
        self,
        bot_token: str,
        chat_id: int,
        machine_name: str,
        state_tracker: StateTracker,
    ):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._machine_name = machine_name
        self._state_tracker = state_tracker
        self._app: Application | None = None
        # Track which panes are awaiting input (for quick reply)
        self._waiting_panes: list[str] = []

    async def initialize(self) -> None:
        self._app = (
            Application.builder()
            .token(self._bot_token)
            .build()
        )
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(CommandHandler("view", self._handle_view))
        self._app.add_handler(CommandHandler("send", self._handle_send))
        self._app.add_handler(CommandHandler("machines", self._handle_machines))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_quick_reply)
        )
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

    async def shutdown(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    def _is_authorized(self, update: Update) -> bool:
        return (
            update.effective_chat is not None
            and update.effective_chat.id == self._chat_id
        )

    async def send_notification(self, transition: StateTransition) -> None:
        """Send a notification message for a state transition."""
        if not self._app:
            return
        msg = format_notification(self._machine_name, transition)
        await self._app.bot.send_message(chat_id=self._chat_id, text=msg)

        # Track waiting panes for quick reply
        if transition.new_state in (PaneState.NEEDS_INPUT, PaneState.PERMISSION):
            if transition.pane_id not in self._waiting_panes:
                self._waiting_panes.append(transition.pane_id)
        else:
            if transition.pane_id in self._waiting_panes:
                self._waiting_panes.remove(transition.pane_id)

    def update_waiting_panes(self, pane_states: dict[str, PaneState]) -> None:
        """Update the list of panes waiting for input."""
        self._waiting_panes = [
            pid for pid, state in pane_states.items()
            if state in (PaneState.NEEDS_INPUT, PaneState.PERMISSION)
        ]

    async def _handle_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        args = context.args or []
        # /status without args: show this machine's status
        if not args or args[0] == self._machine_name:
            states = self._state_tracker.get_all_states()
            if not states:
                await update.message.reply_text(
                    f"[{self._machine_name}] No Claude Code sessions found."
                )
                return

            lines = [f"📊 [{self._machine_name}] Status:"]
            state_icons = {
                PaneState.WORKING: "🔵",
                PaneState.IDLE: "🟢",
                PaneState.NEEDS_INPUT: "🟡",
                PaneState.PERMISSION: "🔴",
                PaneState.UNKNOWN: "⚪",
            }
            for pane_id, state in states.items():
                icon = state_icons.get(state, "⚪")
                lines.append(f"  {icon} {pane_id}: {state.value}")
            await update.message.reply_text("\n".join(lines))

    async def _handle_view(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /view <machine> [pane]")
            return

        target_machine = args[0]
        if target_machine != self._machine_name:
            return  # Not for this machine

        # Determine which pane to view
        states = self._state_tracker.get_all_states()
        if not states:
            await update.message.reply_text(
                f"[{self._machine_name}] No active sessions."
            )
            return

        pane_id = args[1] if len(args) > 1 else next(iter(states))
        content = capture_pane(pane_id, context_lines=30)
        if not content:
            await update.message.reply_text(f"Could not capture pane {pane_id}")
            return

        # Truncate for Telegram message limit (4096 chars)
        if len(content) > 3900:
            content = content[-3900:]
        await update.message.reply_text(
            f"📺 [{self._machine_name}] {pane_id}:\n```\n{content}\n```",
            parse_mode="Markdown",
        )

    async def _handle_send(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        raw_args = " ".join(context.args) if context.args else ""
        parsed = parse_send_command(raw_args)
        if parsed is None:
            await update.message.reply_text(
                "Usage: /send <machine> <text>\n"
                "       /send <machine>:<pane> <text>"
            )
            return

        machine, pane_id, text = parsed
        if machine != self._machine_name:
            return  # Not for this machine

        # If no pane specified, use the first waiting pane or first known pane
        if pane_id is None:
            if self._waiting_panes:
                pane_id = self._waiting_panes[0]
            else:
                states = self._state_tracker.get_all_states()
                if states:
                    pane_id = next(iter(states))
                else:
                    await update.message.reply_text("No active sessions found.")
                    return

        ok = send_keys(pane_id, text)
        if ok:
            await update.message.reply_text(f"✅ Sent to {pane_id}")
        else:
            await update.message.reply_text(f"❌ Failed to send to {pane_id}")

    async def _handle_machines(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        # This machine only knows about itself
        states = self._state_tracker.get_all_states()
        pane_count = len(states)
        active = sum(1 for s in states.values() if s == PaneState.WORKING)
        await update.message.reply_text(
            f"🖥 {self._machine_name}: {pane_count} pane(s), {active} working"
        )

    async def _handle_quick_reply(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Forward plain text to the sole waiting pane (quick reply shortcut)."""
        if not self._is_authorized(update):
            return

        if len(self._waiting_panes) != 1:
            if len(self._waiting_panes) == 0:
                await update.message.reply_text(
                    "No pane is waiting for input. Use /send <machine> <text>."
                )
            else:
                pane_list = ", ".join(self._waiting_panes)
                await update.message.reply_text(
                    f"Multiple panes waiting: {pane_list}\n"
                    "Use /send <machine>:<pane> <text> to specify."
                )
            return

        pane_id = self._waiting_panes[0]
        text = update.message.text
        ok = send_keys(pane_id, text)
        if ok:
            await update.message.reply_text(f"✅ → {pane_id}")
        else:
            await update.message.reply_text(f"❌ Failed to send to {pane_id}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_telegram_bot.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_monitor/telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: telegram bot with notifications, commands, and quick reply"
```

---

### Task 6: Monitor Loop

**Files:**
- Create: `src/claude_monitor/monitor.py`
- Create: `tests/test_monitor.py`

- [ ] **Step 1: Write failing test for monitor loop**

```python
# tests/test_monitor.py
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
        patch("claude_monitor.monitor.capture_pane", return_value="● working"),
    ):
        monitor = Monitor(config)
        monitor._telegram = AsyncMock()
        monitor._telegram.send_notification = AsyncMock()
        monitor._telegram.update_waiting_panes = MagicMock()

        # Pane exists
        mock_discover.return_value = [pane]
        await monitor._poll_once()
        assert monitor._state_tracker.get_state("work:0.0") != PaneState.UNKNOWN

        # Pane removed
        mock_discover.return_value = []
        await monitor._poll_once()
        assert monitor._state_tracker.get_state("work:0.0") == PaneState.UNKNOWN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_monitor.py -v`
Expected: FAIL — cannot import `monitor`

- [ ] **Step 3: Implement monitor module**

```python
# src/claude_monitor/monitor.py
import asyncio
import logging
import signal

from claude_monitor.config import Config
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
        )
        self._running = False
        self._known_panes: set[str] = set()

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

        # Update waiting panes list for quick reply
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

        # Send startup message
        try:
            await self._telegram._app.bot.send_message(
                chat_id=self._config.telegram_chat_id,
                text=f"🚀 [{self._config.machine_name}] Claude Monitor started",
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
                await self._telegram._app.bot.send_message(
                    chat_id=self._config.telegram_chat_id,
                    text=f"🛑 [{self._config.machine_name}] Claude Monitor stopped",
                )
            except Exception:
                pass
            await self._telegram.shutdown()

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_monitor.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_monitor/monitor.py tests/test_monitor.py
git commit -m "feat: monitor loop with scraper-state-telegram pipeline"
```

---

### Task 7: CLI Module

**Files:**
- Create: `src/claude_monitor/cli.py`

- [ ] **Step 1: Implement CLI**

```python
# src/claude_monitor/cli.py
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import click
import yaml

from claude_monitor.config import load_config, DEFAULT_CONFIG_PATH, ConfigError
from claude_monitor.monitor import Monitor

logger = logging.getLogger(__name__)


@click.group()
def main():
    """Claude Monitor — Monitor Claude Code in tmux via Telegram."""
    pass


@main.command()
def init():
    """Interactive setup: create config file and guide Telegram bot creation."""
    config_dir = DEFAULT_CONFIG_PATH.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    if DEFAULT_CONFIG_PATH.exists():
        click.echo(f"Config already exists at {DEFAULT_CONFIG_PATH}")
        if not click.confirm("Overwrite?"):
            return

    click.echo("\n=== Claude Monitor Setup ===\n")
    click.echo("Step 1: Create a Telegram bot")
    click.echo("  1. Open Telegram and search for @BotFather")
    click.echo("  2. Send /newbot and follow the prompts")
    click.echo("  3. Copy the bot token\n")

    bot_token = click.prompt("Telegram bot token")

    click.echo("\nStep 2: Get your Telegram chat ID")
    click.echo("  1. Send any message to your new bot")
    click.echo("  2. Open: https://api.telegram.org/bot<TOKEN>/getUpdates")
    click.echo("  3. Find your chat ID in the response\n")

    chat_id = click.prompt("Your Telegram chat ID", type=int)

    import socket
    default_name = socket.gethostname()
    machine_name = click.prompt("Machine name", default=default_name)

    config = {
        "telegram": {"bot_token": bot_token, "chat_id": chat_id},
        "machine": {"name": machine_name},
        "monitor": {"poll_interval": 5, "stable_threshold": 2, "context_lines": 30},
        "sessions": [],
    }

    with open(DEFAULT_CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    os.chmod(DEFAULT_CONFIG_PATH, 0o600)

    click.echo(f"\n✅ Config saved to {DEFAULT_CONFIG_PATH}")
    click.echo(f"   Permissions set to 600 (owner-only)")
    click.echo(f"\nRun 'claude-monitor run' to start monitoring.")


@main.command()
@click.option("--config", "-c", "config_path", default=None, help="Config file path")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def run(config_path, verbose):
    """Run the monitor in foreground."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    monitor = Monitor(cfg)

    def handle_signal(sig, frame):
        click.echo("\nShutting down...")
        monitor.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(monitor.run())


@main.command()
@click.option("--config", "-c", "config_path", default=None, help="Config file path")
def status(config_path):
    """Show local monitoring status."""
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    from claude_monitor.scraper import discover_panes

    sessions = cfg.sessions or None
    panes = discover_panes(sessions=sessions)

    if not panes:
        click.echo(f"[{cfg.machine_name}] No Claude Code sessions found in tmux.")
        return

    click.echo(f"[{cfg.machine_name}] Found {len(panes)} Claude Code pane(s):")
    for pane in panes:
        click.echo(f"  • {pane.pane_id} (pid: {pane.pid})")
```

- [ ] **Step 2: Verify CLI entry point works**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && pip install -e '.[dev]' && claude-monitor --help`
Expected: Shows help text with `init`, `run`, `status` commands

- [ ] **Step 3: Verify status command against real tmux**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && claude-monitor status -c /dev/null 2>&1 || true`
Expected: Shows error about missing config (confirming the CLI routes correctly)

- [ ] **Step 4: Commit**

```bash
git add src/claude_monitor/cli.py
git commit -m "feat: CLI with init, run, and status commands"
```

---

### Task 8: Systemd Service Helper

**Files:**
- Create: `src/claude_monitor/service.py`

- [ ] **Step 1: Implement service module**

```python
# src/claude_monitor/service.py
import os
import sys
import subprocess
from pathlib import Path

UNIT_TEMPLATE = """\
[Unit]
Description=Claude Monitor — tmux Claude Code monitor with Telegram
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_path} run{config_flag}
Restart=on-failure
RestartSec=10
Environment=PATH={path_env}

[Install]
WantedBy=default.target
"""

SERVICE_NAME = "claude-monitor.service"


def generate_service_file(config_path: str | None = None) -> str:
    """Generate a systemd user service unit file content."""
    exec_path = _find_executable()
    config_flag = f" -c {config_path}" if config_path else ""
    path_env = os.environ.get("PATH", "/usr/bin:/usr/local/bin")

    return UNIT_TEMPLATE.format(
        exec_path=exec_path,
        config_flag=config_flag,
        path_env=path_env,
    )


def install_service(config_path: str | None = None) -> Path:
    """Install the systemd user service and enable it."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)

    unit_path = unit_dir / SERVICE_NAME
    unit_path.write_text(generate_service_file(config_path))

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", SERVICE_NAME], check=True)
    subprocess.run(["systemctl", "--user", "start", SERVICE_NAME], check=True)

    return unit_path


def _find_executable() -> str:
    """Find the claude-monitor executable path."""
    # Check if running from an installed script
    import shutil
    path = shutil.which("claude-monitor")
    if path:
        return path
    # Fallback: use python -m
    return f"{sys.executable} -m claude_monitor.cli"
```

- [ ] **Step 2: Add install-service and stop commands to CLI**

Append to `src/claude_monitor/cli.py`:

```python
@main.command("install-service")
@click.option("--config", "-c", "config_path", default=None, help="Config file path")
def install_service_cmd(config_path):
    """Install and enable systemd user service."""
    from claude_monitor.service import install_service

    try:
        unit_path = install_service(config_path)
        click.echo(f"✅ Service installed and started: {unit_path}")
        click.echo(f"   Check status: systemctl --user status claude-monitor")
        click.echo(f"   View logs: journalctl --user -u claude-monitor -f")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
def stop():
    """Stop the systemd service."""
    import subprocess as sp

    try:
        sp.run(["systemctl", "--user", "stop", "claude-monitor"], check=True)
        click.echo("✅ Claude Monitor stopped.")
    except sp.CalledProcessError:
        click.echo("Service is not running or not installed.", err=True)
```

- [ ] **Step 3: Verify CLI shows all commands**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && pip install -e . && claude-monitor --help`
Expected: Shows `init`, `run`, `status`, `stop`, `install-service` commands

- [ ] **Step 4: Commit**

```bash
git add src/claude_monitor/service.py src/claude_monitor/cli.py
git commit -m "feat: systemd service helper and CLI stop/install-service commands"
```

---

### Task 9: Integration Test — End-to-End Smoke Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
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
❯ \n\
──────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""

NEEDS_INPUT = """\
● Should I proceed with approach A or B?

✻ Crunched for 1m 5s

──────────────────────────────────────────────────────
❯ \n\
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

        for _ in range(6):
            await monitor._poll_once()

    assert len(notifications) == 2
    assert notifications[0].old_state == PaneState.WORKING
    assert notifications[0].new_state == PaneState.IDLE
    assert notifications[1].old_state == PaneState.IDLE
    assert notifications[1].new_state == PaneState.NEEDS_INPUT
```

- [ ] **Step 2: Run the integration test**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run all tests**

Run: `cd /home/xin/Projects/MSRA/claude-monitor && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: end-to-end integration test for monitor lifecycle"
```

---

### Task 10: Manual Smoke Test with Real Tmux

- [ ] **Step 1: Create a test config file**

Create `~/.claude-monitor/config.yaml` with your real Telegram bot token and chat ID (use `claude-monitor init` or create manually).

- [ ] **Step 2: Run in foreground with verbose logging**

Run: `claude-monitor run -v`

Expected:
- Startup message appears in Telegram: "🚀 [machine-name] Claude Monitor started"
- Log output shows discovered Claude Code panes
- When a Claude Code session transitions from working to idle, you receive a Telegram notification

- [ ] **Step 3: Test Telegram commands**

In Telegram, send:
- `/status` — should show your machine's pane states
- `/view <machine-name>` — should show last 30 lines of a pane
- `/send <machine-name> test message` — should send "test message" to the pane

- [ ] **Step 4: Test quick reply**

Wait for a Claude Code session to be in `needs_input` state, then type a plain text message in Telegram. It should be forwarded to the waiting pane.

- [ ] **Step 5: Stop and verify**

Press Ctrl+C. Verify Telegram receives: "🛑 [machine-name] Claude Monitor stopped"
