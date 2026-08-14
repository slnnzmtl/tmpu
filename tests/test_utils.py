"""Tests for utility helpers in src.utils."""

import asyncio
import logging
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import FloodWaitError

from src.utils import (
    chunk_list,
    confirm_deletion,
    confirm_search,
    expected_search_count,
    message_matches_keywords,
    name_matches_query,
    pause_for_telegram,
    setup_logging,
    with_flood_retry,
)


class TestChunkList:
    def test_empty_list_returns_empty(self):
        assert chunk_list([]) == []

    def test_list_shorter_than_size_returns_single_chunk(self):
        assert chunk_list([1, 2, 3]) == [[1, 2, 3]]

    def test_list_exactly_size_returns_single_chunk(self):
        items = list(range(100))
        assert chunk_list(items) == [items]

    def test_list_longer_than_default_size_splits_into_chunks_of_100(self):
        items = list(range(250))
        chunks = chunk_list(items)

        assert len(chunks) == 3
        assert len(chunks[0]) == 100
        assert len(chunks[1]) == 100
        assert len(chunks[2]) == 50
        assert chunks[0] + chunks[1] + chunks[2] == items

    def test_custom_size_splits_correctly(self):
        assert chunk_list([1, 2, 3, 4, 5], size=2) == [[1, 2], [3, 4], [5]]


class TestMessageMatchesKeywords:
    def test_empty_keywords_always_matches(self):
        assert message_matches_keywords("hello", "world", []) is True
        assert message_matches_keywords("", "", []) is True

    def test_matches_keyword_in_text_case_insensitive(self):
        assert message_matches_keywords("Hello SPAM world", None, ["spam"]) is True
        assert message_matches_keywords("HELLO", None, ["hello"]) is True

    def test_matches_keyword_in_caption_case_insensitive(self):
        assert message_matches_keywords(None, "Photo with Trash caption", ["trash"]) is True

    def test_does_not_match_when_keyword_absent_from_text_and_caption(self):
        assert message_matches_keywords("hello", "world", ["spam"]) is False

    def test_matches_any_keyword_in_list(self):
        assert message_matches_keywords("nothing here", "but garbage", ["spam", "garbage"]) is True

    def test_partial_stem_matches_inflected_word(self):
        assert message_matches_keywords("meeting with alexandra", None, ["alexander"]) is True

    def test_partial_stem_matches_different_case_ending(self):
        assert message_matches_keywords("wrote alexandrum yesterday", None, ["alexander"]) is True

    def test_single_short_keyword_still_matches_exactly(self):
        assert message_matches_keywords("say hi", None, ["hi"]) is True

    def test_broad_search_term_trims_suffix(self):
        from src.utils import broad_search_term, search_terms

        assert broad_search_term("alexander") == "alexand"
        assert search_terms(["alexander", "alexandra", "alexandrum"]) == ["alexand"]


class TestNameMatching:
    def test_name_matches_query_partial_case_insensitive(self):
        assert name_matches_query("team", ["TeamChat"]) is True
        assert name_matches_query("ALICE", ["Alice Smith"]) is True

    def test_name_matches_query_requires_non_empty_query(self):
        assert name_matches_query("", ["TeamChat"]) is False


class TestConfirmDeletion:
    def test_returns_true_for_exact_delete(self):
        assert confirm_deletion("DELETE") is True

    def test_returns_false_for_wrong_input(self):
        assert confirm_deletion("delete") is False
        assert confirm_deletion("DELETE ") is False
        assert confirm_deletion("YES") is False

    def test_returns_false_for_empty_input(self):
        assert confirm_deletion("") is False

    def test_returns_false_for_eof(self):
        assert confirm_deletion(None) is False


class TestExpectedSearchCount:
    def test_zero_chats_returns_zero(self):
        assert expected_search_count(0, ["spam"]) == 0

    def test_counts_chats_times_distinct_keywords(self):
        assert expected_search_count(3, ["spam", "trash"]) == 6

    def test_no_keywords_means_one_history_scan_per_chat(self):
        assert expected_search_count(3, None) == 3
        assert expected_search_count(3, []) == 3

    def test_inflected_keywords_collapse_to_one_search_term_per_chat(self):
        assert expected_search_count(3, ["alexander", "alexandra", "alexandrum"]) == 3


