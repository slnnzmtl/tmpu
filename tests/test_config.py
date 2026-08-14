"""Tests for environment variable loading in src.config."""

from pathlib import Path

import pytest

from src.config import Config, load_config


@pytest.fixture
def isolated_env(monkeypatch):
    """Ensure tests do not inherit real environment credentials."""
    for key in ("API_ID", "API_HASH", "PHONE_NUMBER"):
        monkeypatch.delenv(key, raising=False)


def write_env_file(path: Path, lines: dict[str, str]) -> Path:
    env_path = path / ".env"
    env_path.write_text("\n".join(f"{key}={value}" for key, value in lines.items()) + "\n")
    return env_path


def test_load_config_reads_required_env_vars(tmp_path, isolated_env):
    """Given a complete .env file, load_config returns a Config with parsed values."""
    env_file = write_env_file(
        tmp_path,
        {
            "API_ID": "12345678",
            "API_HASH": "abc123hash",
            "PHONE_NUMBER": "+1234567890",
        },
    )

    config = load_config(env_file)

    assert isinstance(config, Config)
    assert config.api_id == 12345678
    assert config.api_hash == "abc123hash"
    assert config.phone_number == "+1234567890"
    assert config.session_name == "tmpu"


def test_load_config_missing_api_id_raises(tmp_path, isolated_env):
    """Given .env without API_ID, load_config fails fast with a clear error."""
    env_file = write_env_file(
        tmp_path,
        {
            "API_HASH": "abc123hash",
            "PHONE_NUMBER": "+1234567890",
        },
    )

    with pytest.raises(ValueError, match="API_ID"):
        load_config(env_file)


def test_load_config_missing_api_hash_raises(tmp_path, isolated_env):
    """Given .env without API_HASH, load_config fails fast with a clear error."""
    env_file = write_env_file(
        tmp_path,
        {
            "API_ID": "12345678",
            "PHONE_NUMBER": "+1234567890",
        },
    )

    with pytest.raises(ValueError, match="API_HASH"):
        load_config(env_file)
