import json
import unittest

from brain.session import SessionRegistry
from brain.web_api import ReviewWeb


class ReviewScreen(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.row = {
            "task_id": "T01", "candidate_sha": self.sha, "status": "in_review",
            "brief": "Problem <text>", "changes": ["Patch one"], "risks": ["Risk one"],
            "sources": ["Source one"], "tests": ["Test one"], "feedback": ["Note one"],
        }
        self.calls = []
        sessions = SessionRegistry(
            lambda role: {"actor_id": "person", "role": role,
                          "project_id": "demo", "verified": True}
        )
        self.token = sessions.open("operator")
        self.reader = sessions.open("reader")
        self.web = ReviewWeb(
            sessions, list_reviews=lambda project: [self.row] if project == "demo" else [],
            get_review=lambda project, key: self.row if (project, key) == ("demo", "T01") else None,
            record_decision=self.record, form_token="form-secret",
        )

    def record(self, project, key, sha, decision, comment, actor):
        if (project, key, sha) != ("demo", "T01", self.row["candidate_sha"]):
            return False
        self.calls.append((project, key, sha, decision, comment, actor))
        self.row["status"] = {"approve": "approved", "return": "changes_requested"}[decision]
        return True

    def test_review_data_on_screen_and_api(self):
        status, kind, queue = self.web.dispatch("GET", "/", token=self.token)
        self.assertEqual(status, 200)
        self.assertTrue(kind.startswith("text/html"))
        self.assertIn("/review/T01", queue)
        status, _, card = self.web.dispatch("GET", "/review/T01", token=self.token)
        self.assertEqual(status, 200)
        for detail in ("Problem &lt;text&gt;", "Patch one", "Risk one", "Source one",
                       "Test one", "Note one", self.sha, "/review/T01/decision"):
            self.assertIn(detail, card)
        status, kind, record = self.web.dispatch("GET", "/api/reviews/T01", token=self.token)
        self.assertEqual(status, 200)
        self.assertTrue(kind.startswith("application/json"))
        self.assertEqual(json.loads(record)["status"], "in_review")

    def test_feedback_requires_operator_form_and_current_candidate(self):
        target = "/review/T01/decision"
        form = {"candidate_sha": self.sha, "form_token": "form-secret",
                "decision": "return", "comment": "Address the test"}
        status = lambda token, payload: self.web.dispatch("POST", target, token=token, form=payload)[0]
        self.assertEqual(status(self.reader, form), 403)
        self.assertEqual(status(self.token, dict(form, form_token="invalid")), 403)
        self.assertEqual(status(self.token, dict(form, candidate_sha="b" * 40)), 409)
        self.assertEqual(self.calls, [])
        self.assertEqual(status(self.token, form), 303)
        self.assertEqual(self.calls, [("demo", "T01", self.sha, "return", "Address the test", "person")])
        _, _, current = self.web.dispatch("GET", "/api/reviews/T01", token=self.token)
        self.assertEqual(json.loads(current)["status"], "changes_requested")
