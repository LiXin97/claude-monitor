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
