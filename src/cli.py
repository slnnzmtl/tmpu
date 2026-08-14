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
    dry_run: bool
    force: bool


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

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", dest="dry_run", action="store_true")
    group.add_argument("--force", action="store_true")

    return parser


def _cli_args_from_namespace(namespace: argparse.Namespace) -> CliArgs:
    # Default is dry-run; --force is the only way to disable it.
    return CliArgs(
        chats=namespace.chats,
        keywords=namespace.keywords,
        after=namespace.after,
        before=namespace.before,
        everyone=namespace.everyone,
        dry_run=not namespace.force,
        force=namespace.force,
    )


def parse_args(argv: list[str] | None = None) -> CliArgs:
    return _cli_args_from_namespace(_create_parser().parse_args(argv))
