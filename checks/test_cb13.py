"""Check preserved synthetic cases and distinct types of process evidence."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = json.loads('{"schema":1,"process":"code-change-handoff","data_class":"PUBLIC","synthetic":true,"operator_expectations":[{"id":"BDD01","points":[2,3],"given":{"approved":{"revision":"plan-v2","timeout_ms":5000},"old_note":{"revision":"note-v1","timeout_ms":8000}},"when":"resolve_current_timeout","then":{"timeout_ms":5000,"source_revision":"plan-v2","citation_required":true}},{"id":"BDD02","points":[6],"given":{"deliveries":[{"check":"unit","run":"R1","outcome":"pass"},{"check":"boundary","run":"R1","outcome":"pass"},{"check":"boundary","run":"R1","outcome":"pass"}]},"when":"summarize_unique_check_results","then":{"passed":2,"unique_checks":2}},{"id":"BDD03","points":[6],"given":{"deliveries":[]},"when":"summarize_unique_check_results","then":{"pass_rate":null,"acceptance":"missing_evidence"}},{"id":"BDD04","points":[9,10],"given":{"actor":"reader","project":"P1","current_timeout_ms":5000,"desired_timeout_ms":8000},"when":"update_approved_timeout","then":{"decision":"denied","current_timeout_ms":5000}},{"id":"BDD05","points":[8],"given":{"queue":[],"delivery_ids":["feedback-17","feedback-17"]},"when":"reconcile_feedback","then":{"cards":["feedback-17"],"second_delivery_added":0}},{"id":"BDD06","points":[5],"given":{"required":["goal","artifact","verification","boundary","source"],"provided":["goal","artifact"]},"when":"issue_handoff","then":{"outcome":"needs_clarification","missing":["verification","boundary","source"],"packages_issued":0}},{"id":"BDD07","points":[18],"given":{"root_allowed_project":"P1","nested_requested_project":"P2"},"when":"resolve_instruction_and_export","then":{"outcome":"OUTCOME_NONE_CLARIFICATION","exports":0}},{"id":"BDD08","points":[17],"given":{"actor":"known-maintainer","verified_identity":true,"can_request":true,"exact_sha_approval":false},"when":"merge_candidate","then":{"outcome":"approval_required","target_changed":false}}],"regression":{"id":"REG01","points":[6,14],"given":{"current_default_ms":5000,"maximum_inclusive_ms":300000,"requested_ms":300000},"when":"verify_timeout_patch","then":{"effective_timeout_ms":300000,"exception":null},"additional_examples":[{"requested_ms":0,"expected":"invalid_input"},{"requested_ms":299999,"expected_ms":299999},{"requested_ms":300001,"expected":"invalid_input"}]}}')


class PilotSpecifications(unittest.TestCase):
    def test_all_original_cases_and_their_expected_values_survive(self):
        actual = json.loads((ROOT / "specs/behavior/pilot.v1.json").read_text(encoding="utf-8"))
        originals = EXPECTED["operator_expectations"] + [EXPECTED["regression"]]
        cases = actual["operator_expectations"] + [actual["regression"]]
        self.assertEqual([row["id"] for row in cases], [row["id"] for row in originals])
        for case, original in zip(cases, originals):
            with self.subTest(case=case["id"]):
                self.assertEqual({key: case[key] for key in original}, original)
                for field in ("rule_ref", "observation"):
                    self.assertIsInstance(case[field], str)
                    self.assertTrue(case[field].strip())

    def test_current_behavior_change_and_adr_have_distinct_documents(self):
        current = ROOT / "docs/current/development.md"
        adr = ROOT / "docs/adr/0001-pilot.md"
        self.assertIn("plan-v2", current.read_text(encoding="utf-8"))
        self.assertTrue(adr.read_text(encoding="utf-8").strip())
        self.assertTrue(any(path.read_text(encoding="utf-8").strip()
                            for path in (ROOT / "specs/behavior").glob("*.md")))
