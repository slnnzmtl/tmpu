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
    """Given only --chats, defaults dry_run=True, force=False, everyone=False."""
    argv(["--chats", "foo"])

    args = parse_args()

    assert args.chats == ["foo"]
    assert args.dry_run is True
    assert args.force is False
    assert args.everyone is False


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
