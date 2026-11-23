from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.operations.recovery import backup, restore
from brain.storage.sqlite_store import SQLiteKnowledgeStore


class StandRecovery(unittest.TestCase):
    def test_restore_preserves_history_acl_and_attachments(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source"
            source.mkdir()
            (source / "attachments").mkdir()
            (source / "attachments/evidence.txt").write_text("fixture")
            (source / "credentials.json").write_text("operator credentials")
            store = SQLiteKnowledgeStore(source / "knowledge.db")
            store.put_revision("p", "doc", "old", ["owner"])
            store.put_revision("p", "doc", "new", ["owner"])
            archive = root / "backup.zip"
            saved = backup(source, archive)
            self.assertEqual(saved["archive_sha256"], sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(set(saved["files"]), {"knowledge.db", "attachments/evidence.txt"})
            target = root / "restored"
            result = restore(archive, target)
            self.assertEqual(result["files"], saved["files"])
            self.assertGreaterEqual(result["rto_seconds"], 0)
            reopened = SQLiteKnowledgeStore(target / "knowledge.db")
            self.assertEqual(reopened.read("p", "doc", subject="owner", version=1)["text"], "old")
            self.assertEqual(reopened.read("p", "doc", subject="owner")["text"], "new")
            self.assertIsNone(reopened.read("p", "doc", subject="outside"))
            self.assertEqual((target / "attachments/evidence.txt").read_text(), "fixture")
            self.assertFalse((target / "credentials.json").exists())
