import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telethon.errors import ChannelPrivateError, ChatAdminRequiredError
from telethon.tl.functions.messages import SearchGlobalRequest
from telethon.tl.types import Channel, Chat, ChatPhotoEmpty, User

from src.config import Config
from src.telegram_client import (
    _chat_log_name,
    _filter_entities_by_exclude_chats,
    create_client,
    delete_messages_batch,
    fetch_target_messages,
    sender_label,
)

ME_ID = 12345
OTHER_ID = 99999


def _make_message(
    message_id: int,
    date: datetime,
    text: str,
    sender_id: int,
) -> MagicMock:
    message = MagicMock()
    message.id = message_id
    message.date = date
    message.text = text
    message.message = text
    message.sender_id = sender_id
    return message


async def _async_iter(items):
    for item in items:
        yield item


def _make_dialog(
    entity_id: int,
    title: str = "chat",
    date: datetime | None = None,
) -> MagicMock:
    dialog = MagicMock()
    dialog.entity = MagicMock(id=entity_id, title=title)
    if date is not None:
        dialog.date = date
    return dialog


_TL_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _tl_user(entity_id: int, first_name: str = "Alice") -> User:
    return User(id=entity_id, access_hash=0, first_name=first_name)


def _tl_chat(entity_id: int, title: str = "GroupChat") -> Chat:
    return Chat(
        id=entity_id,
        title=title,
        photo=ChatPhotoEmpty(),
        participants_count=1,
        date=_TL_DATE,
        version=0,
    )


def _tl_channel(
    entity_id: int,
    title: str,
    *,
    megagroup: bool,
) -> Channel:
    return Channel(
        id=entity_id,
        title=title,
        photo=ChatPhotoEmpty(),
        date=_TL_DATE,
        megagroup=megagroup,
        access_hash=0,
    )


def _make_typed_dialog(entity) -> MagicMock:
    dialog = MagicMock()
    dialog.entity = entity
    dialog.name = (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or "chat"
    )
    return dialog


def _searched_entity_ids(client) -> list[int]:
    return [call.args[0].id for call in client.iter_messages.call_args_list]


def _messages_from_result(result):
    messages = []
    for item in result:
        if isinstance(item, tuple):
            messages.append(item[1])
        else:
            messages.append(item)
    return messages


def _message_ids(result):
    return [message.id for message in _messages_from_result(result)]


def test_sender_label_prefers_username():
    message = MagicMock()
    message.out = False
    message.sender_id = OTHER_ID
    message.sender = MagicMock(username="alice", first_name="Alice", title=None, last_name=None)
    assert sender_label(message, ME_ID) == "@alice"


def test_sender_label_me_for_own_messages():
    message = MagicMock()
    message.out = True
    message.sender_id = ME_ID
    assert sender_label(message, ME_ID) == "me"


def test_chat_log_name_uses_user_display_name_not_full_repr():
    user = User(id=6743660549, access_hash=0, first_name="Ann", last_name="Lee")
    assert _chat_log_name(user) == "Ann Lee"
    assert "User(" not in _chat_log_name(user)


def test_chat_log_name_deleted_user_falls_back_to_id():
    user = User(id=6743660549, access_hash=0, deleted=True)
    assert _chat_log_name(user) == "6743660549"
    assert "User(" not in _chat_log_name(user)


def test_chat_log_name_prefers_username():
    user = User(id=1, access_hash=0, username="alice", first_name="Alice")
    assert _chat_log_name(user) == "@alice"


class TestCreateClient:
    @pytest.mark.asyncio
    async def test_create_client_builds_telegram_client_from_config(self):
        config = Config(
            api_id=42,
            api_hash="test-hash",
            phone_number="+15551234567",
            session_name="tmpu",
        )
        mock_client = AsyncMock()

        with patch(
            "src.telegram_client.TelegramClient", return_value=mock_client
        ) as mock_cls:
            client = await create_client(config)

        mock_cls.assert_called_once_with("tmpu", 42, "test-hash")
        mock_client.start.assert_awaited_once_with(phone="+15551234567")
        assert client is mock_client


