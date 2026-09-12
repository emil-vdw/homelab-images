from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from .config import ConfigError, load_config
from .mail import ImapClient, ImapSettings, MailError
from .runner import run_import
from .state import AlreadyRunning, Ledger, StateError
from .webdav import WebDavAuthenticationError, WebDavClient, WebDavError


def _positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import PDF email attachments into OpenCloud")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", default="/state/imports.sqlite3")
    parser.add_argument("--limit", type=_positive_integer)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--list-folders", action="store_true")
    mode.add_argument("--check-config", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"configuration invalid: {error}", file=sys.stderr)
        return 2
    if args.check_config:
        print(f"configuration valid: sources={len(config.sources)}")
        return 0

    try:
        settings = ImapSettings.from_env()
        with ImapClient(settings) as imap:
            if args.list_folders:
                for folder in imap.list_folders():
                    print(folder)
                return 0
            if args.dry_run:
                stats = run_import(
                    config,
                    imap,
                    account=settings.username,
                    dry_run=True,
                    limit=args.limit,
                )
                return 1 if stats.failed else 0

            webdav = _webdav_from_env()
            webdav.check_root()
            ledger = Ledger(args.state, webdav.archive_root, settings.username)
            with ledger.locked():
                with ledger:
                    stats = run_import(
                        config,
                        imap,
                        account=settings.username,
                        ledger=ledger,
                        webdav=webdav,
                        limit=args.limit,
                    )
            return 1 if stats.failed else 0
    except AlreadyRunning:
        print("state lock is held by another importer process", file=sys.stderr)
    except WebDavAuthenticationError:
        print("OpenCloud authentication failed", file=sys.stderr)
    except WebDavError:
        print("OpenCloud connection or base collection check failed", file=sys.stderr)
    except MailError:
        print("IMAP connection or command failed", file=sys.stderr)
    except (OSError, sqlite3.Error, StateError):
        print("local state operation failed", file=sys.stderr)
    return 1


def _webdav_from_env() -> WebDavClient:
    names = (
        "OPENCLOUD_WEBDAV_BASE_URL",
        "OPENCLOUD_USERNAME",
        "OPENCLOUD_APP_TOKEN",
    )
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise WebDavError(f"missing required OpenCloud setting: {', '.join(missing)}")
    return WebDavClient(*(os.environ[name] for name in names))


if __name__ == "__main__":
    raise SystemExit(main())
