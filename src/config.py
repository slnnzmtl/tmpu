from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass
class Config:
    api_id: int
    api_hash: str
    phone_number: str
    session_name: str


def _require(values: dict, key: str) -> str:
    value = values.get(key)
    if not value:
        raise ValueError(f"{key} is required")
    return value


def load_config(env_path: Path) -> Config:
    values = dotenv_values(env_path)

    return Config(
        api_id=int(_require(values, "API_ID")),
        api_hash=_require(values, "API_HASH"),
        phone_number=_require(values, "PHONE_NUMBER"),
        session_name="tmpu",
    )