class TestFetchTargetMessagesSpecificChat:
    @pytest.mark.asyncio
    async def test_filters_by_keyword_or_case_insensitive_and_sender_me(self):
        chat_entity = MagicMock(id=100, title="mygroup")
        after = datetime(2026, 1, 10, tzinfo=timezone.utc)
        messages = [
            _make_message(
                1,
                datetime(2026, 1, 15, tzinfo=timezone.utc),
                "hello SPAM",
                ME_ID,
            ),
            _make_message(
                2,
                datetime(2026, 1, 14, tzinfo=timezone.utc),
                "nothing special",
                ME_ID,
            ),
            _make_message(
                3,
                datetime(2026, 1, 12, tzinfo=timezone.utc),
                "TRASH here",
                ME_ID,
            ),
            _make_message(
                4,
                datetime(2026, 1, 11, tzinfo=timezone.utc),
                "spam from other",
                OTHER_ID,
            ),
            _make_message(
                5,
                datetime(2026, 1, 8, tzinfo=timezone.utc),
                "old spam",
                ME_ID,
            ),
        ]

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter(messages))
        client.__call__ = AsyncMock()

        result = await fetch_target_messages(
            client,
            chats=["mygroup"],
            keywords=["spam", "trash"],
            after=after,
            everyone=False,
            wait_seconds=0,
        )

        assert _message_ids(result) == [3, 1]
        client.get_entity.assert_awaited_once_with("mygroup")
        assert client.iter_messages.call_count == 2
        searched = {call.kwargs["search"] for call in client.iter_messages.call_args_list}
        assert searched == {"spam", "trash"}

    @pytest.mark.asyncio
    async def test_sorts_matches_by_date_interleaving_keywords(self):
        chat_entity = MagicMock(id=100, title="mygroup")
        spam_old = _make_message(
            1,
            datetime(2026, 1, 10, tzinfo=timezone.utc),
            "early spam",
            ME_ID,
        )
        trash_mid = _make_message(
            2,
            datetime(2026, 1, 12, tzinfo=timezone.utc),
            "mid trash",
            ME_ID,
        )
        spam_new = _make_message(
            3,
            datetime(2026, 1, 15, tzinfo=timezone.utc),
            "late spam",
            ME_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)

        def iter_messages_side_effect(_entity, **kwargs):
            term = kwargs.get("search")
            if term == "spam":
                return _async_iter([spam_new, spam_old])
            if term == "trash":
                return _async_iter([trash_mid])
            return _async_iter([])

        client.iter_messages = MagicMock(side_effect=iter_messages_side_effect)

        result = await fetch_target_messages(
            client,
            chats=["mygroup"],
            keywords=["spam", "trash"],
            everyone=False,
            wait_seconds=0,
        )

        # Keyword discovery order would be [3, 1, 2]; chronological mixes terms.
        assert _message_ids(result) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_matches_any_keyword_not_only_the_first(self):
        chat_entity = MagicMock(id=100, title="mygroup")
        alexander_messages = [
            _make_message(
                1,
                datetime(2026, 1, 15, tzinfo=timezone.utc),
                "about alexander",
                ME_ID,
            ),
            _make_message(
                2,
                datetime(2026, 1, 14, tzinfo=timezone.utc),
                "about alexander",
                ME_ID,
            ),
        ]
        alexandra_messages = [
            _make_message(
                3,
                datetime(2026, 1, 13, tzinfo=timezone.utc),
                "meeting alexandra",
                ME_ID,
            ),
        ]

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)

        def iter_for_search(*_args, **kwargs):
            term = kwargs.get("search")
            if term == "alexander":
                return _async_iter(alexander_messages)
            if term == "alexandra":
                return _async_iter(alexandra_messages)
            return _async_iter([])

        client.iter_messages = MagicMock(side_effect=iter_for_search)

        result = await fetch_target_messages(
            client,
            chats=["mygroup"],
            keywords=["alexander", "alexandra", "alexandrum"],
            everyone=False,
        )

        assert _message_ids(result) == [3, 2, 1]
        assert client.iter_messages.call_count == 3
        searched = [call.kwargs["search"] for call in client.iter_messages.call_args_list]
        assert searched == ["alexander", "alexandra", "alexandrum"]

    @pytest.mark.asyncio
    async def test_logs_telegram_search_term(self, caplog):
        chat_entity = MagicMock(id=100, title="mygroup")
        messages = [
            _make_message(
                1,
                datetime(2026, 1, 15, tzinfo=timezone.utc),
                "about alexander",
                ME_ID,
            ),
        ]

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter(messages))

        with caplog.at_level(logging.INFO):
            await fetch_target_messages(
                client,
                chats=["mygroup"],
                keywords=["alexander"],
                everyone=False,
            )

        assert client.iter_messages.call_args.kwargs["search"] == "alexander"
        assert any(
            "Searching" in record.message and "mygroup" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_substring_keyword_keeps_prefix_and_mid_word_hits(self):
        chat_entity = MagicMock(id=100, title="mygroup")
        messages = [
            _make_message(
                1,
                datetime(2026, 1, 15, tzinfo=timezone.utc),
                "go to hell",
                ME_ID,
            ),
            _make_message(
                2,
                datetime(2026, 1, 14, tzinfo=timezone.utc),
                "say hello",
                ME_ID,
            ),
            _make_message(
                3,
                datetime(2026, 1, 13, tzinfo=timezone.utc),
                "see the helicopter",
                ME_ID,
            ),
            _make_message(
                4,
                datetime(2026, 1, 12, tzinfo=timezone.utc),
                "unrelated message",
                ME_ID,
            ),
        ]

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter(messages))

        result = await fetch_target_messages(
            client,
            chats=["mygroup"],
            keywords=["hel"],
            everyone=False,
        )

        assert _message_ids(result) == [3, 2, 1]
        assert client.iter_messages.call_args.kwargs["search"] == "hel"

    @pytest.mark.asyncio
    async def test_logs_each_match_as_found(self, caplog):
        chat_entity = MagicMock(id=100, title="mygroup")
        messages = [
            _make_message(
                42,
                datetime(2026, 1, 15, tzinfo=timezone.utc),
                "hello spam",
                ME_ID,
            ),
        ]

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter(messages))

        with caplog.at_level(logging.INFO):
            result = await fetch_target_messages(
                client,
                chats=["mygroup"],
                keywords=["spam"],
                everyone=False,
            )

        assert _message_ids(result) == [42]
        assert any(
            record.levelno == logging.INFO
            and "ID: 42" in record.message
            and "hello spam" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_search_hits_from_others_excluded_without_everyone(self, caplog):
        chat_entity = MagicMock(id=100, title="mygroup")
        msg = _make_message(
            1,
            datetime(2026, 1, 15, tzinfo=timezone.utc),
            "about alexander",
            OTHER_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter([msg]))

        with caplog.at_level(logging.INFO):
            result = await fetch_target_messages(
                client,
                chats=["mygroup"],
                keywords=["alexander"],
                everyone=False,
            )

        assert result == []
        assert any("excluded(sender)" in record.message for record in caplog.records)
        assert any("--everyone" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_includes_other_senders_when_everyone_true(self):
        chat_entity = MagicMock(id=100, title="mygroup")
        after = datetime(2026, 1, 10, tzinfo=timezone.utc)
        messages = [
            _make_message(
                1,
                datetime(2026, 1, 15, tzinfo=timezone.utc),
                "hello SPAM",
                ME_ID,
            ),
            _make_message(
                2,
                datetime(2026, 1, 11, tzinfo=timezone.utc),
                "spam from other",
                OTHER_ID,
            ),
        ]

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter(messages))

        result = await fetch_target_messages(
            client,
            chats=["mygroup"],
            keywords=["spam"],
            after=after,
            everyone=True,
        )

        assert _message_ids(result) == [2, 1]

    @pytest.mark.asyncio
    async def test_filters_by_from_user(self):
        chat_entity = MagicMock(id=100, title="mygroup")
        other_user = MagicMock(id=OTHER_ID, username="alice", first_name="Alice")
        messages = [
            _make_message(
                1,
                datetime(2026, 1, 15, tzinfo=timezone.utc),
                "spam from me",
                ME_ID,
            ),
            _make_message(
                2,
                datetime(2026, 1, 14, tzinfo=timezone.utc),
                "spam from alice",
                OTHER_ID,
            ),
        ]

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))

        async def get_entity_side_effect(name):
            if name in ("@alice", "alice"):
                return other_user
            return chat_entity

        client.get_entity = AsyncMock(side_effect=get_entity_side_effect)
        client.iter_messages = MagicMock(return_value=_async_iter(messages))

        result = await fetch_target_messages(
            client,
            chats=["mygroup"],
            keywords=["spam"],
            from_user="@alice",
        )

        assert _message_ids(result) == [2]

    @pytest.mark.asyncio
    async def test_outgoing_messages_count_as_from_me_when_sender_id_missing(self):
        chat_entity = MagicMock(id=100, title="mygroup")
        outgoing = _make_message(
            1,
            datetime(2026, 1, 15, tzinfo=timezone.utc),
            "alexander",
            None,
        )
        outgoing.out = True
        outgoing.sender_id = None

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter([outgoing]))

        result = await fetch_target_messages(
            client,
            chats=["mygroup"],
            keywords=["alexander"],
            everyone=False,
        )

        assert _message_ids(result) == [1]


