"""Independent checks for atomic changesets and compensating revisions."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.storage.changesets import ChangeSetStore
from brain.storage.sqlite_store import SQLiteKnowledgeStore


class Changesets(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "store.db"
        self.storage = SQLiteKnowledgeStore(path)
        self.changes = ChangeSetStore(path)
        for doc in ("first", "second"):
            self.storage.put_revision("P1", doc, f"before {doc}", ["alice"])

    def batch(self, expected=1):
        return [{"doc_id": doc, "expected_version": expected, "text": f"after {doc}", "readers": ["alice"]}
                for doc in ("first", "second")]

    def test_repeat_conflict_and_compensation_preserve_history(self):
        batch = self.batch()
        receipt = self.changes.apply("change-1", "P1", "editor", batch)
        self.assertEqual((receipt["actor"], receipt["versions"]), ("editor", {"first": 2, "second": 2}))
        self.assertEqual(self.changes.apply("change-1", "P1", "editor", batch), receipt)
        with self.assertRaises(ValueError):
            self.changes.apply("change-2", "P1", "editor", self.batch(expected=1))
        restored = self.changes.compensate("change-1", compensation_id="undo-1", actor="operator")
        self.assertEqual((restored["actor"], restored["compensates"], restored["versions"]),
                         ("operator", "change-1", {"first": 3, "second": 3}))
        self.assertEqual(self.storage.read("P1", "first", subject="alice")["text"], "before first")
        self.assertEqual(self.storage.read("P1", "first", subject="alice", version=2)["text"], "after first")

    def test_conflict_on_second_document_does_not_partially_write(self):
        batch = self.batch()
        batch[1]["expected_version"] = 0
        with self.assertRaises(ValueError):
            self.changes.apply("atomic-1", "P1", "editor", batch)
        row = self.storage.read("P1", "first", subject="alice")
        self.assertEqual((row["version"], row["text"]), (1, "before first"))
