import logging

from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, ChatAdminRequiredError

from src.config import Config
from src.utils import chunk_list, message_matches_keywords, with_flood_retry

logger = logging.getLogger(__name__)


async def create_client(config: Config) -> TelegramClient:
    client = TelegramClient(config.session_name, config.api_id, config.api_hash)
    await client.start(phone=config.phone_number)
    return client


def _build_iter_messages_kwargs(
    keywords: list[str] | None,
    before,
    everyone: bool,
) -> dict:
    kwargs: dict = {}
    if keywords:
        kwargs["search"] = keywords[0]
    if before is not None:
        kwargs["offset_date"] = before
    if not everyone:
        kwargs["from_user"] = "me"
    return kwargs


def _matches_keywords(message, keywords: list[str] | None) -> bool:
    text = message.text or getattr(message, "message", None)
    caption = getattr(message, "caption", None)
    if not isinstance(caption, str):
        caption = None
    return message_matches_keywords(text, caption, keywords or [])


async def _collect_messages_from_chat(
    client,
    chat,
    me_id: int,
    keywords: list[str] | None,
    after,
    before,
    everyone: bool,
) -> list[tuple]:
    results: list[tuple] = []
    kwargs = _build_iter_messages_kwargs(keywords, before, everyone)

    try:
        async for message in client.iter_messages(chat, **kwargs):
            if after is not None and message.date < after:
                break

            if not everyone and message.sender_id != me_id:
                continue

            if not _matches_keywords(message, keywords):
                continue

            results.append((chat, message))
    except ChatAdminRequiredError:
        logger.warning("Admin rights required for chat %s; skipping", chat)
    return results


async def _resolve_chat_entity(client, chat_name: str):
    try:
        return await client.get_entity(chat_name)
    except ChannelPrivateError:
        logger.warning("Cannot access chat %s; skipping", chat_name)
        return None


async def _get_chat_entities(client, chats: list[str]) -> list:
    if len(chats) == 1 and chats[0] == "all":
        return [dialog.entity async for dialog in client.iter_dialogs()]

    entities = []
    for chat_name in chats:
        chat = await _resolve_chat_entity(client, chat_name)
        if chat is not None:
            entities.append(chat)
    return entities


async def fetch_target_messages(
    client,
    chats: list[str],
    keywords: list[str] | None = None,
    after=None,
    before=None,
    everyone: bool = False,
) -> list[tuple]:
    me = await client.get_me()
    me_id = me.id
    results: list[tuple] = []

    for chat in await _get_chat_entities(client, chats):
        chat_results = await _collect_messages_from_chat(
            client,
            chat,
            me_id,
            keywords,
            after,
            before,
            everyone,
        )
        results.extend(chat_results)

    return results


@with_flood_retry
async def _delete_chunk(client, chat, message_ids: list[int]) -> None:
    await client.delete_messages(chat, message_ids, revoke=True)


async def delete_messages_batch(client, chat, message_ids: list[int]) -> None:
    for chunk in chunk_list(message_ids):
        await _delete_chunk(client, chat, chunk)