class TestResolveChatEntity:
    @pytest.mark.asyncio
    async def test_resolves_chat_by_dialog_title_when_username_lookup_fails(self):
        dialog = _make_dialog(555, "TeamChat")
        dialog.name = "TeamChat"

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(side_effect=ValueError("not a username"))
        client.iter_dialogs = MagicMock(return_value=_async_iter([dialog]))
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        result = await fetch_target_messages(
            client,
            chats=["TeamChat"],
            keywords=["alexander"],
            everyone=False,
        )

        assert result == []
        client.iter_dialogs.assert_called_once()
        client.iter_messages.assert_called_once()
        assert client.iter_messages.call_args[0][0] is dialog.entity

    @pytest.mark.asyncio
    async def test_resolves_chat_by_partial_dialog_title(self):
        dialog = _make_dialog(555, "TeamChat")
        dialog.name = "TeamChat"

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(side_effect=ValueError("not a username"))
        client.iter_dialogs = MagicMock(return_value=_async_iter([dialog]))
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        await fetch_target_messages(
            client,
            chats=["team"],
            keywords=["alexander"],
            everyone=False,
        )

        assert client.iter_messages.call_args[0][0] is dialog.entity

    @pytest.mark.asyncio
    async def test_partial_chat_name_ambiguous_does_not_resolve(self, caplog):
        dialog_a = _make_dialog(101, "TeamChat")
        dialog_a.name = "TeamChat"
        dialog_b = _make_dialog(102, "TeamAlt")
        dialog_b.name = "TeamAlt"

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(side_effect=ValueError("not a username"))
        client.iter_dialogs = MagicMock(return_value=_async_iter([dialog_a, dialog_b]))
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        with caplog.at_level(logging.WARNING):
            await fetch_target_messages(
                client,
                chats=["team"],
                keywords=["alexander"],
                everyone=False,
            )

        client.iter_messages.assert_not_called()
        assert any("Multiple chats partially match" in record.message for record in caplog.records)


