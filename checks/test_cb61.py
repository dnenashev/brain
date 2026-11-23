"""Independent SQLite version and search ACL checks."""

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from brain.storage.sqlite_store import SQLiteKnowledgeStore


class ServerStorage(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "knowledge.db"
        self.store = SQLiteKnowledgeStore(self.path)

    def test_versions_and_snapshot_survive_reopen(self):
        self.assertEqual(self.store.put_revision("P1", "timeout", "legacy rule", ["alice"]), 1)
        self.assertEqual(self.store.put_revision("P1", "timeout", "current rule", ["alice"]), 2)
        reopened = SQLiteKnowledgeStore(self.path)
        self.assertEqual(reopened.read("P1", "timeout", subject="alice", version=1),
                         {"doc_id": "timeout", "version": 1, "text": "legacy rule"})
        self.assertEqual(reopened.snapshot("P1", subject="alice"),
                         [{"doc_id": "timeout", "version": 2, "text": "current rule"}])
        migrations = Path(__file__).resolve().parents[1] / "brain/storage/migrations"
        versions = [
            int(path.stem) for path in migrations.glob("*.sql")
            if path.stem.isascii() and path.stem.isdecimal()
        ]
        self.assertTrue(versions)
        with sqlite3.connect(self.path) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], max(versions))

    def test_search_limits_project_acl_and_current_version(self):
        self.store.put_revision("P1", "same-id", "oldmarker timeout", ["alice"])
        self.store.put_revision("P1", "same-id", "visible timeout", ["alice"])
        self.store.put_revision("P1", "private", "restrictedmarker timeout", ["bob"])
        self.store.put_revision("P2", "same-id", "otherproject timeout", ["alice"])
        results = self.store.search("P1", "timeout", subject="alice")
        self.assertEqual([(x["doc_id"], x["version"]) for x in results], [("same-id", 2)])
        self.assertIn("visible", results[0]["snippet"])
        self.assertEqual(self.store.search("P1", "oldmarker", subject="alice"), [])
        self.assertEqual(self.store.search("P1", "restrictedmarker", subject="alice"), [])

    def test_revocation_covers_historical_reads(self):
        self.store.put_revision("P1", "rule", "old text", ["alice"])
        self.store.put_revision("P1", "rule", "new text", ["bob"])
        self.assertIsNone(self.store.read("P1", "rule", subject="alice", version=1))
        self.assertEqual(self.store.snapshot("P1", subject="alice"), [])
