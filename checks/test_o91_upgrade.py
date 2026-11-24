import http.client
import json
import os
import re
import sqlite3
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from urllib.parse import urlsplit

from brain.cli_factory import LocalKnowledgeClient
from brain.operations.recovery import backup, restore
from brain.write_access import ApprovalRegistry
from checks import test_o91_live as harness


PROJECT = harness.PROJECT
TRACE = harness.TRACE
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'",
}
RULE = "active-rule"
SOURCE = "support-rule"


class ApprovalAndSecretContract(unittest.TestCase):
    def test_token_is_reserved_through_commit_and_failed_commit_keeps_it(self):
        registry = ApprovalRegistry()
        request = {"project_id": PROJECT}
        token = registry.issue("session", "write", request)
        body = {**request, "approval": token}
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with registry.commit("session", "write", body):
                raise RuntimeError("rollback")
        self.assertTrue(registry.valid("session", "write", body))
        barrier = Barrier(8)

        def attempt(_):
            other = registry.issue("other", "write", request)
            self.assertTrue(registry.consume("other", "write", {**request, "approval": other}))
            barrier.wait(timeout=3)
            try:
                with registry.commit("session", "write", body):
                    return True
            except PermissionError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(8))), 1)
        self.assertFalse(registry.valid("session", "write", body))

    def test_session_exchange_never_returns_a_secret_or_leaks_it_in_an_error(self):
        secret = "X" * 43
        env = {"BRAIN_KNOWLEDGE_URL": "http://127.0.0.1:12345", "BRAIN_PROJECT_ID": PROJECT,
               "BRAIN_CREDENTIAL_FILE": "/synthetic"}
        with patch.dict(os.environ, env), patch("brain.cli_factory._credential", return_value=secret):
            for echo in ({"token": secret}, ValueError(f"server reflected {secret}")):
                with self.subTest(kind=type(echo).__name__):
                    with patch.object(LocalKnowledgeClient, "request") as request:
                        if isinstance(echo, Exception):
                            request.side_effect = echo
                        else:
                            request.return_value = echo
                        with self.assertRaisesRegex(ValueError, "^source operation refused$") as error:
                            LocalKnowledgeClient()
                        self.assertIsNone(error.exception.__context__)
                        self.assertNotIn(secret, str(error.exception))