class TestResolveSearchChats:
    @pytest.mark.asyncio
    async def test_resolve_search_chats_returns_filtered_entities(self):
        from src.telegram_client import resolve_search_chats

        user = _tl_user(101, "Alice")
        group = _tl_chat(102, "GroupChat")
        channel = _tl_channel(103, "NewsChannel", megagroup=False)
        dialogs = [
            _make_typed_dialog(user),
            _make_typed_dialog(group),
            _make_typed_dialog(channel),
        ]

        client = MagicMock()
        client.iter_dialogs = MagicMock(return_value=_async_iter(dialogs))

        entities = await resolve_search_chats(
            client,
            chats=["all"],
            include_channels=False,
            include_group_chats=False,
        )

        assert entities == [user]
        client.iter_dialogs.assert_called_once()


class TestFilterEntitiesByExcludeChats:
    def test_partial_name_matches_display_not_username(self):
        named = _tl_user(1, "Alice")
        titled = _tl_chat(2, "Team Alice")
        combined = User(
            id=3, access_hash=0, first_name="Alice", last_name="Smith"
        )
        username_only = User(id=4, access_hash=0, username="alice_smith")

        kept = _filter_entities_by_exclude_chats(
            [named, titled, combined, username_only],
            ["alice"],
        )

        assert kept == [username_only]

    def test_at_username_matches_exact_username_only(self):
        exact = User(id=1, access_hash=0, username="alice")
        similar = User(id=2, access_hash=0, username="alice_bot")
        display = _tl_user(3, "Alice")

        kept = _filter_entities_by_exclude_chats(
            [exact, similar, display],
            ["@Alice"],
        )

        assert kept == [similar, display]

    def test_empty_and_at_only_patterns_are_ignored(self):
        chat = _tl_user(1, "Alice")

        kept = _filter_entities_by_exclude_chats([chat], ["", "@"])

        assert kept == [chat]

    def test_any_pattern_excludes_and_logs_skip_count(self, caplog):
        team = _tl_chat(1, "Team Chat")
        spam = User(id=2, access_hash=0, username="spamchannel")
        kept_chat = _tl_user(3, "Bob")

        with caplog.at_level(logging.INFO):
            kept = _filter_entities_by_exclude_chats(
                [team, spam, kept_chat],
                ["team", "@spamchannel"],
            )

        assert kept == [kept_chat]
        assert any(
            "Skipping 2 chats matching --exclude-chats" in record.message
            for record in caplog.records
        )


class TestFetchWithChatEntities:
    @pytest.mark.asyncio
    async def test_fetch_with_chat_entities_skips_resolve(self):
        entity = MagicMock(id=555, title="pre-resolved")
        msg = _make_message(
            1,
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            "spam here",
            ME_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.iter_dialogs = MagicMock(return_value=_async_iter([]))
        client.get_entity = AsyncMock()
        client.iter_messages = MagicMock(return_value=_async_iter([msg]))

        result = await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam"],
            chat_entities=[entity],
            everyone=False,
        )

        client.iter_dialogs.assert_not_called()
        client.get_entity.assert_not_called()
        assert client.iter_messages.call_count >= 1
        assert client.iter_messages.call_args_list[0].args[0] is entity
        assert _message_ids(result) == [1]


