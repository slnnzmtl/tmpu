"""Utility helpers for TMPU."""

import asyncio
import functools
import logging
import sys

from telethon.errors import FloodPremiumWaitError, FloodWaitError, SlowModeWaitError

_FLOOD_ERRORS: tuple[type[BaseException], ...] = (
    FloodWaitError,
    FloodPremiumWaitError,
    SlowModeWaitError,
)
try:
    from telethon.errors import FloodPeerWaitError
except ImportError:
    pass
else:
    _FLOOD_ERRORS = _FLOOD_ERRORS + (FloodPeerWaitError,)

_MAX_FLOOD_RETRIES = 5
_LOGGER_NAME = "tmpu"


def chunk_list(lst: list, size: int = 100) -> list[list]:
    if not lst:
        return []
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def message_matches_keywords(
    text: str | None, caption: str | None, keywords: list[str]
) -> bool:
    if not keywords:
        return True
    haystack = " ".join(part for part in (text, caption) if part).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def confirm_deletion(stdin_input: str | None) -> bool:
    return stdin_input == "DELETE"


def _ensure_stderr_handler(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr:
            return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    _ensure_stderr_handler(logger)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    return logger


def with_flood_retry(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        for attempt in range(_MAX_FLOOD_RETRIES + 1):
            try:
                return await func(*args, **kwargs)
            except _FLOOD_ERRORS as exc:
                if attempt == _MAX_FLOOD_RETRIES:
                    raise
                await asyncio.sleep(exc.seconds)

    return wrapper
