from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from brain.server import KnowledgeService
from brain.session import SessionRegistry
from brain.storage.changesets import ChangeSetStore
from brain.storage.sqlite_store import SQLiteKnowledgeStore


class DocumentWriteAccess(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "knowledge.db"
        identities = {
            "owner": self.actor("owner", "operator"),
            "editor": self.actor("editor", "writer"),
            "external": self.actor("external", "operator", "other"),
        }
        sessions = SessionRegistry(identities.get)
        self.tokens = {name: sessions.open(name) for name in identities}
        self.store = SQLiteKnowledgeStore(path)
        self.service = KnowledgeService(
            self.store, ChangeSetStore(path), sessions,
            project_subjects={"local": {"owner", "editor"}, "other": {"external"}},
        )

    @staticmethod
    def actor(name, role, project="local"):
        return {"actor_id": name, "role": role, "project_id": project, "verified": True}

    def dispatch(self, actor, path, **body):
        return self.service.dispatch("POST", path, token=self.tokens[actor], body={"project_id": "local", **body})

    @staticmethod
    def change(readers, version=1, key="rule"):
        return {"doc_id": key, "expected_version": version, "text": "updated rule", "readers": readers}

    def test_private_revision_and_batch_are_preserved(self):
        self.store.put_revision("local", "rule", "original", ["owner"])
        self.assertEqual(self.dispatch("editor", "/write", change_id="batch", changes=[
            self.change(["editor"], 0, "new"), self.change(["editor"]),
        ])[0], 403)
        self.assertIsNone(self.store.read("local", "new", subject="editor"))
        self.assertEqual(self.store.read("local", "rule", subject="owner")["version"], 1)
        self.assertEqual(self.store.read("local", "rule", subject="owner", version=1)["text"], "original")

    def test_reader_list_requires_membership_and_confirmed_operator_action(self):
        self.store.put_revision("local", "rule", "original", ["owner", "editor"])
        self.assertEqual(self.dispatch("editor", "/write", change_id="narrow", changes=[self.change(["editor"])])[0], 403)
        for actor in ("owner", "editor"):
            for reader in ("external", "unknown"):
                self.assertEqual(self.dispatch(actor, "/write", change_id="bad", changes=[self.change([actor, reader])])[0], 403)
        self.assertEqual(self.store.read("local", "rule", subject="owner")["version"], 1)
        request = {"project_id": "local", "change_id": "narrow", "changes": [self.change(["owner"])]}
        self.assertEqual(self.dispatch("owner", "/write", change_id="narrow", changes=request["changes"])[0], 403)
        status, body = self.dispatch("owner", "/approval", action="write", request=request)
        self.assertEqual(status, 200)
        self.assertEqual(self.dispatch("owner", "/write", change_id="different", changes=request["changes"],
                                       approval=body["approval"])[0], 403)
        self.assertEqual(self.dispatch("owner", "/write", change_id="narrow", changes=request["changes"],
                                       approval=body["approval"])[0], 200)
        self.assertEqual(self.store.read("local", "rule", subject="owner", version=1)["text"], "original")
