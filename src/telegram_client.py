import logging
from datetime import datetime, timedelta

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from telethon.tl.types import Channel, Chat, User

from src.config import Config
from src.utils import (
    DEFAULT_WAIT_SECONDS,
    LOGGER_NAME,
    chunk_list,
    message_matches_keywords,
    name_matches_query,
    pause_for_telegram,
    search_terms,
    with_flood_retry,
)

logger = logging.getLogger(LOGGER_NAME).getChild("telegram_client")


async def create_client(config: Config) -> TelegramClient:
    client = TelegramClient(config.session_name, config.api_id, config.api_hash)
    await client.start(phone=config.phone_number)
    return client


def _before_cutoff(before) -> datetime | None:
    """--before YYYY-MM-DD is inclusive: include the full calendar day."""
    if before is None:
        return None
    return before + timedelta(days=1)


def _iter_messages_kwargs(before, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> dict:
    """Map CLI wait_seconds onto Telethon iter_messages wait_time (+ optional before)."""
    kwargs: dict = {"wait_time": wait_seconds}
    cutoff = _before_cutoff(before)
    if cutoff is not None:
        kwargs["offset_date"] = cutoff
    return kwargs


_LOOKUP_ERRORS = (
    ValueError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)


async def _lookup_entity(client, candidate: str):
    if candidate.lstrip("-").isdigit():
        return await client.get_entity(int(candidate))
    return await client.get_entity(candidate)


def _log_entity_resolved(kind: str, query: str, entity) -> None:
    names = _entity_names(entity)
    logger.info("Resolved %s %r as %s", kind, query, names[0] if names else query)


def _message_body(message) -> str | None:
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    for attr in ("message", "raw_text"):
        value = getattr(message, attr, None)
        if isinstance(value, str):
            return value
    return None


def _is_from_me(message, me_id: int) -> bool:
    if getattr(message, "out", False) is True:
        return True
    return getattr(message, "sender_id", None) == me_id


def sender_label(message, me_id: int | None = None) -> str:
    """Human-readable sender for preview logs."""
    if me_id is not None and _is_from_me(message, me_id):
        return "me"

    sender = getattr(message, "sender", None)
    if sender is not None:
        names = _entity_names(sender)
        if names:
            username = getattr(sender, "username", None)
            if username:
                return f"@{username}"
            return names[0]

    sender_id = getattr(message, "sender_id", None)
    if sender_id is not None:
        return str(sender_id)
    return "unknown"


def _entity_names(entity, dialog_name: str | None = None) -> list[str]:
    names: list[str] = []
    for value in (
        dialog_name,
        getattr(entity, "title", None),
        getattr(entity, "first_name", None),
        getattr(entity, "last_name", None),
        getattr(entity, "username", None),
    ):
        if value:
            names.append(str(value))
    return names


def _matches_sender(message, me_id: int, everyone: bool, from_user_id: int | None) -> bool:
    if from_user_id is not None:
        if from_user_id == me_id:
            return _is_from_me(message, me_id)
        return getattr(message, "sender_id", None) == from_user_id
    if everyone:
        return True
    return _is_from_me(message, me_id)


def _matches_keywords(message, keywords: list[str] | None) -> bool:
    text = _message_body(message)
    caption = getattr(message, "caption", None)
    if not isinstance(caption, str):
        caption = None
    return message_matches_keywords(text, caption, keywords or [])


def _passes_date_filters(message, after, before_cutoff) -> bool:
    if before_cutoff is not None and message.date >= before_cutoff:
        return False
    if after is not None and message.date < after:
        return False
    return True


def _chat_log_name(chat) -> str:
    """Short label for logs — never Telethon's full User/Channel repr."""
    title = getattr(chat, "title", None)
    if title:
        return str(title)

    username = getattr(chat, "username", None)
    if username:
        return f"@{username}"

    first = getattr(chat, "first_name", None)
    last = getattr(chat, "last_name", None)
    display = " ".join(part for part in (first, last) if part)
    if display:
        return display

    chat_id = getattr(chat, "id", None)
    if chat_id is not None:
        return str(chat_id)
    return "unknown"


_MATCH_TEXT_PREVIEW_LEN = 80


def _log_match(chat, message, me_id: int) -> None:
    """Log a single live match (same fields for search and history paths)."""
    text = (_message_body(message) or "")[:_MATCH_TEXT_PREVIEW_LEN]
    logger.info(
        "[%s] %s (ID: %s): %s",
        getattr(message, "date", None),
        sender_label(message, me_id),
        message.id,
        text,
    )


def _record_match(results: list[tuple], chat, message) -> None:
    results.append((chat, message))


def _finalize_chat_matches(results: list[tuple], me_id: int) -> list[tuple]:
    """Sort one chat's matches oldest-first, then log in that order."""
    results.sort(key=lambda item: item[1].date)
    for chat, message in results:
        _log_match(chat, message, me_id)
    return results


async def _collect_via_search(
    client,
    chat,
    me_id: int,
    keywords: list[str],
    after,
    before,
    everyone: bool,
    from_user_id: int | None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    progress: tuple[int, int] | None = None,
) -> list[tuple]:
    """Use Telegram server search per keyword (same engine as the mobile app)."""
    results: list[tuple] = []
    seen_ids: set[int] = set()
    iter_kwargs = _iter_messages_kwargs(before, wait_seconds=wait_seconds)
    before_cutoff = _before_cutoff(before)
    chat_name = _chat_log_name(chat)
    terms = search_terms(keywords)
    search_hits = 0
    excluded_sender = 0
    excluded_date = 0

    for index, keyword in enumerate(terms):
        if index > 0 and wait_seconds:
            await pause_for_telegram(wait_seconds)
        async for message in client.iter_messages(chat, search=keyword, **iter_kwargs):
            search_hits += 1
            if message.id in seen_ids:
                continue
            seen_ids.add(message.id)

            if not _passes_date_filters(message, after, before_cutoff):
                excluded_date += 1
                continue

            if not _matches_sender(message, me_id, everyone, from_user_id):
                excluded_sender += 1
                continue

            if not _matches_keywords(message, keywords):
                continue

            _record_match(results, chat, message)

    if progress is None:
        prefix = f"Searching {chat_name}"
    else:
        index, total = progress
        prefix = f"Searching {index}/{total}: {chat_name}"
    if search_hits > 0:
        logger.info(
            "%s hits=%d excluded(sender)=%d excluded(date)=%d matched=%d",
            prefix,
            search_hits,
            excluded_sender,
            excluded_date,
            len(results),
        )
    else:
        logger.info(
            "%s no matches found",
            prefix,
        )

    if (
        search_hits > 0
        and not results
        and excluded_sender > 0
        and not everyone
        and from_user_id is None
    ):
        logger.info(
            "Telegram found messages but all were from other users. "
            "Re-run with --everyone or --from <user> to include them."
        )
    return _finalize_chat_matches(results, me_id)


async def _collect_via_history(
    client,
    chat,
    me_id: int,
    keywords: list[str] | None,
    after,
    before,
    everyone: bool,
    from_user_id: int | None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    progress: tuple[int, int] | None = None,
) -> list[tuple]:
    """Scroll full chat history when no keywords are provided."""
    results: list[tuple] = []
    before_cutoff = _before_cutoff(before)
    chat_name = _chat_log_name(chat)

    scanned = 0
    async for message in client.iter_messages(
        chat, **_iter_messages_kwargs(before, wait_seconds=wait_seconds)
    ):
        scanned += 1
        if not _passes_date_filters(message, after, before_cutoff):
            if after is not None and message.date < after:
                break
            continue

        if not _matches_sender(message, me_id, everyone, from_user_id):
            continue

        if not _matches_keywords(message, keywords):
            continue

        _record_match(results, chat, message)

    if progress is None:
        prefix = f"Searching {chat_name}"
    else:
        index, total = progress
        prefix = f"Searching {index}/{total}: {chat_name}"
    logger.info(
        "%s scanned=%d matched=%d",
        prefix,
        scanned,
        len(results),
    )
    return _finalize_chat_matches(results, me_id)


async def _collect_messages_from_chat(
    client,
    chat,
    me_id: int,
    keywords: list[str] | None,
    after,
    before,
    everyone: bool,
    from_user_id: int | None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    progress: tuple[int, int] | None = None,
) -> list[tuple]:
    try:
        if keywords:
            return await _collect_via_search(
                client,
                chat,
                me_id,
                keywords,
                after,
                before,
                everyone,
                from_user_id,
                wait_seconds=wait_seconds,
                progress=progress,
            )
        return await _collect_via_history(
            client,
            chat,
            me_id,
            keywords,
            after,
            before,
            everyone,
            from_user_id,
            wait_seconds=wait_seconds,
            progress=progress,
        )
    except ChatAdminRequiredError:
        logger.warning("Admin rights required for chat %s; skipping", chat)
        return []


async def _find_entities_by_partial_name(
    client,
    query: str,
    *,
    users_only: bool = False,
) -> list[tuple]:
    """Return unique (entity, label) pairs whose names partially match query."""
    matches: dict[int, tuple] = {}
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if users_only and not isinstance(entity, User):
            continue
        names = _entity_names(entity, dialog.name)
        if not name_matches_query(query, names):
            continue
        entity_id = getattr(entity, "id", None)
        if entity_id is None:
            continue
        label = names[0] if names else dialog.name or str(entity_id)
        matches[entity_id] = (entity, label)
    return list(matches.values())


def _resolve_partial_name_match(
    query: str,
    matches: list[tuple],
    entity_kind: str,
):
    if not matches:
        return None
    if len(matches) == 1:
        entity, label = matches[0]
        logger.info("Resolved %s %r by partial name match: %s", entity_kind, query, label)
        return entity

    labels = ", ".join(sorted({label for _, label in matches}))
    logger.warning(
        "Multiple %ss partially match %r: %s. Use a more specific name, @username, or ID.",
        entity_kind,
        query,
        labels,
    )
    return None


async def _resolve_entity(
    client,
    query: str,
    *,
    kind: str,
    users_only: bool,
    candidates: list[str],
    on_private_error,
    not_found_message: str,
):
    """Try direct lookup, then partial dialog name match."""
    for candidate in candidates:
        try:
            entity = await _lookup_entity(client, candidate)
            _log_entity_resolved(kind, query, entity)
            return entity
        except ChannelPrivateError:
            if on_private_error():
                return None
            continue
        except _LOOKUP_ERRORS:
            continue
        except TypeError:
            if kind != "sender":
                raise
            continue

    entity = _resolve_partial_name_match(
        query,
        await _find_entities_by_partial_name(client, query, users_only=users_only),
        kind,
    )
    if entity is not None:
        return entity

    logger.warning(not_found_message, query)
    return None


async def _resolve_user_entity(client, user_query: str):
    """Resolve a sender by @username, numeric ID, or partial display name."""
    query = user_query.strip()
    candidates = [query]
    if not query.startswith("@") and not query.lstrip("-").isdigit():
        candidates.append(f"@{query}")

    return await _resolve_entity(
        client,
        query,
        kind="sender",
        users_only=True,
        candidates=candidates,
        on_private_error=lambda: False,
        not_found_message=(
            "Could not find sender %r. Use @username, numeric ID, or part of a display name."
        ),
    )


async def _resolve_chat_entity(client, chat_name: str):
    query = chat_name.strip()
    candidates = [query]
    if not query.startswith("@"):
        candidates.append(f"@{query}")

    def on_private_error():
        logger.warning("Cannot access chat %s; skipping", chat_name)
        return True

    return await _resolve_entity(
        client,
        query,
        kind="chat",
        users_only=False,
        candidates=candidates,
        on_private_error=on_private_error,
        not_found_message=(
            "Could not find chat %r. Use @username, numeric ID, or part of a dialog title."
        ),
    )


def _dialog_kind(entity) -> str:
    """Classify a dialog entity: user, group (incl. megagroup), channel, or other."""
    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Chat) or (
        isinstance(entity, Channel) and bool(getattr(entity, "megagroup", False))
    ):
        return "group"
    if isinstance(entity, Channel):
        return "channel"
    return "other"


