import io
import json
import unittest

from brain.adapters.mcp import CodeMode, serve_stdio
from brain.adapters.swarm_mcp import SwarmReadOnly


class SharedClientContract(unittest.TestCase):
    def test_code_execution_and_grounding_keep_the_bound_session(self):
        class SDK:
            def describe(self, session_id):
                if session_id != "bound-session":
                    raise PermissionError
                return {"revision": "r1", "operations": ["read"]}

        sdk = SDK()
        seen = []

        def run(source, *, sdk, session_id):
            seen.append((source, sdk, session_id))
            return {"status": "ok", "result": {"value": "evidence", "grounding_refs": [source]}}

        resolved = []

        def resolve(session, reference):
            resolved.append((session, reference))
            return session == "bound-session" and reference == "readable:doc"

        mode = CodeMode(sdk, session_id="bound-session", resolve_reference=resolve, executor=run)
        self.assertEqual(mode.start()["revision"], "r1")
        good = mode.run("readable:doc")
        self.assertEqual((good["status"], good["revision"], good["result"]["value"]), ("ok", "r1", "evidence"))
        forbidden = mode.run("other-project:doc")
        self.assertEqual((forbidden["status"], forbidden["result"]), ("invalid_result", None))
        self.assertEqual(seen, [("readable:doc", sdk, "bound-session"), ("other-project:doc", sdk, "bound-session")])
        self.assertEqual(resolved, [("bound-session", "readable:doc"), ("bound-session", "other-project:doc")])

    def test_swarm_stdio_keeps_the_query_only_input_contract(self):
        calls = []
        profile = SwarmReadOnly(lambda query, section: calls.append((query, section)) or {"items": ["a"]})
        def wire(id, method, params):
            return json.dumps({"jsonrpc": "2.0", "id": id, "method": method, "params": params})
        reader = io.StringIO("\n".join((
            wire(1, "initialize", {"protocolVersion": "2024-11-05"}),
            '{"jsonrpc":"2.0","method":"notifications/initialized"}',
            wire(2, "tools/list", {}),
            wire(3, "tools/call", {"name": "run", "arguments": {"query": "needle", "section": "plans"}}),
            wire(4, "tools/call", {"name": "run", "arguments": {"source": "result=2"}}),
        )) + "\n")
        writer = io.StringIO()
        serve_stdio(profile, reader, writer)
        replies = {reply["id"]: reply for reply in map(json.loads, writer.getvalue().splitlines())}
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual({tool["name"] for tool in replies[2]["result"]["tools"]}, {"start", "run"})
        self.assertIsInstance(replies[3]["result"]["structuredContent"], dict)
        self.assertTrue("error" in replies[4] or replies[4]["result"].get("isError") is True)
        self.assertEqual(calls, [("needle", "plans")])