class TestFetchTargetMessagesAllChats:
    @pytest.mark.asyncio
    async def test_uses_iter_dialogs_and_never_search_global(self):
        dialog_a = _make_dialog(101, "A")
        dialog_b = _make_dialog(102, "B")
        msg_a = _make_message(
            1,
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            "spam in A",
            ME_ID,
        )
        msg_b = _make_message(
            2,
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            "spam in B",
            ME_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.iter_dialogs = MagicMock(return_value=_async_iter([dialog_a, dialog_b]))

        def iter_messages_side_effect(entity, **kwargs):
            if entity.id == 101:
                return _async_iter([msg_a])
            if entity.id == 102:
                return _async_iter([msg_b])
            return _async_iter([])

        client.iter_messages = MagicMock(side_effect=iter_messages_side_effect)

        search_global_calls = []

        async def track_call(request):
            if isinstance(request, SearchGlobalRequest):
                search_global_calls.append(request)
            return MagicMock()

        client.__call__ = AsyncMock(side_effect=track_call)

        result = await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam"],
            everyone=False,
            wait_seconds=0,
        )

        client.iter_dialogs.assert_called_once()
        assert client.iter_messages.call_count == 2
        assert client.iter_messages.call_args_list[0].args[0] is dialog_a.entity
        assert client.iter_messages.call_args_list[1].args[0] is dialog_b.entity
        assert _message_ids(result) == [1, 2]
        assert search_global_calls == []

    @pytest.mark.asyncio
    async def test_keeps_chats_separate_when_sorting_by_date(self):
        dialog_a = _make_dialog(101, "A")
        dialog_b = _make_dialog(102, "B")
        # Newer message in chat A; older message in chat B — chats must not mix.
        msg_a_new = _make_message(
            10,
            datetime(2026, 2, 10, tzinfo=timezone.utc),
            "spam in A",
            ME_ID,
        )
        msg_b_old = _make_message(
            20,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            "spam in B",
            ME_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.iter_dialogs = MagicMock(return_value=_async_iter([dialog_a, dialog_b]))

        def iter_messages_side_effect(entity, **kwargs):
            if entity.id == 101:
                return _async_iter([msg_a_new])
            if entity.id == 102:
                return _async_iter([msg_b_old])
            return _async_iter([])

        client.iter_messages = MagicMock(side_effect=iter_messages_side_effect)

        result = await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam"],
            everyone=False,
            wait_seconds=0,
        )

        assert _message_ids(result) == [10, 20]
        assert result[0][0] is dialog_a.entity
        assert result[1][0] is dialog_b.entity

    @pytest.mark.asyncio
    async def test_logs_search_progress_per_dialog(self, caplog):
        dialog_a = _make_dialog(101, "A")
        dialog_b = _make_dialog(102, "B")
        msg_a = _make_message(
            1,
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            "spam in A",
            ME_ID,
        )
        msg_b = _make_message(
            2,
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            "spam in B",
            ME_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.iter_dialogs = MagicMock(return_value=_async_iter([dialog_a, dialog_b]))

        def iter_messages_side_effect(entity, **kwargs):
            if entity.id == 101:
                return _async_iter([msg_a])
            if entity.id == 102:
                return _async_iter([msg_b])
            return _async_iter([])

        client.iter_messages = MagicMock(side_effect=iter_messages_side_effect)

        with caplog.at_level(logging.INFO):
            result = await fetch_target_messages(
                client,
                chats=["all"],
                keywords=["spam"],
                everyone=False,
                wait_seconds=0,
            )

        assert _message_ids(result) == [1, 2]
        log_text = " ".join(record.message for record in caplog.records)
        assert "Searching 1/2: A" in log_text
        assert "Searching 2/2: B" in log_text
        assert "Querying Telegram search" not in log_text
        assert "Search in " not in log_text


class TestFilterChatsByLastMessageAfter:
    """When --after is set, skip chats whose dialog.date is before that day."""

    @pytest.mark.asyncio
    async def test_chats_all_skips_dialogs_with_last_message_before_after(
        self, caplog
    ):
        after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        dialog_recent = _make_dialog(
            101,
            "Recent",
            date=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        dialog_old = _make_dialog(
            102,
            "Old",
            date=datetime(2023, 1, 1, tzinfo=timezone.utc),
        )
        msg = _make_message(
            1,
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            "spam here",
            ME_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.iter_dialogs = MagicMock(
            return_value=_async_iter([dialog_recent, dialog_old])
        )
        client.iter_messages = MagicMock(return_value=_async_iter([msg]))

        with caplog.at_level(logging.INFO):
            result = await fetch_target_messages(
                client,
                chats=["all"],
                keywords=["spam"],
                after=after,
                everyone=False,
                wait_seconds=0,
            )

        assert _searched_entity_ids(client) == [101]
        assert client.iter_messages.call_args_list[0].args[0] is dialog_recent.entity
        assert _message_ids(result) == [1]
        log_text = " ".join(record.message for record in caplog.records)
        assert "Skipping" in log_text
        assert "last message before" in log_text
        assert "2024-01-01" in log_text

    @pytest.mark.asyncio
    async def test_named_chat_skipped_when_dialog_date_before_after(self, caplog):
        after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        entity = MagicMock(id=555, title="OldChat")
        dialog = _make_dialog(
            555,
            "OldChat",
            date=datetime(2023, 1, 1, tzinfo=timezone.utc),
        )
        dialog.entity = entity

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=entity)
        client.iter_dialogs = MagicMock(
            side_effect=lambda *a, **k: _async_iter([dialog])
        )
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        with caplog.at_level(logging.INFO):
            await fetch_target_messages(
                client,
                chats=["OldChat"],
                keywords=["spam"],
                after=after,
                everyone=False,
                wait_seconds=0,
            )

        client.iter_messages.assert_not_called()
        log_text = " ".join(record.message for record in caplog.records)
        assert "Skipping" in log_text
        assert "last message before" in log_text
        assert "2024-01-01" in log_text

    @pytest.mark.asyncio
    async def test_named_chat_not_in_dialog_map_still_searched(self):
        after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        entity = MagicMock(id=555, title="OrphanChat")
        unrelated = _make_dialog(
            999,
            "Other",
            date=datetime(2023, 1, 1, tzinfo=timezone.utc),
        )
        msg = _make_message(
            1,
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            "spam here",
            ME_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=entity)
        client.iter_dialogs = MagicMock(
            side_effect=lambda *a, **k: _async_iter([unrelated])
        )
        client.iter_messages = MagicMock(return_value=_async_iter([msg]))

        result = await fetch_target_messages(
            client,
            chats=["OrphanChat"],
            keywords=["spam"],
            after=after,
            everyone=False,
            wait_seconds=0,
        )

        client.iter_dialogs.assert_called()
        assert client.iter_messages.call_count >= 1
        assert client.iter_messages.call_args_list[0].args[0] is entity
        assert _message_ids(result) == [1]

    @pytest.mark.asyncio
    async def test_without_after_searches_all_dialogs(self):
        dialog_a = _make_dialog(
            101,
            "A",
            date=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        dialog_b = _make_dialog(
            102,
            "B",
            date=datetime(2023, 1, 1, tzinfo=timezone.utc),
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.iter_dialogs = MagicMock(
            return_value=_async_iter([dialog_a, dialog_b])
        )
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam"],
            everyone=False,
            wait_seconds=0,
        )

        assert _searched_entity_ids(client) == [101, 102]


class TestFetchTargetMessagesDialogTypeFilter:
    """--chats all defaults to private User dialogs; channels/groups are opt-in."""

    def _three_typed_dialogs(self):
        user = _tl_user(101, "Alice")
        group = _tl_chat(102, "GroupChat")
        channel = _tl_channel(103, "NewsChannel", megagroup=False)
        return (
            _make_typed_dialog(user),
            _make_typed_dialog(group),
            _make_typed_dialog(channel),
            user,
            group,
            channel,
        )

    def _client_with_dialogs(self, dialogs):
        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.iter_dialogs = MagicMock(return_value=_async_iter(dialogs))
        client.iter_messages = MagicMock(return_value=_async_iter([]))
        return client

    @pytest.mark.asyncio
    async def test_chats_all_default_searches_only_users(self, caplog):
        dialog_user, dialog_group, dialog_channel, user, group, channel = (
            self._three_typed_dialogs()
        )
        client = self._client_with_dialogs(
            [dialog_user, dialog_group, dialog_channel]
        )

        with caplog.at_level(logging.INFO):
            await fetch_target_messages(
                client,
                chats=["all"],
                keywords=["spam"],
                everyone=False,
                include_channels=False,
                include_group_chats=False,
            )

        assert _searched_entity_ids(client) == [user.id]
        assert client.iter_messages.call_args_list[0].args[0] is user
        log_text = " ".join(record.message for record in caplog.records)
        assert "Skipping 2 chats" in log_text
        assert "1 group" in log_text
        assert "1 channel" in log_text
        assert "--group-chats" in log_text
        assert "--channels" in log_text
        assert "Skipping group" not in log_text
        assert "Skipping channel" not in log_text

    @pytest.mark.asyncio
    async def test_include_channels_adds_broadcast_channels(self):
        dialog_user, dialog_group, dialog_channel, user, group, channel = (
            self._three_typed_dialogs()
        )
        client = self._client_with_dialogs(
            [dialog_user, dialog_group, dialog_channel]
        )

        await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam"],
            everyone=False,
            include_channels=True,
            include_group_chats=False,
            wait_seconds=0,
        )

        assert _searched_entity_ids(client) == [channel.id]
        searched = [call.args[0] for call in client.iter_messages.call_args_list]
        assert group not in searched
        assert user not in searched

    @pytest.mark.asyncio
    async def test_include_group_chats_adds_groups(self):
        dialog_user, dialog_group, dialog_channel, user, group, channel = (
            self._three_typed_dialogs()
        )
        client = self._client_with_dialogs(
            [dialog_user, dialog_group, dialog_channel]
        )

        await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam"],
            everyone=False,
            include_channels=False,
            include_group_chats=True,
            wait_seconds=0,
        )

        assert _searched_entity_ids(client) == [group.id]
        searched = [call.args[0] for call in client.iter_messages.call_args_list]
        assert channel not in searched
        assert user not in searched

    @pytest.mark.asyncio
    async def test_both_flags_include_all_types(self):
        dialog_user, dialog_group, dialog_channel, user, group, channel = (
            self._three_typed_dialogs()
        )
        client = self._client_with_dialogs(
            [dialog_user, dialog_group, dialog_channel]
        )

        await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam"],
            everyone=False,
            include_channels=True,
            include_group_chats=True,
            wait_seconds=0,
        )

        assert _searched_entity_ids(client) == [group.id, channel.id]

    @pytest.mark.asyncio
    async def test_include_group_chats_adds_megagroup_channels(self):
        user = _tl_user(201, "Bob")
        megagroup = _tl_channel(202, "SuperGroup", megagroup=True)
        broadcast = _tl_channel(203, "BroadcastOnly", megagroup=False)
        client = self._client_with_dialogs(
            [
                _make_typed_dialog(user),
                _make_typed_dialog(megagroup),
                _make_typed_dialog(broadcast),
            ]
        )

        await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam"],
            everyone=False,
            include_channels=False,
            include_group_chats=True,
            wait_seconds=0,
        )

        assert _searched_entity_ids(client) == [megagroup.id]

    @pytest.mark.asyncio
    async def test_named_channel_skipped_when_include_channels_false(self, caplog):
        channel = _tl_channel(300, "NamedNews", megagroup=False)
        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=channel)
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        with caplog.at_level(logging.INFO):
            await fetch_target_messages(
                client,
                chats=["NamedNews"],
                keywords=["spam"],
                everyone=False,
                include_channels=False,
                include_group_chats=False,
            )

        client.iter_messages.assert_not_called()
        log_text = " ".join(record.message for record in caplog.records)
        assert "Skipping 1 chat" in log_text
        assert "1 channel" in log_text
        assert "--channels" in log_text
        assert "Skipping channel" not in log_text

    @pytest.mark.asyncio
    async def test_named_group_skipped_when_include_group_chats_false(self, caplog):
        group = _tl_chat(301, "NamedGroup")
        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=group)
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        with caplog.at_level(logging.INFO):
            await fetch_target_messages(
                client,
                chats=["NamedGroup"],
                keywords=["spam"],
                everyone=False,
                include_channels=False,
                include_group_chats=False,
            )

        client.iter_messages.assert_not_called()
        log_text = " ".join(record.message for record in caplog.records)
        assert "Skipping 1 chat" in log_text
        assert "1 group" in log_text
        assert "--group-chats" in log_text
        assert "Skipping group" not in log_text

    @pytest.mark.asyncio
    async def test_channels_flag_excludes_private_chats(self, caplog):
        dialog_user, dialog_group, dialog_channel, user, group, channel = (
            self._three_typed_dialogs()
        )
        client = self._client_with_dialogs(
            [dialog_user, dialog_group, dialog_channel]
        )

        with caplog.at_level(logging.INFO):
            await fetch_target_messages(
                client,
                chats=["all"],
                keywords=["spam"],
                everyone=False,
                include_channels=True,
                include_group_chats=False,
                wait_seconds=0,
            )

        assert _searched_entity_ids(client) == [channel.id]
        log_text = " ".join(record.message for record in caplog.records)
        assert "private chat" in log_text
        assert "1 group" in log_text


