"""Independent request authorization and persistence checks."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.server import KnowledgeService
from brain.session import SessionRegistry
from brain.storage.changesets import ChangeSetStore
from brain.storage.sqlite_store import SQLiteKnowledgeStore


ACTORS = {
    "read-one": {"actor_id": "alice", "verified": True, "project_id": "P1", "role": "reader"},
    "write-one": {"actor_id": "alice", "verified": True, "project_id": "P1", "role": "writer"},
    "read-two": {"actor_id": "bob", "verified": True, "project_id": "P2", "role": "reader"},
}


class ServiceSessions(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "store.db"
        database = SQLiteKnowledgeStore(self.path)
        database.put_revision("P1", "rule", "active rule", ["alice"])
        database.put_revision("P2", "rule", "other rule", ["bob"])
        self.sessions = SessionRegistry(ACTORS.get)
        self.service = KnowledgeService(database, ChangeSetStore(self.path), self.sessions)

    def test_every_read_uses_session_scope_and_subject(self):
        request = {"project_id": "P1", "doc_id": "rule", "subject": "bob"}
        self.assertEqual(self.service.dispatch("POST", "/read", body=request)[0], 401)
        reader = self.sessions.open("read-one")
        self.assertEqual(self.service.dispatch("POST", "/read", token=reader, body=request),
                         (200, {"doc_id": "rule", "version": 1, "text": "active rule"}))
        self.assertEqual(self.service.dispatch("POST", "/read", token=reader, body={**request, "project_id": "P2"})[0], 403)
        self.sessions.revoke(reader)
        self.assertEqual(self.service.dispatch("POST", "/read", token=reader, body=request)[0], 401)

    def test_writer_conflict_and_reopen(self):
        request = {"project_id": "P1", "change_id": "new", "changes": [
            {"doc_id": "rule", "expected_version": 1, "text": "persisted rule", "readers": ["alice"]},
        ]}
        reader = self.sessions.open("read-one")
        self.assertEqual(self.service.dispatch("POST", "/write", token=reader, body=request)[0], 403)
        writer = self.sessions.open("write-one")
        self.assertEqual(self.service.dispatch("POST", "/write", token=writer, body=request)[0], 200)
        self.assertEqual(self.service.dispatch("POST", "/write", token=writer, body={**request, "change_id": "stale"})[0], 409)
        self.service.close()
        self.assertEqual(self.service.dispatch("GET", "/ready")[0], 503)
        restarted_sessions = SessionRegistry(ACTORS.get)
        service = KnowledgeService(SQLiteKnowledgeStore(self.path), ChangeSetStore(self.path), restarted_sessions)
        token = restarted_sessions.open("read-one")
        self.assertEqual(service.dispatch("POST", "/read", token=token, body={"project_id": "P1", "doc_id": "rule"})[1]["text"], "persisted rule")
