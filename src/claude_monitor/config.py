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
    notification_silence_seconds: int = 300


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
        notification_silence_seconds=int(monitor.get("notification_silence_seconds", 300)),
    )
