"""Tests for main.py orchestration."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cli import CliArgs
from src.config import Config

from main import run_purge


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
        dry_run=True,
        force=False,
    )


@pytest.fixture
def force_args() -> CliArgs:
    return CliArgs(
        chats=["chat1"],
        keywords=None,
        after=None,
        before=None,
        everyone=False,
        dry_run=False,
        force=True,
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
    return client


@pytest.fixture
def env_path(tmp_path: Path) -> Path:
    return tmp_path / ".env"


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
    ) -> None:
        with (
            patch("main.parse_args", return_value=dry_run_args) as parse_args_mock,
            patch("main.load_config", return_value=sample_config) as load_config_mock,
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ) as create_client_mock,
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
        fetch_mock.assert_awaited_once_with(
            mock_client,
            dry_run_args.chats,
            dry_run_args.keywords,
            dry_run_args.after,
            dry_run_args.before,
            dry_run_args.everyone,
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


class TestRunPurgeForce:
    @pytest.mark.asyncio
    async def test_force_without_confirmation_does_not_delete(
        self,
        force_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        with (
            patch("main.parse_args", return_value=force_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
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
        ):
            await run_purge(argv=["--chats", "chat1", "--force"], env_path=env_path)

        confirm_mock.assert_called_once()
        delete_mock.assert_not_awaited()
        mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_force_with_confirmation_deletes_messages(
        self,
        force_args: CliArgs,
        sample_config: Config,
        sample_messages: list[tuple],
        mock_client: AsyncMock,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        chat, _msg1, _msg2 = sample_messages[0][0], sample_messages[0][1], sample_messages[1][1]

        with (
            patch("main.parse_args", return_value=force_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
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
        ):
            await run_purge(argv=["--chats", "chat1", "--force"], env_path=env_path)

        confirm_mock.assert_called_once()
        delete_mock.assert_awaited_once_with(mock_client, chat, [101, 102])
        mock_client.disconnect.assert_awaited_once()

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

        with (
            patch("main.parse_args", return_value=force_args),
            patch("main.load_config", return_value=sample_config),
            patch(
                "main.create_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
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
        ):
            await run_purge(argv=["--chats", "chat1", "--force"], env_path=env_path)

        confirm_mock.assert_not_called()
        delete_mock.assert_not_awaited()
        mock_client.disconnect.assert_awaited_once()
