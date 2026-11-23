"""Independent local Git import and rollback checks."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.storage.changesets import ChangeSetStore
from brain.storage.import_git import apply_git_import, plan_git_import
from brain.storage.sqlite_store import SQLiteKnowledgeStore


class GitImport(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.repo = self.root / "git"
        self.commit_id = "a" * 40
        self.files = {
            "active/rule.md": b"Current [archive](../archive/old.md).\n",
            "archive/old.md": b"Prior revision.\n",
        }
        self.store = SQLiteKnowledgeStore(self.root / "store.db")
        self.changes = ChangeSetStore(self.root / "store.db")

    def snapshot(self, repo, ref):
        self.assertEqual((Path(repo), ref), (self.repo, "main"))
        return self.commit_id, self.files.copy()

    def plan(self, current):
        return plan_git_import(self.repo, "main", project_id="P1", current=current, read_snapshot=self.snapshot,
                               readers_by_path={"active/rule.md": ["alice"], "archive/old.md": ["bob"]})

    def test_inventory_dry_run_retry_and_rollback(self):
        self.store.put_revision("P1", "active/rule.md", "Previously stored.", ["alice"])
        previous = {"active/rule.md": {"version": 1, "text": "Previously stored.", "readers": ["alice"]}}
        plan = self.plan(previous)
        self.assertEqual(plan["commit"], self.commit_id)
        self.assertEqual(plan["missing_links"], [])
        self.assertEqual(plan["inventory"]["active/rule.md"]["sha256"], hashlib.sha256(self.files["active/rule.md"]).hexdigest())
        self.assertEqual(apply_git_import(plan, self.changes, actor="operator", dry_run=True)["changes"], 2)
        self.assertEqual(self.store.read("P1", "active/rule.md", subject="alice")["version"], 1)
        receipt = apply_git_import(plan, self.changes, actor="operator")
        self.assertEqual(apply_git_import(plan, self.changes, actor="operator"), receipt)
        self.assertEqual(self.store.read("P1", "active/rule.md", subject="alice")["version"], 2)
        self.assertIsNone(self.store.read("P1", "archive/old.md", subject="alice"))
        self.changes.compensate(plan["change_id"], compensation_id="import-rollback", actor="operator")
        self.assertEqual(self.store.read("P1", "active/rule.md", subject="alice")["text"], "Previously stored.")
        self.assertIsNone(self.store.read("P1", "archive/old.md", subject="bob"))

    def test_missing_local_link_blocks_apply(self):
        self.files["active/rule.md"] = b"Broken [link](absent.md).\n"
        plan = self.plan({})
        self.assertTrue(plan["missing_links"])
        with self.assertRaises(ValueError):
            apply_git_import(plan, self.changes, actor="operator")
        self.assertEqual(self.store.snapshot("P1", subject="alice"), [])
