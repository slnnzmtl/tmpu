import asyncio
import logging
import sys
from collections import defaultdict
from pathlib import Path

from src.cli import parse_args
from src.config import load_config
from src.telegram_client import (
    create_client,
    delete_messages_batch,
    fetch_target_messages,
)
from src.utils import confirm_deletion, setup_logging

logger = logging.getLogger("tmpu")


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


def _log_message_preview(chat, message) -> None:
    logger.info(
        "Preview chat=%s msg_id=%s date=%s text=%s",
        _chat_label(chat),
        message.id,
        getattr(message, "date", None),
        _message_text(message),
    )


def _confirm_force_deletion() -> bool:
    if not sys.stdin.isatty():
        logger.error("Cannot confirm deletion: stdin is not a TTY")
        return False

    try:
        response = input("Type DELETE to confirm: ")
    except (OSError, EOFError):
        response = None
    return confirm_deletion(response)


def _group_message_ids_by_chat(messages) -> dict:
    by_chat = defaultdict(list)
    for chat, message in messages:
        by_chat[chat].append(message.id)
    return by_chat


async def _purge_messages(client, messages) -> None:
    if not _confirm_force_deletion():
        return

    for chat, message_ids in _group_message_ids_by_chat(messages).items():
        await delete_messages_batch(client, chat, message_ids)


async def run_purge(argv: list[str] | None = None, env_path: Path | None = None) -> None:
    setup_logging()
    args = parse_args(argv)
    config = load_config(env_path or Path(".env"))
    client = await create_client(config)
    try:
        messages = await fetch_target_messages(
            client,
            args.chats,
            args.keywords,
            args.after,
            args.before,
            args.everyone,
        )

        for chat, message in messages:
            _log_message_preview(chat, message)

        if args.force:
            await _purge_messages(client, messages)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run_purge())
