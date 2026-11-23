"""Independent public handoff and review identity checks."""

import json
import unittest

from brain.adapters.swarm import public_task_spec, review_state
from brain.handoff import issue_handoff
from checks.test_cb31 import REQUEST


SHA = "a" * 40
OPTIONS = {"task_id": "T1", "allowed_paths": ["brain/handoff.py"], "verify": ["python3", "checks/run.py", "CB31"], "base_ref": "main"}


class SwarmAdapter(unittest.TestCase):
    def test_public_handoff_is_an_operator_configured_task_spec(self):
        packet = issue_handoff(REQUEST)["packet"]
        task = public_task_spec(packet, **OPTIONS)
        self.assertEqual({k: task[k] for k in OPTIONS}, OPTIONS)
        self.assertEqual(task["data_class"], "PUBLIC")
        self.assertEqual(json.loads(task["brief"]), json.loads(packet))
        with self.assertRaises(ValueError):
            public_task_spec(packet, **OPTIONS, data_class="INTERNAL")
        with self.assertRaises(ValueError):
            public_task_spec('{"schema_version":1}', **OPTIONS)

    def test_only_exact_review_packet_supplies_a_verdict(self):
        status = {"task_id": "T1", "status": "in_review", "candidate_sha": SHA}
        self.assertEqual(review_state(status), {**status, "verdict": None, "review_ref": None})
        packet = {"task_id": "T1", "candidate_sha": SHA, "verdict": "checks passed"}
        self.assertEqual(review_state(status, packet=packet, review_ref="evidence/review.json"),
                         {**status, "verdict": "checks passed", "review_ref": "evidence/review.json"})
        for modified in ({**packet, "task_id": "T2"}, {**packet, "candidate_sha": "b" * 40}):
            with self.assertRaises(ValueError):
                review_state(status, packet=modified, review_ref="evidence/review.json")
