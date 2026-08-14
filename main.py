import asyncio
import logging
import sys
from pathlib import Path

from src.cli import parse_args
from src.config import load_config
from src.telegram_client import (
    create_client,
    delete_messages_batch,
    fetch_target_messages,
    resolve_search_chats,
    sender_label,
)
from src.utils import (
    LOGGER_NAME,
    confirm_deletion,
    confirm_search,
    expected_search_count,
    search_terms,
    setup_logging,
)

logger = logging.getLogger(LOGGER_NAME)


def _truncate_text(text: str | None, max_len: int = 80) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _chat_label(chat) -> str:
    chat_id = getattr(chat, "id", None)
    name = (
        getattr(chat, "title", None)
        or getattr(chat, "name", None)
        or getattr(chat, "username", None)
    )
    if chat_id is not None and name:
        return f"{name} ({chat_id})"
    if chat_id is not None:
        return str(chat_id)
    return str(chat)


def _message_text(message) -> str:
    return _truncate_text(
        getattr(message, "text", None) or getattr(message, "message", None)
    )


def _confirm_search() -> bool:
    if not sys.stdin.isatty():
        logger.error("Cannot confirm search: stdin is not a TTY")
        return False

    try:
        response = input("Proceed with search? [y/N]: ")
    except (OSError, EOFError):
        response = None
    return confirm_search(response)


def _confirm_force_deletion() -> bool:
    if not sys.stdin.isatty():
        logger.error("Cannot confirm deletion: stdin is not a TTY")
        return False

    try:
        response = input("Type DELETE to confirm: ")
    except (OSError, EOFError):
        response = None
    return confirm_deletion(response)


def _chat_key(chat) -> object:
    chat_id = getattr(chat, "id", None)
    if chat_id is not None:
        return chat_id
    return id(chat)


def _group_message_ids_by_chat(messages) -> list[tuple]:
    grouped: dict[object, tuple] = {}
    for chat, message in messages:
        key = _chat_key(chat)
        if key not in grouped:
            grouped[key] = (chat, [])
        grouped[key][1].append(message.id)
    return list(grouped.values())


async def _purge_messages(client, messages, wait_seconds: float) -> None:
    if not _confirm_force_deletion():
        return

    for chat, message_ids in _group_message_ids_by_chat(messages):
        await delete_messages_batch(
            client, chat, message_ids, wait_seconds=wait_seconds
        )


async def run_purge(argv: list[str] | None = None, env_path: Path | None = None) -> None:
    setup_logging()
    args = parse_args(argv)
    config = load_config(env_path or Path(".env"))
    client = await create_client(config)
    try:
        me = await client.get_me()
        logger.info("Resolving candidate chats...")
        chat_entities = await resolve_search_chats(
            client,
            args.chats,
            include_channels=args.channels,
            include_group_chats=args.group_chats,
            after=args.after,
        )
        candidate_count = len(chat_entities)
        if candidate_count == 0:
            logger.info("No candidate chats to search")
            return

        term_count = len(search_terms(args.keywords or []))
        search_count = expected_search_count(candidate_count, args.keywords)
        logger.info(
            "About to search %d chats (%d search terms, %d Telegram searches).",
            candidate_count,
            term_count,
            search_count,
        )

        if not args.no_confirmation and not _confirm_search():
            logger.info("Search aborted")
            return

        messages = await fetch_target_messages(
            client,
            args.chats,
            args.keywords,
            args.after,
            args.before,
            args.everyone,
            args.from_user,
            include_channels=args.channels,
            include_group_chats=args.group_chats,
            chat_entities=chat_entities,
            wait_seconds=args.wait_seconds,
        )
        logger.info("Found %d matching message(s)", len(messages))
        if not messages:
            logger.info(
                "No matches. Tips: use @username or exact chat title; "
                "add --everyone or --from <user> if matches are from other users."
            )

        if args.force:
            await _purge_messages(client, messages, wait_seconds=args.wait_seconds)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run_purge())
