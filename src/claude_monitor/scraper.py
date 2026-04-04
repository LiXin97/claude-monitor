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
