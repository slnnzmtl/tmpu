import argparse
from dataclasses import dataclass
from datetime import datetime, timezone

_DATE_FORMAT = "%Y-%m-%d"


@dataclass
class CliArgs:
    chats: list[str]
    keywords: list[str] | None
    after: datetime | None
    before: datetime | None
    everyone: bool
    from_user: str | None
    dry_run: bool
    force: bool
    channels: bool
    group_chats: bool
    no_confirmation: bool
    wait_seconds: float


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_date(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, _DATE_FORMAT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date format: {value}") from exc
    return parsed.replace(tzinfo=timezone.utc)


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chats", required=True, type=_split_csv)
    parser.add_argument("--keywords", type=_split_csv)
    parser.add_argument("--after", type=_parse_date)
    parser.add_argument("--before", type=_parse_date)
    parser.add_argument("--everyone", action="store_true")
    parser.add_argument("--channels", action="store_true")
    parser.add_argument("--group-chats", dest="group_chats", action="store_true")
    parser.add_argument(
        "--from",
        dest="from_user",
        help="Filter by sender (@username, numeric ID, or exact display name)",
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", dest="dry_run", action="store_true")
    group.add_argument("--force", action="store_true")
    parser.add_argument("--no-confirmation", dest="no_confirmation", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=0.1)

    return parser


def _cli_args_from_namespace(namespace: argparse.Namespace) -> CliArgs:
    # Default is dry-run; --force is the only way to disable it.
    return CliArgs(
        chats=namespace.chats,
        keywords=namespace.keywords,
        after=namespace.after,
        before=namespace.before,
        everyone=namespace.everyone,
        from_user=namespace.from_user,
        dry_run=not namespace.force,
        force=namespace.force,
        channels=namespace.channels,
        group_chats=namespace.group_chats,
        no_confirmation=namespace.no_confirmation,
        wait_seconds=namespace.wait_seconds,
    )


def parse_args(argv: list[str] | None = None) -> CliArgs:
    return _cli_args_from_namespace(_create_parser().parse_args(argv))
