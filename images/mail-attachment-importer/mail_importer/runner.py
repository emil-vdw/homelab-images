from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable

from .config import Config
from .mail import ImapClient, MailError, archive_filename
from .state import Ledger
from .webdav import WebDavAuthenticationError, WebDavClient, WebDavError


@dataclass
class RunStats:
    examined: int = 0
    completed_messages: int = 0
    saved: int = 0
    duplicate: int = 0
    unmatched: int = 0
    without_pdf: int = 0
    failed: int = 0


def run_import(
    config: Config,
    imap: ImapClient,
    *,
    account: str,
    ledger: Ledger | None = None,
    webdav: WebDavClient | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    output: Callable[[str], None] = print,
) -> RunStats:
    if dry_run and (ledger is not None or webdav is not None):
        raise ValueError("dry runs must not receive state or WebDAV clients")
    if not dry_run and (ledger is None or webdav is None):
        raise ValueError("normal imports require state and WebDAV clients")
    stats = RunStats()
    remaining = limit

    for source in config.sources:
        if remaining == 0:
            break
        try:
            snapshot = imap.select(source.mailbox)
        except MailError:
            output(f"source={source.id} stage=select result=failed")
            stats.failed += 1
            continue

        for uid in snapshot.uids:
            if remaining == 0:
                break
            if ledger is not None and ledger.message_completed(
                source.id, source.mailbox, snapshot.uidvalidity, uid
            ):
                continue
            if remaining is not None:
                remaining -= 1
            stats.examined += 1
            opaque_id = _opaque_message_id(
                account, source.id, source.mailbox, snapshot.uidvalidity, uid
            )
            try:
                message = imap.fetch(uid, config.timezone)
            except MailError:
                output(f"source={source.id} message={opaque_id} stage=fetch result=failed")
                stats.failed += 1
                continue

            if not message.attachments:
                stats.without_pdf += 1
                stats.completed_messages += 1
                if ledger is not None:
                    ledger.mark_message_complete(
                        source.id, source.mailbox, snapshot.uidvalidity, uid
                    )
                output(f"source={source.id} message={opaque_id} pdfs=0 result=complete")
                continue

            message_failed = False
            for attachment in message.attachments:
                route = source.route(
                    message.from_addresses,
                    message.subject,
                    attachment.filename,
                    message.when,
                )
                filename = archive_filename(attachment.filename, attachment.sha256, message.when)
                intended_path = f"{route.destination}/{filename}"
                rule_id = route.rule_id or "unmatched"
                if route.rule_id is None:
                    stats.unmatched += 1

                if dry_run:
                    output(
                        f"source={source.id} message={opaque_id} rule={rule_id} "
                        f"attachment={attachment.filename!r} destination={intended_path!r}"
                    )
                    continue

                assert ledger is not None and webdav is not None
                stored = ledger.reserve(attachment.sha256, intended_path)
                if stored.completed:
                    stats.duplicate += 1
                    output(
                        f"source={source.id} message={opaque_id} rule={rule_id} result=duplicate"
                    )
                    continue
                try:
                    directory = str(PurePosixPath(stored.destination).parent)
                    webdav.ensure_collection(directory)
                    webdav.upload_create_only(
                        stored.destination, attachment.content, attachment.sha256
                    )
                    ledger.complete(attachment.sha256)
                    stats.saved += 1
                    output(f"source={source.id} message={opaque_id} rule={rule_id} result=saved")
                except WebDavAuthenticationError:
                    raise
                except WebDavError as error:
                    message_failed = True
                    stats.failed += 1
                    output(
                        f"source={source.id} message={opaque_id} rule={rule_id} "
                        f"stage=upload result=failed detail={error}"
                    )
                except OSError:
                    message_failed = True
                    stats.failed += 1
                    output(
                        f"source={source.id} message={opaque_id} rule={rule_id} "
                        "stage=upload result=failed detail=transport"
                    )

            if not message_failed:
                stats.completed_messages += 1
                if ledger is not None:
                    ledger.mark_message_complete(
                        source.id, source.mailbox, snapshot.uidvalidity, uid
                    )

    output(
        f"examined={stats.examined} messages={stats.completed_messages} saved={stats.saved} "
        f"duplicate={stats.duplicate} unmatched={stats.unmatched} "
        f"without_pdf={stats.without_pdf} failed={stats.failed}"
    )
    return stats


def _opaque_message_id(
    account: str,
    source_id: str,
    mailbox: str,
    uidvalidity: int,
    uid: int,
) -> str:
    identity = "\0".join(
        (account, source_id, mailbox, str(uidvalidity), str(uid))
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:12]
