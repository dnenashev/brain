"""Independent public contract examples for the first versioned envelopes."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from specs.contracts.validate import validate_packet


CONTRACTS = Path(__file__).resolve().parents[1] / "specs/contracts"
RULE = {"rule_id": "timeout", "revision": "plan-v2", "source": "docs/timeouts.md#limits"}
BASE = {
    "schema_version": 1,
    "task_id": "T1",
    "rule_snapshot": [RULE],
    "constraints": ["upper-bound-inclusive"],
}
OUTCOMES = {
    "result": {"candidate_sha": "a" * 40},
    "rejected": {"reason": "boundary verification failed"},
    "needs_clarification": {"missing": ["source"]},
}


class PacketContracts(unittest.TestCase):
    def packet(self, outcome):
        return deepcopy({**BASE, "outcome": outcome, **OUTCOMES[outcome]})

    def test_valid_envelopes_round_trip_through_json(self):
        for outcome in OUTCOMES:
            with self.subTest(outcome=outcome):
                original = self.packet(outcome)
                parsed = json.loads(json.dumps(original))
                validate_packet(parsed)
                self.assertEqual(parsed, original)

    def test_clarification_can_report_that_no_rules_are_known_yet(self):
        changed = self.packet("needs_clarification")
        changed["rule_snapshot"] = []
        changed["constraints"] = []
        validate_packet(changed)

    def test_missing_common_provenance_or_constraints_is_rejected(self):
        for outcome in OUTCOMES:
            for name in ("schema_version", "rule_snapshot", "constraints"):
                with self.subTest(outcome=outcome, missing=name):
                    changed = self.packet(outcome)
                    del changed[name]
                    with self.assertRaises(ValueError):
                        validate_packet(changed)
        changed = self.packet("result")
        del changed["rule_snapshot"][0]["revision"]
        with self.assertRaises(ValueError):
            validate_packet(changed)

    def test_outcome_evidence_is_required(self):
        for outcome, fields in OUTCOMES.items():
            for name in fields:
                with self.subTest(outcome=outcome, missing=name):
                    changed = self.packet(outcome)
                    del changed[name]
                    with self.assertRaises(ValueError):
                        validate_packet(changed)
        for field, value in (("schema_version", 2), ("candidate_sha", "main"), ("rule_snapshot", []), ("constraints", [])):
            with self.subTest(field=field):
                changed = self.packet("result")
                changed[field] = value
                with self.assertRaises(ValueError):
                    validate_packet(changed)

    def test_entity_lifecycles_and_identifiers_are_defined(self):
        entities = json.loads((CONTRACTS / "entities.v1.json").read_text(encoding="utf-8"))
        for name in ("knowledge", "task", "check", "history", "execution"):
            with self.subTest(entity=name):
                self.assertTrue(entities[name]["identifier"])
                self.assertTrue(entities[name]["states"])