def _filter_entities_by_dialog_type(
    entities: list,
    include_channels: bool,
    include_group_chats: bool,
) -> list:
    filtered = []
    skipped_groups = 0
    skipped_channels = 0
    for entity in entities:
        kind = _dialog_kind(entity)
        if kind == "group":
            included = include_group_chats
        elif kind == "channel":
            included = include_channels
        else:
            included = True

        if included:
            filtered.append(entity)
            continue

        if kind == "group":
            skipped_groups += 1
        else:
            skipped_channels += 1

    skipped_total = skipped_groups + skipped_channels
    if skipped_total:
        skipped = (
            (skipped_groups, "group", "groups", "--group-chats"),
            (skipped_channels, "channel", "channels", "--channels"),
        )
        parts = [
            f"{count} {singular if count == 1 else plural}"
            for count, singular, plural, _ in skipped
            if count
        ]
        hint_parts = [flag for count, _, _, flag in skipped if count]
        chat_label = "chat" if skipped_total == 1 else "chats"
        logger.info(
            "Skipping %d %s (%s); pass %s to include",
            skipped_total,
            chat_label,
            ", ".join(parts),
            " / ".join(hint_parts),
        )
    return filtered


def _dialog_last_message_date(dialog) -> datetime | None:
    date = getattr(dialog, "date", None)
    if isinstance(date, datetime):
        return date
    message = getattr(dialog, "message", None)
    if message is None:
        return None
    msg_date = getattr(message, "date", None)
    if isinstance(msg_date, datetime):
        return msg_date
    return None


