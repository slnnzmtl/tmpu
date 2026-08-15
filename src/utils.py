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


def chunk_list(lst: list, size: int = 100) -> list[list]:
    if not lst:
        return []
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def search_terms(keywords: list[str]) -> list[str]:
    """Deduplicated keywords as typed for Telegram server search."""
    terms: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        term = keyword.lower().strip()
        if term and term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


def message_matches_keywords(
    text: str | None, caption: str | None, keywords: list[str]
) -> bool:
    """Case-insensitive substring match against message text and caption."""
    if not keywords:
        return True
    haystack = " ".join(part for part in (text, caption) if part).lower()
    if not haystack:
        return False

    return any(
        keyword.lower().strip() in haystack
        for keyword in keywords
        if keyword.strip()
    )


def normalize_name_query(query: str) -> str:
    return query.strip().lstrip("@").lower()


def name_matches_query(query: str, names: list[str]) -> bool:
    """Case-insensitive partial match against any display name or username."""
    needle = normalize_name_query(query)
    if not needle:
        return False
    return any(needle in name.lower() for name in names if name)


def confirm_yes(stdin_input: str | None) -> bool:
    """Accept y/yes (case-insensitive); anything else is declined."""
    return bool(stdin_input) and stdin_input.strip().lower() in ("y", "yes")


def confirm_deletion(stdin_input: str | None) -> bool:
    return confirm_yes(stdin_input)


def expected_search_count(chat_count: int, keywords: list[str] | None) -> int:
    if not chat_count:
        return 0
    term_count = len(search_terms(keywords)) if keywords else 1
    return chat_count * term_count


def confirm_search(stdin_input: str | None) -> bool:
    return confirm_yes(stdin_input)


LOGGER_NAME = "tmpu"
_LOG_FORMAT = "%(message)s"
logger = logging.getLogger(LOGGER_NAME)

DEFAULT_WAIT_SECONDS = 0.05


async def pause_for_telegram(seconds: float) -> None:
    """Proactive pacing between chats, search terms, or delete chunks.

    No-op when ``seconds`` is 0 so callers can always invoke it after the first
    item without a separate truthiness guard.
    """
    if not seconds:
        return
    await asyncio.sleep(seconds)


def _is_stderr_handler(handler: logging.Handler) -> bool:
    return isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr


def _ensure_stderr_handler(logger: logging.Logger) -> None:
    handler = next((h for h in logger.handlers if _is_stderr_handler(h)), None)
    if handler is None:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))


def setup_logging() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    _ensure_stderr_handler(logger)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("telethon.network.mtprotosender").setLevel(logging.ERROR)
    return logger


def with_flood_retry(func):
    """Retry after Telegram FloodWait* / SlowModeWait (reactive; separate from
    proactive ``pause_for_telegram`` pacing).
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        for attempt in range(_MAX_FLOOD_RETRIES + 1):
            try:
                return await func(*args, **kwargs)
            except _FLOOD_ERRORS as exc:
                if attempt == _MAX_FLOOD_RETRIES:
                    raise
                logger.warning("Flood wait: sleeping %s seconds", exc.seconds)
                await asyncio.sleep(exc.seconds)

    return wrapper
