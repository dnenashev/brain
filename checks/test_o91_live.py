import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode, urlsplit


TRACE = "o91-real-mac-e2e"
PROJECT = "P1"
SHA1 = hashlib.sha1(b"synthetic candidate v1").hexdigest()
SHA2 = hashlib.sha1(b"synthetic candidate v2").hexdigest()
REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "Requires native macOS Seatbelt")
class LiveAcceptance(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="brain-o91-live-")))
        self.config = self.root / "config.json"
        self.credentials = self.root / "credentials.json"
        self.credential = self.root / "cli.credential"
        self.credential.touch(mode=0o600)
        self.credential.write_text("agent-secret", encoding="utf-8")
        identities = [
            ("agent-secret", "agent", PROJECT, "operator"),
            ("reviewer-secret", "reviewer", PROJECT, "operator"),
            ("writer-secret", "writer", PROJECT, "writer"),
            ("other-secret", "other", "P2", "operator"),
        ]
        self.credentials.touch(mode=0o600)
        self.credentials.write_text(json.dumps([
            {"credential_sha256": hashlib.sha256(secret.encode()).hexdigest(),
             "actor_id": actor, "project_id": project, "role": role}
            for secret, actor, project, role in identities
        ]), encoding="utf-8")
        self.config.write_text(json.dumps({
            "bind": "127.0.0.1", "http_port": 0, "review_port": 0,
            "data_dir": str(self.root / "data"), "credential_file": str(self.credentials),
            "limits": {"document_bytes": 1024, "active_documents": 2, "versions": 12},
            "directives": {PROJECT: {"organization": PROJECT, "project": PROJECT, "workflow": PROJECT}},
        }), encoding="utf-8")
        self.start_app()
        self.agent = self.api("/session", credential="agent-secret")[1]["token"]
        self.reviewer = self.api("/session", credential="reviewer-secret")[1]["token"]
        self.writer = self.api("/session", credential="writer-secret")[1]["token"]
        self.other = self.api("/session", credential="other-secret")[1]["token"]

    def start_app(self):
        ready = self.root / "data" / "ready.json"
        logfile = self.enterContext((self.root / "deploy.log").open("a+", encoding="utf-8"))
        app = subprocess.Popen([sys.executable, "-W", "error::ResourceWarning", "-m", "deploy.app",
                                "--config", str(self.config)], cwd=REPO, stdout=logfile, stderr=logfile)
        self.addCleanup(self.stop_app, app)
        deadline = time.monotonic() + 35
        while not ready.is_file() and app.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not ready.is_file():
            logfile.seek(0)
            self.fail("Deployment did not become ready: " + logfile.read())
        addresses = json.loads(ready.read_text())
        self.api_url = addresses["knowledge_url"]
        self.review_url = addresses["review_url"]
        return app

    @staticmethod
    def stop_app(app):
        if app.poll() is None:
            app.terminate()
        try:
            app.wait(timeout=6)
        except subprocess.TimeoutExpired:
            app.kill()
            app.wait(timeout=2)

    def request(self, base, method, path, data=None, headers=None):
        address = urlsplit(base)
        with closing(http.client.HTTPConnection(address.hostname, address.port, timeout=3)) as connection:
            connection.request(method, path, data, headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8"), dict(response.getheaders())

    def api(self, path, *, token=None, project=PROJECT, trace=TRACE, **fields):
        headers = {"Content-Type": "application/json"}
        if trace is not None:
            headers["X-Trace-Id"] = trace
        if token is not None:
            headers["Authorization"] = "Bearer " + token
            fields["project_id"] = project
        status, response, returned = self.request(self.api_url, "POST", path, json.dumps(fields), headers)
        if trace == TRACE:
            self.assertEqual(returned["X-Trace-Id"], trace)
        return status, json.loads(response), returned

    def browser(self, method, path, form=None, *, cookie=None):
        headers = {} if cookie is None else {"Cookie": cookie}
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self.request(self.review_url, method, path, urlencode(form) if form is not None else None, headers)

    def cli(self, *args, factory="build_code_mode"):
        env = {**os.environ, "BRAIN_KNOWLEDGE_URL": self.api_url, "BRAIN_CREDENTIAL_FILE": str(self.credential),
               "BRAIN_PROJECT_ID": PROJECT, "BRAIN_TRACE_ID": TRACE}
        call = subprocess.run([sys.executable, "-m", "brain.cli", "--factory",
                               "brain.cli_factory:" + factory, *args], cwd=REPO,
                              env=env, capture_output=True, text=True, timeout=25)
        self.assertEqual(call.returncode, 0, call.stdout + call.stderr)
        return json.loads(call.stdout)

    @staticmethod
    def change(text, expected=0, *, doc="timeout-rule", readers=None, sources=None):
        change = {"doc_id": doc, "expected_version": expected, "text": text,
                  "readers": readers if readers is not None else ["agent", "reviewer"]}
        return change if sources is None else {**change, "sources": sources}

    def confirm(self, action, body, *, token):
        response = self.api("/approval", action=action, request={"project_id": PROJECT, **body}, token=token)
        self.assertEqual(response[0], 200, response)
        return response[1]["approval"]

    def run_candidate(self, sha, proposed):
        script = (
            'import os\n'
            'assert not os.environ\n'
            'rule = sdk_call("read", doc_id="timeout-rule", version=1)\n'
            'ref = "doc:timeout-rule@1"\n'
            f'change = {self.change(proposed, 1, sources=["doc:timeout-rule@1"])!r}\n'
            'draft = sdk_call("draft", draft_id="draft-timeout", change=change)\n'
            'check = sdk_call("check", deliveries=[{"check":"grounded","run":"seatbelt","outcome":"pass"}])\n'
            'assert check["acceptance"] == "pass"\n'
            'handoff = {"goal":"Adjust synthetic timeout", "artifact":change["text"], '
            '"verification":"Native Seatbelt live check", "boundary":"Local synthetic rule", '
            '"assignee":"agent", "constraints":["Synthetic"], "criteria":["Grounded"], '
            '"exceptions":[], "source":[{"rule_id": rule["doc_id"], "revision": rule["version"], "source": ref}]}\n'
            'review = sdk_call("task", task_id="timeout-review", '
            f'candidate_sha="{sha}", draft_id=draft["draft_id"], revision=draft["revision"], handoff=handoff)\n'
            'result = {"value": {"original":rule["text"], "draft":draft, "review":review, "handoff":handoff}, '
            '"grounding_refs":[ref]}\n'
        )
        result = self.cli("run", script)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["result"]["grounding_refs"], ["doc:timeout-rule@1"])
        self.assertEqual(result["result"]["value"]["original"], "timeout baseline 250")
        return result["result"]["value"]

    def test_seatbelt_through_browser_approval_publication_and_restore(self):
        body = {"change_id": "baseline", "changes": [self.change("timeout baseline 250")]}
        approval = self.confirm("write", body, token=self.agent)
        self.assertEqual(self.api("/write", token=self.agent, approval=approval, **body)[0], 200)
        self.assertEqual(self.cli("start")["revision"], "knowledge-http-v1")
        self.assertEqual(self.cli("start", factory="build_swarm_readonly")["operations"], ["search"])
        self.assertEqual(self.cli("run", "baseline", factory="build_swarm_readonly")[0]["doc_id"], "timeout-rule")
        self.assertEqual(self.cli("run", 'result = {"value": 0, "grounding_refs": ["doc:missing@1"]}')["status"],
                         "invalid_result")

        status, _, headers = self.browser("POST", "/login", {"credential": "reviewer-secret"})
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertNotIn("timeout-review", self.browser("GET", "/", cookie=cookie)[1])

        first = self.run_candidate(SHA1, "timeout candidate 400")
        retry = {"task_id": "timeout-review", "candidate_sha": SHA1, "handoff": first["handoff"], **first["draft"]}
        self.assertEqual(self.api("/reviews", token=self.agent, **retry)[0], 200)
        self.assertIn("timeout-review", self.browser("GET", "/", cookie=cookie)[1])
        self.browser_decision(cookie, SHA1, "return", "", expected=400)
        self.browser_decision(cookie, SHA1, "return", "Set the agreed timeout.")
        second = self.run_candidate(SHA2, "timeout candidate 300")
        self.browser_decision(cookie, SHA1, "approve", "", expected=409)
        self.browser_decision(cookie, SHA2, "approve", "")

        publication = {"task_id": "timeout-review", "candidate_sha": SHA2,
                       "change_id": "published-timeout", **second["draft"]}
        self.assertEqual(self.api("/publish", token=self.reviewer, **publication)[0], 403)
        approval = self.confirm("publish", publication, token=self.reviewer)
        self.assertEqual(self.api("/publish", token=self.reviewer, **{**publication, "revision": 1},
                                  approval=approval)[0], 403)
        published = self.api("/publish", token=self.reviewer, **publication, approval=approval)
        self.assertEqual(published[0], 200, published)
        self.assertEqual(published[1]["versions"], {"timeout-rule": 2})
        self.assertEqual(self.api("/read", token=self.reviewer, doc_id="timeout-rule")[1]["text"],
                         "timeout candidate 300")

        undo = {"change_id": "published-timeout", "compensation_id": "undo-timeout"}
        self.assertEqual(self.api("/compensate", token=self.writer, **undo)[0], 403)
        self.assertEqual(self.api("/compensate", token=self.other, project="P2", **undo)[0], 403)
        restored = self.api("/compensate", token=self.reviewer, **undo)
        self.assertEqual(restored[0], 200, restored)
        self.assertEqual(restored[1]["versions"], {"timeout-rule": 3})
        self.assertEqual(self.api("/compensate", token=self.reviewer, **undo)[:2], restored[:2])
        self.assertEqual(self.api("/read", token=self.reviewer, doc_id="timeout-rule")[1]["text"],
                         "timeout baseline 250")

        self.assertEqual(self.api("/ops/audit-summary", token=self.writer)[0], 403)
        report = self.api("/ops/audit-summary", token=self.reviewer)[1]
        self.assertEqual(report["observer"]["sequence_gap"], 0)
        self.assertEqual(report["telemetry"]["returns"], 1)
        self.assertEqual(report["telemetry"]["versions"], 3)
        steps = [(event["step"], event["outcome"]) for event in report["events"]]
        expected = [("write", "ok"), ("read", "ok"), ("draft", "ok"), ("check", "ok"),
                    ("review_enqueued", "ok"), ("decision", "returned"), ("read", "ok"),
                    ("draft", "ok"), ("check", "ok"), ("review_enqueued", "ok"),
                    ("decision", "approved"), ("publish", "policy_denied"), ("publish", "ok"),
                    ("compensate", "ok"), ("read", "ok")]
        positions = iter(steps)
        for wanted in expected:
            self.assertIn(wanted, positions, steps)
        self.assertTrue(all(event["trace_id"] == TRACE for event in report["events"]))
        other = self.api("/ops/audit-summary", token=self.other, project="P2")[1]
        self.assertNotIn(("publish", "ok"), [(item["step"], item["outcome"]) for item in other["events"]])

        logout = self.browser("GET", "/", cookie=cookie)[1]
        csrf = re.search(r'name="form_token" value="([^"]+)"', logout)[1]
        status, _, headers = self.browser("POST", "/logout", {"form_token": csrf}, cookie=cookie)
        self.assertEqual(status, 303)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.browser("GET", "/", cookie=cookie)[0], 403)

        if destination := os.environ.get("BRAIN_E2E_REPORT"):
            Path(destination).write_text(json.dumps({
                "native_seatbelt": True, "seeds": False, "first_sha": SHA1, "corrected_sha": SHA2,
                "grounding_refs": ["doc:timeout-rule@1"],
                "published_revision": published[1]["versions"]["timeout-rule"],
                "restored_revision": restored[1]["versions"]["timeout-rule"],
                "trace": report,
            }, indent=2) + "\n", encoding="utf-8")

    def browser_decision(self, cookie, sha, decision, comment, *, expected=303):
        status, html, _ = self.browser("GET", "/review/timeout-review", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('name="candidate_sha"', html)
        csrf = re.search(r'name="form_token" value="([^"]+)"', html)[1]
        result = self.browser("POST", "/review/timeout-review/decision", {
            "form_token": csrf, "candidate_sha": sha, "decision": decision, "comment": comment,
        }, cookie=cookie)
        self.assertEqual(result[0], expected, result)

    def test_live_guards_reject_atomically_and_scope_trace(self):
        only_agent = ["agent"]
        original = self.change("initial", readers=only_agent)
        self.assertEqual(self.api("/write", token=self.agent, change_id="seed", changes=[original])[0], 200)
        self.assertEqual(self.api("/write", token=self.writer, change_id="private-writer", changes=[
            self.change("not allowed", 1, readers=["writer"])]
        )[0], 403)
        self.assertEqual(self.api("/write", token=self.agent, change_id="external-reader", changes=[
            self.change("not allowed", 1, readers=["agent", "other"])]
        )[0], 403)
        update = self.change("new state", 1, readers=only_agent)
        invalid = self.change("bad link", doc="another", readers=only_agent, sources=["doc:private-p2@1"])
        third = self.change("rejected", doc="third", readers=only_agent)
        self.assertEqual(self.api("/write", token=self.other, project="P2", change_id="other", changes=[
            self.change("scope private", doc="private-p2", readers=["other"])])[0], 200)
        self.assertEqual(self.api("/write", token=self.agent, change_id="scoped-sources",
                                  changes=[update, invalid])[0], 422)
        self.assertEqual(self.api("/write", token=self.agent, change_id="size",
                                  changes=[{**update, "text": "x" * 1025}])[0], 413)
        self.assertEqual(self.api("/write", token=self.agent, change_id="quota",
                                  changes=[update, {**invalid, "sources": ["doc:timeout-rule@1"]}, third])[0], 422)
        status, response, _ = self.api("/write", token=self.agent, change_id="directive", changes=[update],
                                       instruction_scope="P2")
        self.assertEqual((status, response["outcome"]), (422, "OUTCOME_NONE_CLARIFICATION"))
        self.assertEqual(self.api("/read", token=self.agent, doc_id="timeout-rule")[1]["version"], 1)
        self.assertEqual(self.api("/read", token=self.agent, doc_id="another")[0], 404)
        status, _, headers = self.api("/read", token=self.agent, doc_id="timeout-rule", trace=None)
        self.assertEqual(status, 200)
        self.assertRegex(headers["X-Trace-Id"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.api("/read", token=self.agent, doc_id="timeout-rule", trace="invalid trace")[0], 400)
        report = self.api("/ops/audit-summary", token=self.agent)[1]
        self.assertIn("directives", [item["step"] for item in report["events"]])


if __name__ == "__main__":
    unittest.main()