class TestFetchTargetMessagesDateFiltering:
    @pytest.mark.asyncio
    async def test_before_is_inclusive_for_the_given_day(self):
        chat_entity = MagicMock(id=100, title="dated")
        before = datetime(2026, 8, 14, tzinfo=timezone.utc)
        messages = [
            _make_message(
                1,
                datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc),
                "same day spam",
                ME_ID,
            ),
            _make_message(
                2,
                datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc),
                "next day spam",
                ME_ID,
            ),
        ]

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter(messages))

        result = await fetch_target_messages(
            client,
            chats=["dated"],
            keywords=["spam"],
            before=before,
            everyone=False,
        )

        assert _message_ids(result) == [1]
        _, kwargs = client.iter_messages.call_args
        assert kwargs["offset_date"] == datetime(2026, 8, 15, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_skips_messages_before_after_and_stops_iteration(self):
        chat_entity = MagicMock(id=100, title="dated")
        after = datetime(2026, 1, 10, tzinfo=timezone.utc)
        messages = [
            _make_message(
                1,
                datetime(2026, 1, 15, tzinfo=timezone.utc),
                "recent spam",
                ME_ID,
            ),
            _make_message(
                2,
                datetime(2026, 1, 9, tzinfo=timezone.utc),
                "too old spam",
                ME_ID,
            ),
            _make_message(
                3,
                datetime(2026, 1, 8, tzinfo=timezone.utc),
                "even older spam",
                ME_ID,
            ),
        ]

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter(messages))

        result = await fetch_target_messages(
            client,
            chats=["dated"],
            keywords=["spam"],
            after=after,
            everyone=False,
        )

        assert _message_ids(result) == [1]


