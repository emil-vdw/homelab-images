import tempfile
import unittest
from pathlib import Path

from mail_importer.state import AlreadyRunning, Ledger, StateError


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "imports.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def test_reservation_survives_rule_change_and_completion(self):
        with Ledger(self.path, "https://cloud.example/documents", "mail@example.com") as ledger:
            first = ledger.reserve("a" * 64, "old/place/file.pdf")
            second = ledger.reserve("a" * 64, "new/place/file.pdf")
            self.assertEqual(first.destination, "old/place/file.pdf")
            self.assertEqual(second.destination, "old/place/file.pdf")
            self.assertFalse(second.completed)
            ledger.complete("a" * 64)
            self.assertTrue(ledger.reserve("a" * 64, "elsewhere/file.pdf").completed)

    def test_content_is_shared_across_sources_but_not_archive_roots(self):
        with Ledger(self.path, "https://cloud.example/one", "a@example.com") as ledger:
            ledger.reserve("b" * 64, "invoices/file.pdf")
            ledger.complete("b" * 64)
        with Ledger(self.path, "https://cloud.example/one", "b@example.com") as ledger:
            self.assertTrue(ledger.reserve("b" * 64, "payslips/file.pdf").completed)
        with Ledger(self.path, "https://cloud.example/two", "a@example.com") as ledger:
            self.assertFalse(ledger.reserve("b" * 64, "invoices/file.pdf").completed)

    def test_messages_are_scoped_by_uidvalidity_and_source(self):
        with Ledger(self.path, "archive", "account") as ledger:
            ledger.mark_message_complete("invoices", "Invoices", 10, 42)
            self.assertTrue(ledger.message_completed("invoices", "Invoices", 10, 42))
            self.assertFalse(ledger.message_completed("invoices", "Invoices", 11, 42))
            self.assertFalse(ledger.message_completed("payslips", "Invoices", 10, 42))

    def test_complete_requires_reservation(self):
        with Ledger(self.path, "archive", "account") as ledger:
            with self.assertRaises(StateError):
                ledger.complete("c" * 64)

    def test_second_process_cannot_take_lock(self):
        with Ledger(self.path, "archive", "account") as first:
            with first.locked():
                with Ledger(self.path, "archive", "account") as ledger:
                    with self.assertRaises(AlreadyRunning):
                        with ledger.locked():
                            pass


if __name__ == "__main__":
    unittest.main()
