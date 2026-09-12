import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mail_importer.config import parse_config
from mail_importer.mail import Attachment, FolderSnapshot, ParsedMessage
from mail_importer.runner import run_import
from mail_importer.state import Ledger
from mail_importer.webdav import WebDavError


def configuration(destination="Inbox/Unsorted invoices"):
    return parse_config(
        {
            "timezone": "Europe/Amsterdam",
            "sources": [
                {
                    "id": "invoices",
                    "mailbox": "Invoices",
                    "unmatched_destination": destination,
                    "rules": [],
                }
            ],
        }
    )


def attachment(name, digest_character):
    content = (b"%PDF-1.7\n" + digest_character.encode())
    import hashlib

    return Attachment(name, content, hashlib.sha256(content).hexdigest())


class FakeImap:
    def __init__(self, messages, uidvalidity=1):
        self.messages = messages
        self.uidvalidity = uidvalidity

    def select(self, mailbox):
        self.mailbox = mailbox
        return FolderSnapshot(self.uidvalidity, tuple(self.messages))

    def fetch(self, uid, timezone):
        return self.messages[uid]


class FakeWebDav:
    def __init__(self, fail_once=()):
        self.fail_once = set(fail_once)
        self.collections = []
        self.uploads = []

    def ensure_collection(self, path):
        self.collections.append(path)

    def upload_create_only(self, path, content, digest):
        self.uploads.append((path, digest))
        if digest in self.fail_once:
            self.fail_once.remove(digest)
            raise WebDavError("synthetic failure")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temporary.name) / "imports.sqlite3"
        self.when = datetime(2026, 2, 3, 12, tzinfo=ZoneInfo("Europe/Amsterdam"))

    def tearDown(self):
        self.temporary.cleanup()

    def message(self, uid, attachments):
        return ParsedMessage(
            uid,
            self.when,
            ("billing@example.com",),
            "Private invoice subject",
            tuple(attachments),
        )

    def test_dry_run_needs_no_state_or_webdav_and_writes_nothing(self):
        item = attachment("private invoice.pdf", "a")
        logs = []
        stats = run_import(
            configuration(),
            FakeImap({1: self.message(1, [item]), 2: self.message(2, [])}),
            account="me@example.com",
            dry_run=True,
            output=logs.append,
        )
        self.assertEqual(stats.unmatched, 1)
        self.assertEqual(stats.without_pdf, 1)
        self.assertIn("private invoice.pdf", "\n".join(logs))
        self.assertFalse(self.state_path.exists())

    def test_reserved_destination_survives_failed_upload_and_rule_edit(self):
        item = attachment("invoice.pdf", "b")
        imap = FakeImap({1: self.message(1, [item])})
        first_remote = FakeWebDav(fail_once={item.sha256})
        with Ledger(self.state_path, "archive", "account") as ledger:
            first = run_import(
                configuration("Old/{year}"),
                imap,
                account="account",
                ledger=ledger,
                webdav=first_remote,
                output=lambda _: None,
            )
            self.assertEqual(first.failed, 1)
            second_remote = FakeWebDav()
            second = run_import(
                configuration("New/{year}"),
                imap,
                account="account",
                ledger=ledger,
                webdav=second_remote,
                output=lambda _: None,
            )
            self.assertEqual(second.saved, 1)
            self.assertTrue(second_remote.uploads[0][0].startswith("Old/2026/"))
            self.assertTrue(ledger.message_completed("invoices", "Invoices", 1, 1))

    def test_partial_message_retries_only_incomplete_attachment(self):
        first = attachment("first.pdf", "c")
        second = attachment("second.pdf", "d")
        imap = FakeImap({1: self.message(1, [first, second])})
        remote = FakeWebDav(fail_once={second.sha256})
        with Ledger(self.state_path, "archive", "account") as ledger:
            initial = run_import(
                configuration(),
                imap,
                account="account",
                ledger=ledger,
                webdav=remote,
                output=lambda _: None,
            )
            self.assertEqual((initial.saved, initial.failed), (1, 1))
            self.assertFalse(ledger.message_completed("invoices", "Invoices", 1, 1))
            retry = run_import(
                configuration(),
                imap,
                account="account",
                ledger=ledger,
                webdav=remote,
                output=lambda _: None,
            )
            self.assertEqual((retry.saved, retry.duplicate, retry.failed), (1, 1, 0))
            self.assertTrue(ledger.message_completed("invoices", "Invoices", 1, 1))

    def test_uidvalidity_change_rescans_but_content_is_duplicate(self):
        item = attachment("invoice.pdf", "e")
        messages = {42: self.message(42, [item])}
        with Ledger(self.state_path, "archive", "account") as ledger:
            first = run_import(
                configuration(),
                FakeImap(messages, uidvalidity=10),
                account="account",
                ledger=ledger,
                webdav=FakeWebDav(),
                output=lambda _: None,
            )
            second_remote = FakeWebDav()
            second = run_import(
                configuration(),
                FakeImap(messages, uidvalidity=11),
                account="account",
                ledger=ledger,
                webdav=second_remote,
                output=lambda _: None,
            )
            self.assertEqual(first.saved, 1)
            self.assertEqual(second.duplicate, 1)
            self.assertEqual(second_remote.uploads, [])
            self.assertTrue(ledger.message_completed("invoices", "Invoices", 11, 42))

    def test_limit_counts_only_unprocessed_messages(self):
        item = attachment("invoice.pdf", "f")
        messages = {
            1: self.message(1, [item]),
            2: self.message(2, [attachment("other.pdf", "g")]),
        }
        with Ledger(self.state_path, "archive", "account") as ledger:
            stats = run_import(
                configuration(),
                FakeImap(messages),
                account="account",
                ledger=ledger,
                webdav=FakeWebDav(),
                limit=1,
                output=lambda _: None,
            )
            self.assertEqual(stats.examined, 1)
            self.assertTrue(ledger.message_completed("invoices", "Invoices", 1, 1))
            self.assertFalse(ledger.message_completed("invoices", "Invoices", 1, 2))

    def test_normal_failure_log_does_not_include_mail_metadata(self):
        item = attachment("private filename.pdf", "h")
        logs = []
        with Ledger(self.state_path, "archive", "account") as ledger:
            run_import(
                configuration(),
                FakeImap({1: self.message(1, [item])}),
                account="account",
                ledger=ledger,
                webdav=FakeWebDav(fail_once={item.sha256}),
                output=logs.append,
            )
        joined = "\n".join(logs)
        self.assertNotIn("private filename", joined)
        self.assertNotIn("Private invoice subject", joined)
        self.assertIn("synthetic failure", joined)


if __name__ == "__main__":
    unittest.main()
