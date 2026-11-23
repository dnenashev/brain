"""Independent checks for source routing and credential isolation."""

import unittest

from brain.adapters.secret_broker import SecretBroker
from brain.adapters.sources import SourceAdapter


ACTOR = {"actor_id": "editor", "verified": True, "project_id": "P1", "role": "writer"}
REQUEST = {"project_id": "P1", "revision": "v1", "source_id": "git", "path": "docs/source.md", "action": "read"}
DEST = "https://git.example.test"  # Transport is a synthetic callback.


class SourceBoundaries(unittest.TestCase):
    def setUp(self):
        self.loads = []
        self.reads = []
        self.writes = []

        def load(source_id):
            self.loads.append(source_id)
            return "synthetic-credential"

        def read(destination, path, secret):
            self.reads.append((destination, path, secret))
            return "# Synthetic source"

        def write(destination, path, content, expected_revision, secret):
            self.writes.append((destination, path, content, expected_revision, secret))
            return True

        self.broker = SecretBroker(load)
        self.read_callback = read
        self.write_callback = write

    def bridge(self, **overrides):
        opts = {"allowlist": {DEST}, "broker": self.broker, "read_transport": self.read_callback,
                "write_transport": self.write_callback, **overrides}
        return SourceAdapter({"git": {"project_id": "P1", "destination": DEST, "writable": True}}, **opts)

    def test_authorized_read_and_write_use_distinct_transports(self):
        bridge = self.bridge()
        self.assertEqual(bridge.read(ACTOR, REQUEST, current_revision="v1"),
                         {"outcome": "result", "content": "# Synthetic source"})
        self.assertEqual(self.reads, [(DEST, "docs/source.md", "synthetic-credential")])
        self.assertEqual(self.writes, [])
        change = {**REQUEST, "action": "write", "content": "updated"}
        self.assertEqual(bridge.write(ACTOR, change, current_revision="v1")["outcome"], "written")
        self.assertEqual(self.writes, [(DEST, "docs/source.md", "updated", "v1", "synthetic-credential")])

    def test_denied_routes_do_not_load_credentials(self):
        self.assertEqual(self.bridge(allowlist=set()).read(ACTOR, REQUEST, current_revision="v1")["outcome"], "refused")
        self.assertEqual(self.bridge().read({**ACTOR, "project_id": "P2"}, REQUEST, current_revision="v1")["outcome"], "refused")
        self.assertEqual(self.loads, [])

    def test_unavailable_source_refuses_without_forwarding_transport_error(self):
        def unavailable(destination, path, secret):
            raise RuntimeError(f"bad token {secret}")

        result = self.bridge(read_transport=unavailable).read(ACTOR, REQUEST, current_revision="v1")
        self.assertEqual(result["outcome"], "refused")
        self.assertNotIn("synthetic-credential", repr(result))
