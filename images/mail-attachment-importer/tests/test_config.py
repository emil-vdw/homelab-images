import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mail_importer.config import ConfigError, load_config


def valid_config() -> str:
    return """\
timezone: Europe/Amsterdam
sources:
  - id: invoices
    mailbox: Invoices
    unmatched_destination: Inbox/Unsorted invoices
    rules:
      - id: first
        from_contains: accounts@example.com
        subject_regex: 'invoice|factuur'
        destination: Home/Invoices/{year}/{month}
      - id: second
        filename_contains: invoice
        destination: Other/{date}
"""


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)

    def write_config(self, body: str) -> Path:
        path = self.directory / "rules.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_loads_and_routes_ordered_rules(self) -> None:
        config = load_config(self.write_config(valid_config()))
        route = config.sources[0].route(
            ("accounts@example.com",),
            "Monthly FACTUUR",
            "invoice.pdf",
            datetime(2025, 12, 31, 23, 30, tzinfo=ZoneInfo("UTC")).astimezone(
                config.timezone
            ),
        )
        self.assertEqual(str(config.timezone), "Europe/Amsterdam")
        self.assertEqual(route.rule_id, "first")
        self.assertEqual(route.destination, "Home/Invoices/2026/01")

    def test_sender_predicates_must_match_the_same_address(self) -> None:
        body = valid_config().replace(
            "from_contains: accounts@example.com",
            "from_contains: accounts@\n        from_regex: '^billing@.*example\\.com$'",
        )
        source = load_config(self.write_config(body)).sources[0]
        route = source.route(
            ("accounts@other.test", "billing@example.com"),
            "invoice",
            "invoice.pdf",
            datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")),
        )
        self.assertEqual(route.rule_id, "second")

    def test_rejects_invalid_top_level_values(self) -> None:
        cases = (
            ("timezone: Europe/Amsterdam", "duplicate YAML key"),
            ("timezone: Invalid/Nowhere", "unknown timezone"),
            ("timezone: 42", "timezone must be a non-empty string"),
        )
        for replacement, message in cases:
            with self.subTest(replacement=replacement):
                body = valid_config()
                if replacement.startswith("timezone: Europe"):
                    body = replacement + "\n" + body
                else:
                    body = body.replace("timezone: Europe/Amsterdam", replacement)
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(self.write_config(body))

    def test_rejects_invalid_sources_and_rules(self) -> None:
        changes = (
            lambda body: body.replace(
                "mailbox: Invoices", "mailbox: Invoices\n    extra: true"
            ),
            lambda body: body.replace(
                "subject_regex: 'invoice|factuur'", "subject_regex: '('"
            ),
            lambda body: body.replace("Home/Invoices/{year}/{month}", "../outside"),
            lambda body: body.replace("Home/Invoices/{year}/{month}", "Home/{sender}"),
            lambda body: body.replace("Home/Invoices/{year}/{month}", "Home/{{year}}"),
            lambda body: body.replace(
                "from_contains: accounts@example.com", "from_contains: 123"
            ),
            lambda body: body.replace("id: second", "id: first"),
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ConfigError):
                load_config(self.write_config(change(valid_config())))

    def test_rejects_duplicate_source_ids_and_mailboxes(self) -> None:
        source = """\
  - id: invoices
    mailbox: Invoices
    unmatched_destination: Inbox/Unsorted
    rules: []
"""
        duplicates = (
            source + source,
            source + source.replace("id: invoices", "id: payslips"),
        )
        for duplicate in duplicates:
            with self.subTest(duplicate=duplicate), self.assertRaises(ConfigError):
                load_config(
                    self.write_config(
                        "timezone: Europe/Amsterdam\nsources:\n" + duplicate
                    )
                )

    def test_unmatched_destination_has_no_implicit_year(self) -> None:
        source = load_config(self.write_config(valid_config())).sources[0]
        route = source.route(
            ("someone@example.net",),
            "hello",
            "document.pdf",
            datetime(2026, 7, 2, tzinfo=ZoneInfo("UTC")),
        )
        self.assertIsNone(route.rule_id)
        self.assertEqual(route.destination, "Inbox/Unsorted invoices")


if __name__ == "__main__":
    unittest.main()
