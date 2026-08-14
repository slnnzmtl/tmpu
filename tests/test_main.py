"""Tests for main.py orchestration."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cli import CliArgs
from src.config import Config

from main import _group_message_ids_by_chat, run_purge


class UnhashableChat:
    """Mimics Telethon User/Chat entities, which are not dict-key-safe."""

    def __init__(self, chat_id: int) -> None:
        self.id = chat_id

    def __hash__(self) -> int:
        raise TypeError("unhashable type: 'User'")


@pytest.fixture
def sample_config() -> Config:
    return Config(
        api_id=1,
        api_hash="test-hash",
        phone_number="+15551234567",
        session_name="tmpu",
    )


@pytest.fixture
def dry_run_args() -> CliArgs:
    return CliArgs(
        chats=["chat1"],
        keywords=None,
        after=None,
        before=None,
        everyone=False,
        from_user=None,
        dry_run=True,
        force=False,
        channels=False,
        group_chats=False,
        no_confirmation=False,
        wait_seconds=1.0,
    )


@pytest.fixture
def force_args() -> CliArgs:
    return CliArgs(
        chats=["chat1"],
        keywords=None,
        after=None,
        before=None,
        everyone=False,
        from_user=None,
        dry_run=False,
        force=True,
        channels=False,
        group_chats=False,
        no_confirmation=False,
        wait_seconds=1.0,
    )


@pytest.fixture
def sample_messages() -> list[tuple]:
    chat = MagicMock()
    msg1 = MagicMock()
    msg1.id = 101
    msg1.text = "hello spam"
    msg2 = MagicMock()
    msg2.id = 102
    msg2.text = "goodbye spam"
    return [(chat, msg1), (chat, msg2)]


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.disconnect = AsyncMock()
    client.get_me = AsyncMock(return_value=MagicMock(id=12345))
    return client


@pytest.fixture
def env_path(tmp_path: Path) -> Path:
    return tmp_path / ".env"


class TestRunPurgeConnection:
    @pytest.mark.asyncio
    async def test_run_purge_exits_cleanly_on_connection_failure(
        self,
        dry_run_args: CliArgs,
        sample_config: Config,
        mock_client: AsyncMock,
        env_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            patch("main.parse_args", return_value=dry_run_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                side_effect=TimeoutError("Connection to Telegram failed 6 time(s)"),
            ),
            caplog.at_level("INFO"),
        ):
            await run_purge(argv=["--chats", "chat1"], env_path=env_path)

        messages = [record.getMessage() for record in caplog.records]
        assert any("Connecting to Telegram..." in msg for msg in messages)
        error_messages = [
            record.getMessage()
            for record in caplog.records
            if record.levelname == "ERROR"
        ]
        assert error_messages, "expected connection ERROR log"
        assert "Could not connect to Telegram" in error_messages[0]
        assert "Check network/VPN/firewall" in error_messages[0]
        mock_client.disconnect.assert_not_awaited()


class TestRunPurgeDryRun:
    @pytest.mark.asyncio
    async def test_default_dry_run_fetches_and_logs_without_deleting(
        self,
        dry_run_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        resolved_chats = [sample_messages[0][0]]

        with (
            patch("main.parse_args", return_value=dry_run_args) as parse_args_mock,
            patch("main.load_config", return_value=sample_config) as load_config_mock,
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ) as create_client_mock,
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ) as resolve_mock,
            patch("main.confirm_search", return_value=True, create=True) as confirm_search_mock,
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ) as fetch_mock,
            patch(
                "main.delete_messages_batch",
                new_callable=AsyncMock,
            ) as delete_mock,
            patch("main.confirm_deletion", return_value=False) as confirm_mock,
        ):
            await run_purge(argv=["--chats", "chat1"], env_path=env_path)

        parse_args_mock.assert_called_once_with(["--chats", "chat1"])
        load_config_mock.assert_called_once_with(env_path)
        create_client_mock.assert_awaited_once_with(sample_config)
        resolve_mock.assert_awaited_once_with(
            mock_client,
            dry_run_args.chats,
            include_channels=dry_run_args.channels,
            include_group_chats=dry_run_args.group_chats,
            after=dry_run_args.after,
        )
        confirm_search_mock.assert_called_once()
        fetch_mock.assert_awaited_once_with(
            mock_client,
            dry_run_args.chats,
            dry_run_args.keywords,
            dry_run_args.after,
            dry_run_args.before,
            dry_run_args.everyone,
            dry_run_args.from_user,
            include_channels=dry_run_args.channels,
            include_group_chats=dry_run_args.group_chats,
            chat_entities=resolved_chats,
            wait_seconds=dry_run_args.wait_seconds,
        )
        delete_mock.assert_not_awaited()
        confirm_mock.assert_not_called()
        mock_client.disconnect.assert_awaited_once()

        preview_records = [
            record
            for record in caplog.records
            if record.levelname == "INFO" and "101" in record.getMessage()
        ]
        assert preview_records, "expected dry-run preview log for message 101"

    @pytest.mark.asyncio
    async def test_passes_after_datetime_to_resolve_search_chats(
        self,
        dry_run_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        dry_run_args.after = after
        resolved_chats = [sample_messages[0][0]]

        with (
            patch("main.parse_args", return_value=dry_run_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ) as resolve_mock,
            patch("main.confirm_search", return_value=True, create=True),
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ),
            patch("main.delete_messages_batch", new_callable=AsyncMock),
        ):
            await run_purge(argv=["--chats", "chat1", "--after", "2024-01-01"], env_path=env_path)

        resolve_mock.assert_awaited_once_with(
            mock_client,
            dry_run_args.chats,
            include_channels=dry_run_args.channels,
            include_group_chats=dry_run_args.group_chats,
            after=after,
        )


class TestRunPurgeSearchConfirmation:
    @pytest.mark.asyncio
    async def test_search_aborts_when_user_declines_confirmation(
        self,
        dry_run_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        resolved_chats = [sample_messages[0][0]]

        with (
            patch("main.parse_args", return_value=dry_run_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ),
            patch("main.confirm_search", return_value=False, create=True),
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ) as fetch_mock,
            patch("main.delete_messages_batch", new_callable=AsyncMock),
        ):
            await run_purge(argv=["--chats", "chat1"], env_path=env_path)

        fetch_mock.assert_not_awaited()
        mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_aborts_when_stdin_not_tty(
        self,
        dry_run_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        resolved_chats = [sample_messages[0][0]]

        with (
            patch("main.parse_args", return_value=dry_run_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ),
            patch("main.confirm_search", return_value=True, create=True) as confirm_search_mock,
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ) as fetch_mock,
            patch("main.delete_messages_batch", new_callable=AsyncMock),
        ):
            await run_purge(argv=["--chats", "chat1"], env_path=env_path)

        confirm_search_mock.assert_not_called()
        fetch_mock.assert_not_awaited()
        mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_confirmation_skips_prompt(
        self,
        dry_run_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
    ) -> None:
        dry_run_args.no_confirmation = True
        resolved_chats = [sample_messages[0][0]]

        with (
            patch("main.parse_args", return_value=dry_run_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ),
            patch("main.confirm_search", return_value=True, create=True) as confirm_search_mock,
            patch("builtins.input", side_effect=AssertionError("input should not be called")) as input_mock,
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ) as fetch_mock,
            patch("main.delete_messages_batch", new_callable=AsyncMock),
        ):
            await run_purge(
                argv=["--chats", "chat1", "--no-confirmation"],
                env_path=env_path,
            )

        confirm_search_mock.assert_not_called()
        input_mock.assert_not_called()
        fetch_mock.assert_awaited_once_with(
            mock_client,
            dry_run_args.chats,
            dry_run_args.keywords,
            dry_run_args.after,
            dry_run_args.before,
            dry_run_args.everyone,
            dry_run_args.from_user,
            include_channels=dry_run_args.channels,
            include_group_chats=dry_run_args.group_chats,
            chat_entities=resolved_chats,
            wait_seconds=dry_run_args.wait_seconds,
        )
        mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_logs_candidate_and_search_counts(
        self,
        dry_run_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        dry_run_args.keywords = ["spam", "trash"]
        resolved_chats = [MagicMock(), MagicMock()]

        with (
            patch("main.parse_args", return_value=dry_run_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ),
            patch("main.confirm_search", return_value=True, create=True),
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ),
            patch("main.delete_messages_batch", new_callable=AsyncMock),
        ):
            await run_purge(argv=["--chats", "chat1,chat2", "--keywords", "spam,trash"], env_path=env_path)

        messages = [record.getMessage() for record in caplog.records if record.levelname == "INFO"]
        assert any("Resolving candidate chats" in msg for msg in messages)
        about_records = [msg for msg in messages if "About to search" in msg]
        assert about_records, "expected About to search log with candidate/search counts"
        message = about_records[0]
        assert "2 chats" in message
        assert "2 search terms" in message
        assert "4 Telegram searches" in message
        resolve_idx = next(i for i, msg in enumerate(messages) if "Resolving candidate chats" in msg)
        about_idx = next(i for i, msg in enumerate(messages) if "About to search" in msg)
        assert resolve_idx < about_idx

    @pytest.mark.asyncio
    async def test_empty_candidates_skips_fetch_and_prompt(
        self,
        dry_run_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        with (
            patch("main.parse_args", return_value=dry_run_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=[],
                create=True,
            ),
            patch("main.confirm_search", return_value=True, create=True) as confirm_search_mock,
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ) as fetch_mock,
            patch("main.delete_messages_batch", new_callable=AsyncMock),
        ):
            await run_purge(argv=["--chats", "chat1"], env_path=env_path)

        confirm_search_mock.assert_not_called()
        fetch_mock.assert_not_awaited()
        mock_client.disconnect.assert_awaited_once()


class TestRunPurgeForce:
    @pytest.mark.asyncio
    async def test_force_without_confirmation_does_not_delete(
        self,
        force_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        resolved_chats = [sample_messages[0][0]]

        with (
            patch("main.parse_args", return_value=force_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ),
            patch("main.confirm_search", return_value=True, create=True),
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ),
            patch(
                "main.delete_messages_batch",
                new_callable=AsyncMock,
            ) as delete_mock,
            patch("main.confirm_deletion", return_value=False) as confirm_mock,
            caplog.at_level("INFO"),
        ):
            await run_purge(argv=["--chats", "chat1", "--force"], env_path=env_path)

        confirm_mock.assert_called_once()
        delete_mock.assert_not_awaited()
        mock_client.disconnect.assert_awaited_once()
        assert any(
            "Deletion aborted" in record.getMessage() for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_force_with_confirmation_deletes_messages(
        self,
        force_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        chat, _msg1, _msg2 = sample_messages[0][0], sample_messages[0][1], sample_messages[1][1]
        chat.title = "TeamChat"
        resolved_chats = [chat]

        with (
            patch("main.parse_args", return_value=force_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ),
            patch("main.confirm_search", return_value=True, create=True),
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ),
            patch(
                "main.delete_messages_batch",
                new_callable=AsyncMock,
            ) as delete_mock,
            patch("main.confirm_deletion", return_value=True) as confirm_mock,
            caplog.at_level("INFO"),
        ):
            await run_purge(argv=["--chats", "chat1", "--force"], env_path=env_path)

        confirm_mock.assert_called_once()
        delete_mock.assert_awaited_once_with(
            mock_client,
            chat,
            [101, 102],
            wait_seconds=force_args.wait_seconds,
        )
        mock_client.disconnect.assert_awaited_once()
        messages = [record.getMessage() for record in caplog.records]
        assert any("Deleting 2 message(s) across 1 chat(s)..." in msg for msg in messages)
        assert any("Deleting 1/1: TeamChat (2 message(s))" in msg for msg in messages)
        assert any("Successfully deleted 2 message(s)" in msg for msg in messages)

    @pytest.mark.asyncio
    async def test_force_with_zero_matches_skips_deletion_prompt(
        self,
        force_args: CliArgs,
        sample_config: Config,
        mock_client: AsyncMock,
        env_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        resolved_chats = [MagicMock()]

        with (
            patch("main.parse_args", return_value=force_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ),
            patch("main.confirm_search", return_value=True, create=True),
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "main.delete_messages_batch",
                new_callable=AsyncMock,
            ) as delete_mock,
            patch("main.confirm_deletion", return_value=True) as confirm_mock,
            caplog.at_level("INFO"),
        ):
            await run_purge(argv=["--chats", "chat1", "--force"], env_path=env_path)

        confirm_mock.assert_not_called()
        delete_mock.assert_not_awaited()
        mock_client.disconnect.assert_awaited_once()
        messages = [record.getMessage() for record in caplog.records]
        assert not any("Deleting" in msg for msg in messages)
        assert not any("Successfully deleted" in msg for msg in messages)
        assert not any("Deletion aborted" in msg for msg in messages)

    @pytest.mark.asyncio
    async def test_force_on_non_tty_stdin_aborts_without_delete(
        self,
        force_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        resolved_chats = [sample_messages[0][0]]

        with (
            patch("main.parse_args", return_value=force_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "main.resolve_search_chats",
                new_callable=AsyncMock,
                return_value=resolved_chats,
                create=True,
            ),
            patch("main.confirm_search", return_value=True, create=True),
            patch(
                "main.fetch_target_messages",
                new_callable=AsyncMock,
                return_value=sample_messages,
            ) as fetch_mock,
            patch(
                "main.delete_messages_batch",
                new_callable=AsyncMock,
            ) as delete_mock,
            patch("main.confirm_deletion", return_value=True) as confirm_mock,
        ):
            await run_purge(argv=["--chats", "chat1", "--force"], env_path=env_path)

        confirm_mock.assert_not_called()
        fetch_mock.assert_not_awaited()
        delete_mock.assert_not_awaited()
        mock_client.disconnect.assert_awaited_once()


def test_group_message_ids_by_chat_accepts_unhashable_entities() -> None:
    chat_a = UnhashableChat(111)
    chat_b = UnhashableChat(222)
    msg_a1 = MagicMock(id=1)
    msg_a2 = MagicMock(id=2)
    msg_b1 = MagicMock(id=3)

    grouped = _group_message_ids_by_chat(
        [(chat_a, msg_a1), (chat_b, msg_b1), (chat_a, msg_a2)]
    )

    assert grouped == [(chat_a, [1, 2]), (chat_b, [3])]
