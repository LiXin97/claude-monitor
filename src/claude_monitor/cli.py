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
    # Suppress noisy third-party loggers even in verbose mode
    for noisy in ("httpx", "httpcore", "telegram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

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
    for i, pane in enumerate(panes, 1):
        click.echo(f"  {i}: {pane.pane_id} (pid: {pane.pid})")


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


@main.command("install-hooks")
@click.option("--port", default=9876, help="Hook server port")
@click.option("--config", "-c", "config_path", default=None, help="Config file path")
def install_hooks(port, config_path):
    """Configure Claude Code hooks to send events to claude-monitor."""
    import json

    settings_path = Path.home() / ".claude" / "settings.json"

    # Load existing settings or start fresh
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    base_url = f"http://localhost:{port}"
    hooks = settings.get("hooks", {})
    # Claude Code passes hook event JSON via stdin to command hooks
    hooks["Stop"] = [{"hooks": [{"type": "command", "command": f"curl -s -X POST {base_url}/hook/stop -H 'Content-Type: application/json' -d @-"}]}]
    hooks["Notification"] = [{"hooks": [{"type": "command", "command": f"curl -s -X POST {base_url}/hook/notification -H 'Content-Type: application/json' -d @-"}]}]
    # PreToolUse hook is NOT installed by default — it blocks Claude until
    # the user approves via Telegram, which is disruptive for most workflows.
    # To enable it, manually add a PreToolUse entry in settings.json.
    settings["hooks"] = hooks

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    click.echo(f"✅ Hooks configured in {settings_path}")
    click.echo(f"   Hook server URL: {base_url}")
    click.echo(f"\n   Make sure to enable hooks in your config:")
    click.echo(f"   monitor:")
    click.echo(f"     hooks_enabled: true")
    click.echo(f"     hook_server_port: {port}")

    # Also enable hooks in monitor config if possible
    try:
        cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        if cfg_path.exists():
            with open(cfg_path) as f:
                config = yaml.safe_load(f) or {}
            monitor = config.setdefault("monitor", {})
            monitor["hooks_enabled"] = True
            monitor["hook_server_port"] = port
            with open(cfg_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False)
            click.echo(f"\n   ✅ Also updated {cfg_path}")
    except Exception as e:
        click.echo(f"\n   ⚠️  Could not update monitor config: {e}")
