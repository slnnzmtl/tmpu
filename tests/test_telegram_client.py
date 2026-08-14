"""Tests for Telegram client helpers in src.telegram_client."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telethon.errors import ChannelPrivateError, ChatAdminRequiredError
from telethon.tl.functions.messages import SearchGlobalRequest

from src.config import Config
from src.telegram_client import create_client, delete_messages_batch, fetch_target_messages

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


def _make_dialog(entity_id: int, title: str = "chat") -> MagicMock:
    dialog = MagicMock()
    dialog.entity = MagicMock(id=entity_id, title=title)
    return dialog


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
        )

        assert _message_ids(result) == [1, 3]
        client.get_entity.assert_awaited_once_with("mygroup")
        client.iter_messages.assert_called_once()
        assert client.iter_messages.call_args[0][0] is chat_entity

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

        assert _message_ids(result) == [1, 2]


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
        )

        client.iter_dialogs.assert_called_once()
        assert client.iter_messages.call_count == 2
        assert client.iter_messages.call_args_list[0].args[0] is dialog_a.entity
        assert client.iter_messages.call_args_list[1].args[0] is dialog_b.entity
        assert _message_ids(result) == [1, 2]
        assert search_global_calls == []


class TestFetchTargetMessagesDateFiltering:
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

        await delete_messages_batch(client, chat, message_ids)

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
        )

        assert _message_ids(result) == [1]
