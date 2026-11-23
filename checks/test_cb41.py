"""Independent checks for identity, project and exact approval."""

import unittest

from brain.policy import apply_write, authorize, request_digest


OPERATOR = {"actor_id": "operator", "verified": True, "project_id": "P1", "role": "operator"}
REQUEST = {"project_id": "P1", "action": "publish", "revision": "rule-v2", "key": "timeout", "value": 5000}


def approval(request=REQUEST):
    return {"verified": True, "project_id": request["project_id"], "revision": request["revision"],
            "request_digest": request_digest(request)}


class AccessPolicy(unittest.TestCase):
    def test_reader_can_read_but_cannot_write(self):
        reader = {**OPERATOR, "role": "reader"}
        read = {**REQUEST, "action": "read"}
        write = {**REQUEST, "action": "write"}
        self.assertTrue(authorize(reader, read, current_revision="rule-v2"))
        self.assertFalse(authorize(reader, write, current_revision="rule-v2"))
        self.assertFalse(authorize({**reader, "verified": False}, read, current_revision="rule-v2"))

    def test_approval_is_bound_to_the_project_revision_and_exact_body(self):
        approved = approval()
        self.assertTrue(authorize(OPERATOR, REQUEST, current_revision="rule-v2", approval=approved))
        self.assertFalse(authorize(OPERATOR, REQUEST, current_revision="rule-v2"))
        for invalid in ({**approved, "project_id": "P2"}, {**approved, "revision": "rule-v1"}):
            self.assertFalse(authorize(OPERATOR, REQUEST, current_revision="rule-v2", approval=invalid))
        self.assertFalse(authorize(OPERATOR, {**REQUEST, "value": 8000}, current_revision="rule-v2", approval=approved))
        self.assertFalse(authorize(OPERATOR, REQUEST, current_revision="rule-v3", approval=approved))

    def test_denied_action_never_reaches_commit(self):
        current = {"timeout": 3000}
        calls = []

        def commit(request, expected_revision):
            calls.append(expected_revision)
            current[request["key"]] = request["value"]
            return True

        self.assertFalse(apply_write(OPERATOR, REQUEST, current_revision="rule-v2", commit=commit))
        self.assertEqual((current["timeout"], calls), (3000, []))
        self.assertTrue(apply_write(OPERATOR, REQUEST, current_revision="rule-v2", approval=approval(), commit=commit))
        self.assertEqual((current["timeout"], calls), (5000, ["rule-v2"]))
