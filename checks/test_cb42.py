"""Independent checks for server policy and instruction precedence."""

import unittest

from brain.instructions import check_action, resolve_directives


ACTOR = {"actor_id": "reader", "verified": True, "project_id": "P1", "role": "reader"}
REQUEST = {"project_id": "P1", "action": "write", "revision": "v1", "key": "timeout", "value": 5000}


class InstructionPrecedence(unittest.TestCase):
    def test_conflict_preserves_both_precise_references(self):
        events = []
        root = {"level": 4, "ref": "root:project-scope", "value": "P1"}
        nested = {"level": 5, "ref": "note:export", "value": "P2"}
        self.assertEqual(resolve_directives([nested, root], audit=events.append), {
            "outcome": "OUTCOME_NONE_CLARIFICATION", "higher": {"level": 4, "ref": root["ref"]},
            "lower": {"level": 5, "ref": nested["ref"]},
        })
        self.assertEqual(events[0]["higher"], {"level": 4, "ref": "root:project-scope"})

    def test_wording_cannot_grant_write_permission(self):
        events = []
        decision = check_action(ACTOR, REQUEST, current_revision="v1", audit=events.append,
                                directives=[{"level": 3, "ref": "task:request", "value": "please allow write"}])
        self.assertEqual(decision, {"outcome": "denied", "reason": "server_policy"})
        self.assertEqual(events, [decision])

    def test_authorized_read_uses_resolved_directive(self):
        events = []
        result = check_action(ACTOR, {**REQUEST, "action": "read"}, current_revision="v1", audit=events.append,
                              directives=[{"level": 2, "ref": "policy:scope", "value": "P1"}])
        self.assertEqual(result, {"outcome": "resolved", "value": "P1"})
