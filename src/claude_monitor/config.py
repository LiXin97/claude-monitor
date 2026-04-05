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
    notification_silence_seconds: int = 0
    hooks_enabled: bool = False
    hook_server_port: int = 9876


DEFAULT_CONFIG_PATH = Path.home() / ".claude-monitor" / "config.yaml"


def _parse_bool(value) -> bool:
    """Parse a boolean value from YAML, handling string representations."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def load_config(path: str | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {config_path}: {e}")

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

    try:
        return Config(
            telegram_bot_token=str(bot_token),
            telegram_chat_id=int(chat_id),
            machine_name=str(machine_name),
            poll_interval=int(monitor.get("poll_interval", 5)),
            stable_threshold=int(monitor.get("stable_threshold", 2)),
            context_lines=int(monitor.get("context_lines", 30)),
            sessions=raw.get("sessions", []),
            notification_silence_seconds=int(monitor.get("notification_silence_seconds", 0)),
            hooks_enabled=_parse_bool(monitor.get("hooks_enabled", False)),
            hook_server_port=int(monitor.get("hook_server_port", 9876)),
        )
    except (ValueError, TypeError) as e:
        raise ConfigError(f"Invalid config value: {e}")