class TestDeleteMessagesBatch:
    @pytest.mark.asyncio
    async def test_chunks_ids_into_groups_of_100_with_revoke_true(self):
        chat = MagicMock(id=100)
        client = AsyncMock()
        message_ids = list(range(1, 251))

        await delete_messages_batch(client, chat, message_ids, wait_seconds=0)

        assert client.delete_messages.await_count == 3
        actual_chunks = [
            call.args[1] for call in client.delete_messages.await_args_list
        ]
        assert actual_chunks == [
            list(range(1, 101)),
            list(range(101, 201)),
            list(range(201, 251)),
        ]
        for call in client.delete_messages.await_args_list:
            assert call.kwargs.get("revoke") is True


class TestWaitSecondsPacing:
    @pytest.mark.asyncio
    async def test_iter_messages_passes_wait_time(self):
        chat_entity = MagicMock(id=100, title="mygroup")
        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        await fetch_target_messages(
            client,
            chats=["mygroup"],
            keywords=["spam"],
            everyone=False,
            wait_seconds=1.0,
        )

        assert client.iter_messages.call_count >= 1
        for call in client.iter_messages.call_args_list:
            wait_time = call.kwargs.get("wait_time")
            assert wait_time in (1, 1.0), (
                f"expected wait_time=1 or 1.0, got {wait_time!r}"
            )

    @pytest.mark.asyncio
    async def test_pauses_between_search_terms(self, monkeypatch):
        chat_entity = MagicMock(id=100, title="mygroup")
        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.get_entity = AsyncMock(return_value=chat_entity)
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        pause_mock = AsyncMock()
        monkeypatch.setattr(
            "src.telegram_client.pause_for_telegram",
            pause_mock,
            raising=False,
        )

        await fetch_target_messages(
            client,
            chats=["mygroup"],
            keywords=["spam", "trash"],
            everyone=False,
            wait_seconds=1.0,
        )

        assert client.iter_messages.call_count == 2
        pause_mock.assert_awaited_once_with(1.0)

    @pytest.mark.asyncio
    async def test_pauses_between_chats(self, monkeypatch):
        dialog_a = _make_dialog(101, "A")
        dialog_b = _make_dialog(102, "B")

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.iter_dialogs = MagicMock(
            return_value=_async_iter([dialog_a, dialog_b])
        )
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        pause_mock = AsyncMock()
        monkeypatch.setattr(
            "src.telegram_client.pause_for_telegram",
            pause_mock,
            raising=False,
        )

        await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam"],
            everyone=False,
            wait_seconds=1.0,
        )

        assert client.iter_messages.call_count == 2
        pause_mock.assert_awaited_once_with(1.0)

    @pytest.mark.asyncio
    async def test_pauses_between_delete_chunks(self, monkeypatch):
        chat = MagicMock(id=100)
        client = AsyncMock()
        message_ids = list(range(200))

        pause_mock = AsyncMock()
        monkeypatch.setattr(
            "src.telegram_client.pause_for_telegram",
            pause_mock,
            raising=False,
        )

        await delete_messages_batch(
            client, chat, message_ids, wait_seconds=1.0
        )

        assert client.delete_messages.await_count == 2
        pause_mock.assert_awaited_once_with(1.0)

    @pytest.mark.asyncio
    async def test_wait_seconds_zero_skips_proactive_pauses(self, monkeypatch):
        dialog_a = _make_dialog(101, "A")
        dialog_b = _make_dialog(102, "B")

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))
        client.iter_dialogs = MagicMock(
            return_value=_async_iter([dialog_a, dialog_b])
        )
        client.iter_messages = MagicMock(return_value=_async_iter([]))

        pause_mock = AsyncMock()
        monkeypatch.setattr(
            "src.telegram_client.pause_for_telegram",
            pause_mock,
            raising=False,
        )

        await fetch_target_messages(
            client,
            chats=["all"],
            keywords=["spam", "trash"],
            everyone=False,
            wait_seconds=0,
        )

        pause_mock.assert_not_called()
        assert client.iter_messages.call_count == 4
        for call in client.iter_messages.call_args_list:
            wait_time = call.kwargs.get("wait_time")
            assert wait_time in (0, 0.0), (
                f"expected wait_time=0 or 0.0, got {wait_time!r}"
            )


