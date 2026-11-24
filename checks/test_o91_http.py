from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase

from brain.server import KnowledgeService, make_http_server
from brain.session import SessionRegistry
from brain.storage.changesets import ChangeSetStore
from brain.storage.sqlite_store import SQLiteKnowledgeStore


class ClosedHTTP(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        identity = {"actor_id": "operator", "project_id": "P1", "role": "operator", "verified": True}
        self.now = 0
        self.sessions = SessionRegistry(lambda _: identity, clock=lambda: self.now, ttl=10)
        path = Path(folder.name) / "test.db"
        self.service = KnowledgeService(SQLiteKnowledgeStore(path), ChangeSetStore(path), self.sessions)
        self.addCleanup(self.service.close)
        self.server = make_http_server(self.service)
        self.addCleanup(self.server.server_close)
        thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.token = self.sessions.open("example")

    def call(self, path, body=None, **headers):
        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            client.request("POST", path, body=None if body is None else json.dumps(body), headers={
                "Authorization": "Bearer " + self.token, **headers,
            })
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def test_request_size_and_bad_fts_input(self):
        self.assertEqual(self.call("/write", **{"Content-Length": "262145"}),
                         (413, {"error": "payload_too_large"}))
        self.assertEqual(self.call("/search", {"project_id": "P1", "query": '"unfinished'}),
                         (400, {"error": "invalid_request"}))
        self.assertEqual(self.call("/search", {"project_id": "P1", "query": "rule"}), (200, {"results": []}))

    def test_session_expiry_and_logout(self):
        one = self.sessions.csrf(self.token)
        self.assertNotEqual(one, self.sessions.csrf(self.sessions.open("example")))
        self.now = 10
        self.assertEqual(self.call("/search", {"project_id": "P1", "query": "rule"})[0], 401)
        self.assertIsNone(self.sessions.csrf(self.token))
        self.token = self.sessions.open("example")
        self.assertEqual(self.call("/logout")[0], 200)
        self.assertEqual(self.call("/search", {"project_id": "P1", "query": "rule"})[0], 401)