def _last_message_dates_by_entity_id(dialogs) -> dict[int, datetime]:
    """Map entity id -> last activity from dialog.date (or message.date)."""
    last_dates: dict[int, datetime] = {}
    for dialog in dialogs:
        entity = getattr(dialog, "entity", None)
        entity_id = getattr(entity, "id", None)
        if entity_id is None:
            continue
        last_date = _dialog_last_message_date(dialog)
        if last_date is not None:
            last_dates[entity_id] = last_date
    return last_dates


def _filter_entities_by_last_message_after(entities, after, last_dates) -> list:
    """Drop entities whose last dialog activity is before after; keep unknown ids."""
    filtered = []
    skipped = 0
    for entity in entities:
        entity_id = getattr(entity, "id", None)
        if entity_id in last_dates and last_dates[entity_id] < after:
            skipped += 1
            continue
        filtered.append(entity)
    if skipped > 0:
        logger.info(
            "Skipping %d chats with last message before %s",
            skipped,
            after.strftime("%Y-%m-%d"),
        )
    return filtered


async def resolve_search_chats(
    client,
    chats: list[str],
    include_channels: bool = False,
    include_group_chats: bool = False,
    after=None,
) -> list:
    dialogs = None
    if len(chats) == 1 and chats[0] == "all":
        logger.info("Listing dialogs...")
        dialogs = [dialog async for dialog in client.iter_dialogs()]
        entities = [dialog.entity for dialog in dialogs]
    else:
        logger.info("Looking up %d chat(s)...", len(chats))
        entities = []
        for chat_name in chats:
            chat = await _resolve_chat_entity(client, chat_name)
            if chat is not None:
                entities.append(chat)

        if not entities:
            logger.warning("No chats resolved from: %s", ", ".join(chats))

    logger.info("Filtering candidate chats...")
    entities = _filter_entities_by_dialog_type(
        entities,
        include_channels,
        include_group_chats,
    )

    if after is not None:
        if dialogs is None:
            logger.info("Listing dialogs for --after filter...")
            dialogs = [dialog async for dialog in client.iter_dialogs()]
        last_dates = _last_message_dates_by_entity_id(dialogs)
        entities = _filter_entities_by_last_message_after(
            entities, after, last_dates
        )

    return entities