class TestFetchTargetMessagesAccessErrors:
    @pytest.mark.asyncio
    async def test_channel_private_error_logs_warning_and_skips_chat(self, caplog):
        good_entity = MagicMock(id=200, title="good")
        good_message = _make_message(
            1,
            datetime(2026, 1, 15, tzinfo=timezone.utc),
            "spam",
            ME_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))

        async def get_entity_side_effect(name):
            if name == "private":
                raise ChannelPrivateError(request=MagicMock())
            return good_entity

        client.get_entity = AsyncMock(side_effect=get_entity_side_effect)
        client.iter_messages = MagicMock(return_value=_async_iter([good_message]))

        with caplog.at_level(logging.WARNING):
            result = await fetch_target_messages(
                client,
                chats=["private", "good"],
                keywords=["spam"],
                everyone=False,
            )

        assert _message_ids(result) == [1]
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    @pytest.mark.asyncio
    async def test_chat_admin_required_error_skips_chat(self):
        good_entity = MagicMock(id=200, title="good")
        blocked_entity = MagicMock(id=300, title="blocked")
        good_message = _make_message(
            1,
            datetime(2026, 1, 15, tzinfo=timezone.utc),
            "spam",
            ME_ID,
        )

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=ME_ID))

        async def get_entity_side_effect(name):
            if name == "blocked":
                return blocked_entity
            return good_entity

        client.get_entity = AsyncMock(side_effect=get_entity_side_effect)

        def iter_messages_side_effect(entity, **kwargs):
            if entity.id == 300:
                raise ChatAdminRequiredError(request=MagicMock())
            return _async_iter([good_message])

        client.iter_messages = MagicMock(side_effect=iter_messages_side_effect)

        result = await fetch_target_messages(
            client,
            chats=["blocked", "good"],
            keywords=["spam"],
            everyone=False,
            wait_seconds=0,
        )

        assert _message_ids(result) == [1]
