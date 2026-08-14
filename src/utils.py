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


_MIN_STEM_LENGTH = 4
_MAX_SUFFIX_TRIM = 3


def keyword_stems(keyword: str) -> list[str]:
    """Return stems from broadest to most specific for partial matching."""
    normalized = keyword.lower().strip()
    if not normalized:
        return []

    stems: set[str] = {normalized}
    if len(normalized) > _MIN_STEM_LENGTH:
        max_trim = min(_MAX_SUFFIX_TRIM, len(normalized) - _MIN_STEM_LENGTH)
        for trim in range(1, max_trim + 1):
            stems.add(normalized[:-trim])
        for length in range(_MIN_STEM_LENGTH, len(normalized)):
            stems.add(normalized[:length])
    return sorted(stems, key=len)


def broad_search_term(keyword: str) -> str:
    """Single broad term for Telegram server search."""
    normalized = keyword.lower().strip()
    if len(normalized) <= 6:
        return normalized
    return normalized[:-2]


def search_terms(keywords: list[str]) -> list[str]:
    """Deduplicated broad search terms to query Telegram's search API."""
    if not keywords:
        return []

    normalized = [keyword.lower().strip() for keyword in keywords if keyword.strip()]
    if len(normalized) > 1:
        shared_roots: list[str] = []
        for anchor in normalized:
            for length in range(len(anchor), _MIN_STEM_LENGTH - 1, -1):
                root = anchor[:length]
                if all(root in word for word in normalized):
                    shared_roots.append(root)
                    break
        if shared_roots:
            return [min(shared_roots, key=len)]

    terms: list[str] = []
    seen: set[str] = set()
    for keyword in normalized:
        term = broad_search_term(keyword)
        if term and term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


def message_matches_keywords(
    text: str | None, caption: str | None, keywords: list[str]
) -> bool:
    if not keywords:
        return True
    haystack = " ".join(part for part in (text, caption) if part).lower()
    if not haystack:
        return False

    words = [word for word in haystack.split() if word]

    for keyword in keywords:
        for stem in keyword_stems(keyword):
            if stem in haystack:
                return True
            if any(
                word.startswith(stem) or stem.startswith(word)
                for word in words
                if len(word) >= _MIN_STEM_LENGTH or len(stem) <= len(word)
            ):
                return True
    return False


def normalize_name_query(query: str) -> str:
    return query.strip().lstrip("@").lower()


def name_matches_query(query: str, names: list[str]) -> bool:
    """Case-insensitive partial match against any display name or username."""
    needle = normalize_name_query(query)
    if not needle:
        return False
    return any(needle in name.lower() for name in names if name)


def confirm_deletion(stdin_input: str | None) -> bool:
    return stdin_input == "DELETE"


def expected_search_count(chat_count: int, keywords: list[str] | None) -> int:
    if not chat_count:
        return 0
    term_count = len(search_terms(keywords)) if keywords else 1
    return chat_count * term_count


def confirm_search(stdin_input: str | None) -> bool:
    return bool(stdin_input) and stdin_input.strip().lower() in ("y", "yes")


LOGGER_NAME = "tmpu"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
logger = logging.getLogger(LOGGER_NAME)

DEFAULT_WAIT_SECONDS = 0.1


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