async def fetch_target_messages(
    client,
    chats: list[str],
    keywords: list[str] | None = None,
    after=None,
    before=None,
    everyone: bool = False,
    from_user: str | None = None,
    include_channels: bool = False,
    include_group_chats: bool = False,
    chat_entities=None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> list[tuple]:
    me = await client.get_me()
    me_id = me.id
    from_user_id = None
    if from_user:
        entity = await _resolve_user_entity(client, from_user)
        if entity is None:
            return []
        from_user_id = getattr(entity, "id", None)
        if from_user_id is None:
            logger.warning("Resolved sender has no id: %r", from_user)
            return []

    results: list[tuple] = []

    entities = (
        chat_entities
        if chat_entities is not None
        else await resolve_search_chats(
            client,
            chats,
            include_channels=include_channels,
            include_group_chats=include_group_chats,
            after=after,
        )
    )
    total = len(entities)
    for index, chat in enumerate(entities):
        if index > 0 and wait_seconds:
            await pause_for_telegram(wait_seconds)
        chat_results = await _collect_messages_from_chat(
            client,
            chat,
            me_id,
            keywords,
            after,
            before,
            everyone,
            from_user_id,
            wait_seconds=wait_seconds,
            progress=(index + 1, total),
        )
        results.extend(chat_results)

    return results


@with_flood_retry
async def _delete_chunk(client, chat, message_ids: list[int]) -> None:
    await client.delete_messages(chat, message_ids, revoke=True)


async def delete_messages_batch(
    client,
    chat,
    message_ids: list[int],
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> None:
    chunks = chunk_list(message_ids)
    for index, chunk in enumerate(chunks):
        if index > 0 and wait_seconds:
            await pause_for_telegram(wait_seconds)
        await _delete_chunk(client, chat, chunk)
