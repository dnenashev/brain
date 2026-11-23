"""Independent checks for immutable handoff snapshots."""

from copy import deepcopy
import json
import unittest

from brain.handoff import compare_handoff, issue_handoff


REQUEST = {
    "goal": "Change the synthetic timeout", "artifact": "limit.py", "verification": "protected boundary cases",
    "boundary": "project P1", "assignee": "author", "constraints": ["preserve earlier revisions"],
    "criteria": ["maximum is inclusive"], "exceptions": [],
    "source": [{"rule_id": "max", "revision": "plan-v2", "source": "rules.md#maximum"}],
}


class HandoffSnapshots(unittest.TestCase):
    def test_two_issued_packets_preserve_different_rule_revisions(self):
        request = deepcopy(REQUEST)
        one = issue_handoff(request)
        request["source"][0]["revision"] = "plan-v3"
        two = issue_handoff(request)
        self.assertEqual((one["outcome"], one["packages_issued"]), ("issued", 1))
        self.assertEqual((two["outcome"], two["packages_issued"]), ("issued", 1))
        snapshots = [json.loads(result["packet"]) for result in (one, two)]
        self.assertEqual([x["schema_version"] for x in snapshots], [1, 1])
        self.assertEqual([x["source"][0]["revision"] for x in snapshots], ["plan-v2", "plan-v3"])
        self.assertEqual(one["packet"], issue_handoff(deepcopy(REQUEST))["packet"])

    def test_no_package_is_issued_with_missing_or_invalid_fields(self):
        incomplete = {**REQUEST, "verification": " ", "source": []}
        del incomplete["boundary"]
        issue = issue_handoff(incomplete)
        self.assertEqual((issue["outcome"], issue["packages_issued"]), ("needs_clarification", 0))
        self.assertCountEqual(issue["missing"], ["boundary", "verification", "source"])

    def test_retelling_reports_removed_and_changed_constraints(self):
        retelling = deepcopy(REQUEST)
        del retelling["verification"]
        retelling["source"][0]["revision"] = "plan-v1"
        self.assertCountEqual(compare_handoff(issue_handoff(REQUEST)["packet"], retelling),
                              ["verification", "source"])
