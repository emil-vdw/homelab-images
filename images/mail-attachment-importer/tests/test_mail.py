import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from zoneinfo import ZoneInfo

from mail_importer.mail import (
    AttachmentTooLarge,
    ImapClient,
    ImapSettings,
    MailError,
    MessageTooLarge,
    archive_filename,
    parse_message,
    sanitize_filename,
)


class FakeImap:
    def __init__(self, raw: bytes, *, declared_size: int | None = None) -> None:
        self.raw = raw
        self.declared_size = len(raw) if declared_size is None else declared_size
        self.commands: list[tuple[object, ...]] = []

    def login(self, username: str, password: str):
        self.commands.append(("LOGIN", username, password))
        return "OK", [b"logged in"]

    def logout(self):
        self.commands.append(("LOGOUT",))
        return "BYE", [b""]

    def list(self):
        self.commands.append(("LIST",))
        return "OK", [
            b'(\\HasNoChildren) "/" "Invoices 2026"',
            b'(\\HasNoChildren) "/" "L&APY-hne"',
        ]

    def select(self, mailbox: str, readonly: bool = False):
        self.commands.append(("SELECT", mailbox, readonly))
        return "OK", [b"2"]

    def response(self, name: str):
        self.commands.append(("RESPONSE", name))
        return name, [b"8123"]

    def uid(self, command: str, *args):
        self.commands.append(("UID", command, *args))
        if command == "SEARCH":
            return "OK", [b"7 11"]
        if args[-1] == "(RFC822.SIZE INTERNALDATE)":
            line = (
                f"1 (UID {args[0]} RFC822.SIZE {self.declared_size} "
                'INTERNALDATE "01-Jan-2025 10:00:00 +0000")'
            ).encode()
            return "OK", [line]
        return "OK", [(b"1 (BODY[] {123})", self.raw), b")"]


def message_bytes() -> bytes:
    message = EmailMessage()
    message["From"] = "Accounts <Accounts@Example.COM>"
    message["Subject"] = "=?utf-8?q?Januari_factuur?="
    message["Date"] = "Tue, 31 Dec 2024 23:30:00 -0200"
    message.set_content("Invoice attached")
    message.add_attachment(
        b"%PDF-1.7\nfirst",
        maintype="application",
        subtype="pdf",
        filename="factuur januári.pdf",
    )
    message.add_attachment(
        b"%PDF-1.7\nsecond",
        maintype="application",
        subtype="octet-stream",
        filename="second.pdf",
    )
    message.add_attachment(
        b"image",
        maintype="image",
        subtype="png",
        filename="logo.png",
        disposition="inline",
    )
    return message.as_bytes(policy=SMTP)


def settings() -> ImapSettings:
    return ImapSettings("bridge", 1993, "mail.terminus.home.arpa", "user", "password")


