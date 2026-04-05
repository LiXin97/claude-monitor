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


def test_load_config_with_silence():
    from claude_monitor.config import Config
    config = Config(
        telegram_bot_token="token",
        telegram_chat_id=123,
        machine_name="test",
        notification_silence_seconds=600,
    )
    assert config.notification_silence_seconds == 600


def test_load_config_machine_index(tmp_path):
    cfg_dict = {
        "telegram": {"bot_token": "123:ABC", "chat_id": 999},
        "machine": {"name": "box", "index": 2},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(cfg_dict))
    cfg = load_config(str(config_path))
    assert cfg.machine_index == 2


def test_load_config_machine_index_default(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(VALID_CONFIG))
    cfg = load_config(str(config_path))
    assert cfg.machine_index == 0