@unittest.skipUnless(sys.platform == "darwin", "Requires native macOS Seatbelt")
class UpgradeAcceptance(unittest.TestCase):
    setUp = harness.LiveAcceptance.setUp
    stop_app = staticmethod(harness.LiveAcceptance.stop_app)
    request = harness.LiveAcceptance.request
    api = harness.LiveAcceptance.api
    browser = harness.LiveAcceptance.browser
    cli = harness.LiveAcceptance.cli
    change = staticmethod(harness.LiveAcceptance.change)
    confirm = harness.LiveAcceptance.confirm

    def start_app(self):
        config = json.loads(self.config.read_text())
        config["limits"].update(active_documents=12, versions=50)
        self.config.write_text(json.dumps(config))
        self.app = harness.LiveAcceptance.start_app(self)
        return self.app

    def browser_login(self, credential):
        status, _, headers = self.browser("POST", "/login", {"credential": credential})
        self.assertEqual(status, 303)
        self.assert_headers(headers)
        return headers["Set-Cookie"].split(";", 1)[0]

    def assert_headers(self, headers):
        for name, value in HEADERS.items():
            self.assertEqual(headers.get(name), value)

    def approve(self, task, sha, cookie):
        page = "/review/" + task
        status, html, headers = self.browser("GET", page, cookie=cookie)
        self.assertEqual(status, 200)
        self.assert_headers(headers)
        csrf = re.search(r'name="form_token" value="([^"]+)"', html)[1]
        result = self.browser("POST", page + "/decision", {
            "form_token": csrf, "candidate_sha": sha, "decision": "approve", "comment": "",
        }, cookie=cookie)
        self.assertEqual(result[0], 303, result)
        self.assert_headers(result[2])

    def draft_review(self, task, readers, *, rule=RULE, sources=SOURCE, metadata=None):
        change = self.change("Grounded local rule", doc=rule, readers=readers)
        if metadata is not None:
            change["rule"] = metadata
        draft = self.api("/drafts", token=self.writer, draft_id=task, change=change)
        self.assertEqual(draft[0], 201, draft)
        sha = ("%040x" % (len(task) * 101 + sum(task.encode())))
        handoff = {
            "goal": "Review local fixture", "artifact": "Grounded rule", "verification": "Run local check",
            "boundary": "Synthetic", "assignee": "writer", "constraints": ["synthetic"],
            "criteria": ["readable rule"], "exceptions": [],
            "source": [{"rule_id": sources, "revision": 1, "source": f"doc:{sources}@1"}],
        }
        matched = {"task_id": task, "candidate_sha": sha, **draft[1]}
        queued = self.api("/reviews", token=self.writer, handoff=handoff, **matched)
        self.assertEqual(queued[0], 201, queued)
        return {**matched, "change_id": "publish-" + task}

    def resolve_in_seatbelt(self, rule_id):
        result = self.cli("run", (
            'import os\n'
            'assert not os.environ\n'
            f'value = sdk_call("resolve_rule", rule_id={rule_id!r})\n'
            'result = {"value": value, "grounding_refs": [value["source"]] if "source" in value else []}\n'
        ))
        self.assertEqual(result["status"], "ok", result)
        return result["result"]

    def test_upgrade_on_live_mac_and_restore_snapshot(self):
        readers = ["agent", "reviewer", "writer"]
        prior = {"change_id": "baseline-u3", "changes": [self.change("Source fragment", doc=SOURCE, readers=readers)]}
        approval = self.confirm("write", prior, token=self.agent)
        self.assertEqual(self.api("/write", token=self.agent, approval=approval, **prior)[0], 200)
        self.assertEqual(self.api("/write", token=self.agent, approval=approval, **prior)[0], 403)
        stale = {**prior, "change_id": "stale-u1"}
        stale_approval = self.confirm("write", stale, token=self.agent)
        for _ in range(2):
            self.assertEqual(self.api("/write", token=self.agent, approval=stale_approval, **stale)[0], 409)

        for method, route, expected in (("GET", "/login", 200), ("GET", "/ops", 403), ("GET", "/", 403)):
            status, _, headers = self.browser(method, route)
            self.assertEqual(status, expected)
            self.assert_headers(headers)
        address = urlsplit(self.review_url)
        with closing(http.client.HTTPConnection(address.hostname, address.port, timeout=3)) as conn:
            conn.putrequest("POST", "/login")
            conn.putheader("Content-Type", "application/x-www-form-urlencoded")
            conn.putheader("Content-Length", "+10")
            conn.endheaders(b"0123456789")
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            self.assert_headers(dict(response.getheaders()))
            response.read()

        operator = self.browser_login("reviewer-secret")
        writer = self.browser_login("writer-secret")
        forbidden_ops = self.browser("GET", "/ops", cookie=writer)
        self.assertEqual(forbidden_ops[0], 403)
        self.assert_headers(forbidden_ops[2])
        self.assertIn('href="/ops"', self.browser("GET", "/", cookie=operator)[1])

        blocked = self.draft_review("without-publisher", ["writer"], rule="writer-only")
        self.approve(blocked["task_id"], blocked["candidate_sha"], operator)
        approval = self.confirm("publish", blocked, token=self.reviewer)
        self.assertEqual(self.api("/publish", token=self.reviewer, approval=approval, **blocked)[0], 403)
        self.assertEqual(self.api("/read", token=self.writer, doc_id="writer-only")[0], 404)

        live = self.draft_review("readable-publication", readers, metadata={"source": f"doc:{SOURCE}@1"})
        pending = self.draft_review("pending-recovery", readers, rule="next-rule")
        self.assertEqual(self.api("/check", token=self.writer, task_id=live["task_id"], rule_ids=[SOURCE],
                                  deliveries=[{"check": "grounding", "run": "run-u3", "result_id": "result-u3",
                                               "outcome": "pass"}])[0], 200)
        report = self.api("/ops/audit-summary", token=self.reviewer)[1]
        queue_lag = report["telemetry"]["queue_lag_seconds"]
        self.assertGreater(queue_lag, 0)
        self.assertIn("max_ms", report["telemetry"]["steps"]["check"])
        status, ops, headers = self.browser("GET", "/ops", cookie=operator)
        self.assertEqual(status, 200)
        self.assert_headers(headers)
        lag = re.search(r"queue_lag_seconds</dt><dd><span[^>]*>([0-9.]+)", ops)
        self.assertIsNotNone(lag)
        self.assertGreater(float(lag[1]), 0)
        for label in ("Observer", "recovery_owner", "policy_denied", "avg_ms", "max_ms"):
            self.assertIn(label, ops)

        self.approve(live["task_id"], live["candidate_sha"], operator)
        approval = self.confirm("publish", live, token=self.reviewer)
        self.assertEqual(self.api("/publish", token=self.reviewer, approval=approval, **live)[0], 200)
        self.assertEqual(self.api("/publish", token=self.reviewer, approval=approval, **live)[0], 403)
        self.assertEqual(self.api("/read", token=self.reviewer, doc_id=RULE)[0], 200)
        output = self.resolve_in_seatbelt(RULE)
        self.assertEqual(output["value"]["fragment"], "Source fragment")
        self.assertEqual(output["grounding_refs"], [f"doc:{SOURCE}@1"])
        report = self.api("/ops/audit-summary", token=self.reviewer)[1]
        self.assertIn(("source_read", "ok"), [(item["step"], item["outcome"]) for item in report["events"]])
        self.assertEqual(report["observer"]["sequence_gap"], 0)

        response = self.api("/impact", token=self.reviewer, rule_id=SOURCE, kind="result", identifier="result-u3")[1]
        self.assertIn(live["task_id"], response["affected"]["tasks"])
        self.assertEqual(response["affected"]["checks"], ["grounding"])
        self.assertEqual(response["rules_for"], [SOURCE])
        self.assertIn(RULE, response["uncovered_rules"])
        other = self.api("/impact", token=self.other, project="P2", rule_id=SOURCE, kind="result",
                         identifier="result-u3", trace="foreign-project")[1]
        self.assertEqual(other["affected"]["tasks"], [])
        self.assertEqual(other["rules_for"], [])
        other_cookie = self.browser_login("other-secret")
        self.assertNotIn(TRACE, self.browser("GET", "/ops", cookie=other_cookie)[1])

        self.assertEqual(self.api("/write", token=self.writer, change_id="private-source", changes=[
            self.change("Private fragment", doc="private-source", readers=["writer"])])[0], 200)
        private = self.draft_review("private-related-task", ["writer"], rule="private-rule", sources="private-source")
        impact = self.api("/impact", token=self.reviewer, kind="task", identifier=private["task_id"])[1]
        self.assertNotIn("private-source", repr(impact))
        self.assertEqual(impact["rules_for"], [])
        self.assertNotIn(private["task_id"], repr(self.api("/impact", token=self.reviewer, rule_id=SOURCE)[1]))

        overdue = self.draft_review("overdue-publication", readers, rule="overdue-rule",
                                    metadata={"review_on": "2000-01-01"})
        self.approve(overdue["task_id"], overdue["candidate_sha"], operator)
        expired = self.confirm("publish", overdue, token=self.reviewer)
        denied = self.api("/publish", token=self.reviewer, approval=expired, **overdue)
        self.assertEqual((denied[0], denied[1]["issues"]), (422, ["review_overdue"]))
        self.assertEqual(self.api("/read", token=self.reviewer, doc_id="overdue-rule")[0], 404)

        snapshot = self.root / "knowledge-before-recovery.zip"
        data = self.root / "data"
        self.stop_app(self.app)
        expected = self.database_snapshot(data)
        saved = backup(data, snapshot)
        self.assertEqual(set(saved["files"]), {"knowledge.db", "audit.db"})
        data.rename(self.root / "original-data")
        restore(snapshot, data)
        self.assertEqual(self.database_snapshot(data), expected)
        self.assertTrue(all(len(rows) for rows in expected.values()))

        directive_paths = {}
        for level in ("root", "organization", "project", "workflow"):
            doc = self.root / level / "AGENTS.md"
            doc.parent.mkdir()
            doc.write_text(f"# Project policy\n\nScope: {PROJECT}\n")
            directive_paths[level] = str(doc.relative_to(self.root))
        config = json.loads(self.config.read_text())
        config["directive_files"] = directive_paths
        self.config.write_text(json.dumps(config))
        self.start_app()
        self.agent = self.api("/session", credential="agent-secret")[1]["token"]
        self.reviewer = self.api("/session", credential="reviewer-secret")[1]["token"]
        operator = self.browser_login("reviewer-secret")

        def card(task_id):
            status, body, _ = self.browser("GET", f"/api/reviews/{task_id}", cookie=operator)
            self.assertEqual(status, 200)
            return json.loads(body)

        saved_card = card(live["task_id"])
        self.assertEqual(saved_card["published_change_id"], live["change_id"])
        self.assertIsNone(saved_card["compensated_by"])
        self.assertEqual(saved_card["impact"]["affected"][SOURCE]["checks"], ["grounding"])
        self.assertTrue(saved_card["queued_at"].endswith("+00:00"))
        self.assertIn(pending["task_id"], self.browser("GET", "/", cookie=operator)[1])
        self.assertEqual(self.resolve_in_seatbelt(RULE)["value"]["outcome"], "result")

        stale_source = {"change_id": "restricted-source", "changes": [
            self.change("Source is now restricted", 1, doc=SOURCE, readers=["reviewer", "writer"])]}
        new_source_approval = self.confirm("write", stale_source, token=self.reviewer)
        self.assertEqual(self.api("/write", token=self.reviewer, approval=new_source_approval, **stale_source)[0], 200)
        response = self.resolve_in_seatbelt(RULE)
        self.assertEqual(response["value"], {"outcome": "needs_clarification", "reason": "source_unavailable"})

        # Advance the stored synthetic review deadline to exercise time-based clarification.
        with closing(sqlite3.connect(data / "knowledge.db")) as database, database:
            database.execute("UPDATE knowledge_cards SET review_on = '2000-01-01' WHERE knowledge_id = ?", (RULE,))
        expired_rule = self.api("/resolve", token=self.reviewer, rule_id=RULE)[1]
        self.assertEqual(expired_rule, {"outcome": "needs_clarification", "reason": "review_overdue"})

        self.approve(pending["task_id"], pending["candidate_sha"], operator)
        pending_approval = self.confirm("publish", pending, token=self.reviewer)
        workflow = self.root / directive_paths["workflow"]
        workflow.write_text("# Workflow\n\nScope: P2\n")
        for route, fields in (("/write", stale_source), ("/publish", {**pending, "approval": pending_approval})):
            status, body, _ = self.api(route, token=self.reviewer, **fields)
            self.assertEqual((status, body["outcome"]), (422, "OUTCOME_NONE_CLARIFICATION"))
            self.assertEqual(body["lower"]["ref"], "policy-file:workflow")
        workflow.write_text(f"Scope: {PROJECT}\n")
        self.assertEqual(self.api("/publish", token=self.reviewer, approval=pending_approval, **pending)[0], 200)
        self.assertEqual(
            self.api("/compensate", token=self.reviewer, change_id=pending["change_id"], compensation_id="undo-pending")[0],
            200,
        )
        self.assertEqual(self.api("/publish", token=self.reviewer, approval=pending_approval, **pending)[0], 403)
        report = self.api("/ops/audit-summary", token=self.reviewer)[1]
        self.assertGreaterEqual(sum(item["step"] == "directives" for item in report["events"]), 2)

        undo = self.api("/compensate", token=self.reviewer, change_id=live["change_id"], compensation_id="undo-u2")
        self.assertEqual(undo[0], 200, undo)
        self.assertEqual(self.api("/read", token=self.reviewer, doc_id=RULE)[0], 404)
        self.assertEqual(card(live["task_id"])["compensated_by"], "undo-u2")
        with closing(sqlite3.connect(data / "knowledge.db")) as database:
            history = database.execute(
                "SELECT published_change_id, compensated_by FROM review_decisions WHERE task_id = ?", (live["task_id"],)
            ).fetchone()
            self.assertEqual(history, (live["change_id"], "undo-u2"))
        self.assertEqual(self.request(self.api_url, "GET", "/ready")[0], 200)
        with closing(sqlite3.connect(data / "audit.db")) as database, database:
            database.execute("DELETE FROM audit_events WHERE seq = (SELECT MIN(seq) FROM audit_events)")
        status, raw, _ = self.request(self.api_url, "GET", "/ready")
        self.assertEqual((status, json.loads(raw)), (503, {"ready": False, "reason": "audit_sequence_gap"}))

        if destination := os.environ.get("BRAIN_UPGRADE_E2E_REPORT"):
            Path(destination).write_text(json.dumps({
                "scope": "closed_mac_synthetic", "native_seatbelt": True, "trace_id": TRACE,
                "U1": {"operator_can_compensate_own_publication": True, "approval_replay_rejected": True,
                       "conflict_retains_approval": True, "browser_security_headers": list(HEADERS)},
                "U2": {"restored_tables": sorted(expected), "confirmed_revisions_lost": 0,
                       "published_change_id": live["change_id"], "compensated_by": "undo-u2"},
                "U3": {"grounding_ref": output["grounding_refs"][0], "source_read_in_audit": True,
                       "scoped_impact": True, "file_conflicts_block_write_and_publish": True},
                "U4": {"queue_lag_seconds": queue_lag,
                       "browser_ops": True, "sequence_gap_triggers_503": True},
            }, indent=2) + "\n")

    def database_snapshot(self, data):
        with closing(sqlite3.connect(data / "knowledge.db")) as knowledge, closing(
            sqlite3.connect(data / "audit.db")
        ) as audit:
            for database in (knowledge, audit):
                self.assertEqual(database.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            tables = ("document_revisions", "drafts", "review_queue", "review_decisions", "knowledge_cards", "impact_links")
            snapshot = {table: sorted(knowledge.execute(f"SELECT * FROM {table}"), key=repr) for table in tables}
            return {**snapshot, "audit_events": list(audit.execute("SELECT * FROM audit_events ORDER BY seq"))}


if __name__ == "__main__":
    unittest.main()