class MailTests(unittest.TestCase):
    def test_read_only_imap_flow_prefetches_size_and_peeks_body(self) -> None:
        fake = FakeImap(message_bytes())
        client = ImapClient(settings(), connection_factory=lambda *_: fake)
        with client:
            self.assertEqual(client.list_folders(), ("Invoices 2026", "Löhne"))
            snapshot = client.select("Invoices 2026")
            parsed = client.fetch(7, ZoneInfo("Europe/Amsterdam"))

        self.assertEqual(snapshot.uidvalidity, 8123)
        self.assertEqual(snapshot.uids, (7, 11))
        self.assertIn(("SELECT", '"Invoices 2026"', True), fake.commands)
        fetches = [
            command for command in fake.commands if command[:2] == ("UID", "FETCH")
        ]
        self.assertEqual(fetches[0][-1], "(RFC822.SIZE INTERNALDATE)")
        self.assertEqual(
            fetches[1][-1], f"(BODY.PEEK[]<0.{client.max_message_bytes + 1}>)"
        )
        self.assertEqual(parsed.when.isoformat(), "2025-01-01T02:30:00+01:00")
        self.assertEqual(parsed.from_addresses, ("accounts@example.com",))
        self.assertEqual(parsed.subject, "Januari factuur")
        self.assertEqual(
            [attachment.filename for attachment in parsed.attachments],
            ["factuur januári.pdf", "second.pdf"],
        )

    def test_oversized_message_is_rejected_before_body_fetch(self) -> None:
        fake = FakeImap(message_bytes(), declared_size=101)
        client = ImapClient(
            settings(), max_message_bytes=100, connection_factory=lambda *_: fake
        )
        with client:
            client.select("Invoices")
            with self.assertRaises(MessageTooLarge):
                client.fetch(7, ZoneInfo("UTC"))
        fetches = [
            command for command in fake.commands if command[:2] == ("UID", "FETCH")
        ]
        self.assertEqual(len(fetches), 1)

    def test_pdf_attachment_without_filename_gets_default_name(self) -> None:
        message = EmailMessage()
        message.set_content("body")
        message.add_attachment(
            b"%PDF-1.4\ncontent", maintype="application", subtype="pdf"
        )
        parsed = self.parse(message)
        self.assertEqual(parsed.attachments[0].filename, "attachment.pdf")

    def test_named_non_inline_pdf_without_attachment_disposition_is_accepted(
        self,
    ) -> None:
        message = EmailMessage()
        message.set_content("body")
        message.add_attachment(
            b"%PDF-1.4\ncontent",
            maintype="application",
            subtype="pdf",
            filename="named.pdf",
            disposition=None,
        )
        self.assertEqual(
            [item.filename for item in self.parse(message).attachments], ["named.pdf"]
        )

    def test_oversized_and_invalid_pdf_attachments_fail_the_message(self) -> None:
        oversized = EmailMessage()
        oversized.set_content("body")
        oversized.add_attachment(
            b"%PDF-123", maintype="application", subtype="pdf", filename="a.pdf"
        )
        with self.assertRaises(AttachmentTooLarge):
            self.parse(oversized, max_attachment_bytes=4)

        invalid = EmailMessage()
        invalid.set_content("body")
        invalid.add_attachment(
            b"not actually a PDF",
            maintype="application",
            subtype="pdf",
            filename="fake.pdf",
        )
        with self.assertRaisesRegex(MailError, "without a PDF signature"):
            self.parse(invalid)

    def test_malformed_base64_pdf_fails_instead_of_archiving_permissive_decode(
        self,
    ) -> None:
        raw = (
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: application/pdf\r\n"
            b"Content-Disposition: attachment; filename=invoice.pdf\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\n"
            b"JVBERi0xLjQK!!\r\n"
        )
        with self.assertRaisesRegex(MailError, "invalid PDF attachment encoding"):
            parse_message(
                raw,
                uid=9,
                internal_date=datetime.now(timezone.utc),
                local_timezone=ZoneInfo("UTC"),
            )

    def test_invalid_or_timezone_less_date_falls_back_to_internaldate(self) -> None:
        for date_header in ("definitely invalid", "Wed, 01 Jul 2026 23:30:00"):
            with self.subTest(date_header=date_header):
                message = EmailMessage()
                message["Date"] = date_header
                message.set_content("body")
                parsed = self.parse(
                    message,
                    internal_date=datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc),
                    local_timezone=ZoneInfo("Europe/Amsterdam"),
                )
                self.assertEqual(parsed.when.isoformat(), "2026-07-02T01:30:00+02:00")

    def test_unknown_offset_date_is_defined_as_utc(self) -> None:
        message = EmailMessage()
        message["Date"] = "Thu, 01 Jan 2026 00:30:00 -0000"
        message.set_content("body")
        parsed = self.parse(
            message,
            internal_date=datetime(2025, 12, 1, tzinfo=timezone.utc),
            local_timezone=ZoneInfo("Europe/Amsterdam"),
        )
        self.assertEqual(parsed.when.isoformat(), "2026-01-01T01:30:00+01:00")

    def test_filename_helpers_remove_paths_and_add_stable_hash_suffix(self) -> None:
        digest = "a" * 64
        self.assertEqual(sanitize_filename("../../bad:name.pdf"), "bad_name.pdf")
        self.assertEqual(
            archive_filename(
                "../../bad:name.pdf",
                digest,
                datetime(2026, 2, 3, tzinfo=timezone.utc),
            ),
            "2026-02-03-bad_name-aaaaaaaaaaaa.pdf",
        )
        self.assertLessEqual(len(sanitize_filename("é" * 200 + ".pdf").encode()), 160)

    def test_imap_settings_validate_environment(self) -> None:
        values = {
            "IMAP_HOST": "bridge",
            "IMAP_USERNAME": "me",
            "IMAP_PASSWORD": "secret",
        }
        self.assertEqual(ImapSettings.from_env(values).port, 993)
        with self.assertRaisesRegex(MailError, "IMAP_PORT"):
            ImapSettings.from_env({**values, "IMAP_PORT": "invalid"})

    @staticmethod
    def parse(message: EmailMessage, **overrides):
        arguments = {
            "uid": 1,
            "internal_date": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "local_timezone": ZoneInfo("UTC"),
        }
        arguments.update(overrides)
        return parse_message(message.as_bytes(), **arguments)


if __name__ == "__main__":
    unittest.main()
