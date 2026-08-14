"""Tests for CLI argument parsing in src.cli."""

import sys

import pytest

from src.cli import parse_args


@pytest.fixture
def argv(monkeypatch):
    """Replace sys.argv with a synthetic CLI invocation."""

    def _set_args(args: list[str]) -> None:
        monkeypatch.setattr(sys, "argv", ["tmpu", *args])

    return _set_args


def test_minimal_args_default_to_dry_run(argv):
    """Given only --chats, defaults dry_run=True, force=False, everyone=False,
    channels=False, group_chats=False, no_confirmation=False, wait_seconds=0.1."""
    argv(["--chats", "foo"])

    args = parse_args()

    assert args.chats == ["foo"]
    assert args.dry_run is True
    assert args.force is False
    assert args.everyone is False
    assert args.from_user is None
    assert args.channels is False
    assert args.group_chats is False
    assert args.no_confirmation is False
    assert args.wait_seconds == 0.1


def test_wait_seconds_parses_as_float(argv):
    """Given --wait-seconds 0.5, wait_seconds is the float 0.5."""
    argv(["--chats", "foo", "--wait-seconds", "0.5"])

    args = parse_args()

    assert args.wait_seconds == 0.5


def test_wait_seconds_zero_parses_as_float(argv):
    """Given --wait-seconds 0, wait_seconds is the float 0.0."""
    argv(["--chats", "foo", "--wait-seconds", "0"])

    args = parse_args()

    assert args.wait_seconds == 0.0


def test_no_confirmation_flag_sets_no_confirmation_true(argv):
    """Given --no-confirmation, no_confirmation=True."""
    argv(["--chats", "foo", "--no-confirmation"])

    args = parse_args()

    assert args.no_confirmation is True


def test_force_sets_force_true_and_dry_run_false(argv):
    """Given --force, force=True and dry_run=False."""
    argv(["--chats", "foo", "--force"])

    args = parse_args()

    assert args.force is True
    assert args.dry_run is False


def test_dry_run_and_force_together_raise_error(argv):
    """Given --dry-run and --force, argparse rejects conflicting flags."""
    argv(["--chats", "foo", "--dry-run", "--force"])

    with pytest.raises(SystemExit):
        parse_args()


def test_invalid_date_format_raises(argv):
    """Given an invalid --after date, parse_args raises an error."""
    argv(["--chats", "foo", "--after", "not-a-date"])

    with pytest.raises((SystemExit, ValueError)):
        parse_args()


def test_chats_and_keywords_parsed_as_lists(argv):
    """Given comma-separated --chats and --keywords, values are stripped and split."""
    argv(["--chats", " foo , bar ", "--keywords", " spam , trash "])

    args = parse_args()

    assert args.chats == ["foo", "bar"]
    assert args.keywords == ["spam", "trash"]

    argv(["--chats", "all"])
    args = parse_args()
    assert args.chats == ["all"]


def test_everyone_flag(argv):
    """Given --everyone, everyone=True."""
    argv(["--chats", "foo", "--everyone"])

    args = parse_args()

    assert args.everyone is True


def test_from_user_flag(argv):
    """Given --from, from_user is stored."""
    argv(["--chats", "foo", "--from", "@alice"])

    args = parse_args()

    assert args.from_user == "@alice"


def test_channels_flag_sets_channels_true(argv):
    """Given --channels, channels=True and group_chats remains False."""
    argv(["--chats", "foo", "--channels"])

    args = parse_args()

    assert args.channels is True
    assert args.group_chats is False


def test_group_chats_flag_sets_group_chats_true(argv):
    """Given --group-chats, group_chats=True and channels remains False."""
    argv(["--chats", "foo", "--group-chats"])

    args = parse_args()

    assert args.group_chats is True
    assert args.channels is False


def test_channels_and_group_chats_together(argv):
    """Given --channels and --group-chats, both are True."""
    argv(["--chats", "foo", "--channels", "--group-chats"])

    args = parse_args()

    assert args.channels is True
    assert args.group_chats is True