class TestConfirmSearch:
    def test_returns_true_for_yes_variants(self):
        assert confirm_search("y") is True
        assert confirm_search("yes") is True
        assert confirm_search("Y") is True
        assert confirm_search("YES") is True
        assert confirm_search(" Yes ") is True

    def test_returns_false_for_non_yes_input(self):
        assert confirm_search("n") is False
        assert confirm_search("no") is False
        assert confirm_search("") is False
        assert confirm_search("DELETE") is False
        assert confirm_search(None) is False
        assert confirm_search("yeah") is False


class TestSetupLogging:
    def test_returns_logger_instance(self):
        logger = setup_logging()
        assert isinstance(logger, logging.Logger)

    def test_logs_to_stderr_at_info_level(self):
        logger = setup_logging()

        stderr_handlers = [
            handler
            for handler in logger.handlers
            if isinstance(handler, logging.StreamHandler)
            and handler.stream is sys.stderr
        ]

        assert stderr_handlers, "expected a StreamHandler writing to stderr"
        assert stderr_handlers[0].level == logging.INFO

    def test_stderr_handler_has_minimal_formatter(self):
        logger = setup_logging()

        stderr_handlers = [
            handler
            for handler in logger.handlers
            if isinstance(handler, logging.StreamHandler)
            and handler.stream is sys.stderr
        ]

        assert stderr_handlers, "expected a StreamHandler writing to stderr"
        formatter = stderr_handlers[0].formatter
        assert formatter is not None, "stderr handler must have a Formatter"
        fmt = formatter._fmt or ""
        assert fmt == "%(message)s"
        assert "asctime" not in fmt
        assert "name" not in fmt

    def test_child_logger_info_appears_on_stderr_without_prefix(self, capsys):
        setup_logging()

        client_logger = logging.getLogger("tmpu.telegram_client")
        client_logger.info("client visibility probe")

        captured = capsys.readouterr()
        assert "client visibility probe" in captured.err
        assert "tmpu.telegram_client" not in captured.err
        assert "INFO" not in captured.err.split("client visibility probe")[0]


class TestWithFloodRetry:
    @pytest.mark.asyncio
    async def test_returns_result_without_sleep_on_success(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        @with_flood_retry
        async def succeed():
            return "ok"

        result = await succeed()

        assert result == "ok"
        sleep_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_sleeps_and_retries_on_flood_wait_error(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        call_count = 0

        @with_flood_retry
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FloodWaitError(request=MagicMock(), capture=42)
            return "recovered"

        result = await flaky()

        assert result == "recovered"
        assert call_count == 2
        sleep_mock.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_logs_warning_with_wait_seconds_on_flood_wait_error(
        self, monkeypatch, caplog
    ):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        call_count = 0

        @with_flood_retry
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FloodWaitError(request=MagicMock(), capture=42)
            return "recovered"

        with caplog.at_level(logging.WARNING):
            result = await flaky()

        assert result == "recovered"
        sleep_mock.assert_awaited_once_with(42)
        assert any(
            record.levelno == logging.WARNING and "42" in record.getMessage()
            for record in caplog.records
        ), "expected a WARNING log including the FloodWait wait seconds (42)"

    @pytest.mark.asyncio
    async def test_retries_up_to_five_times_then_reraises(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        call_count = 0

        @with_flood_retry
        async def always_flood():
            nonlocal call_count
            call_count += 1
            raise FloodWaitError(request=MagicMock(), capture=3)

        with pytest.raises(FloodWaitError):
            await always_flood()

        assert call_count == 6
        assert sleep_mock.await_count == 5

    @pytest.mark.asyncio
    async def test_does_not_retry_non_flood_errors(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        call_count = 0

        @with_flood_retry
        async def other_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await other_error()

        assert call_count == 1
        sleep_mock.assert_not_called()
