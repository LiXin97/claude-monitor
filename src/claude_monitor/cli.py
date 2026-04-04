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
