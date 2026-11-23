import unittest

from brain.operations.telemetry import summarize, summarize_check_results


class OperationalSignals(unittest.TestCase):
    def test_failure_uses_last_success_from_the_same_trace(self):
        events = [
            {"seq": 1, "trace_id": "work", "step": "start", "outcome": "ok", "details": {"duration_ms": 3}},
            {"seq": 2, "trace_id": "other", "step": "upload", "outcome": "ok", "details": {"duration_ms": 5}},
            {"seq": 3, "trace_id": "work", "step": "policy", "outcome": "policy_denied", "details": {"private": "fixture"}},
            {"seq": 4, "trace_id": "a", "step": "review", "outcome": "returned", "details": {}},
        ]
        for event in events:
            event["timestamp"] = "2026-01-02T12:00:00+00:00"
        report = summarize(events, now="2026-01-02T12:00:09+00:00", versions={"sdk": "v2"},
                           used_bytes=820, queue_oldest_at="2026-01-02T12:00:00+00:00")
        self.assertEqual(report["steps"]["start"], {"count": 1, "total_ms": 3})
        self.assertEqual((report["returns"], report["policy_denials"], report["queue_lag_seconds"]), (1, 1, 9))
        self.assertEqual(report["failure"], {
            "trace_id": "work", "step": "policy", "reason": "policy_denied", "last_success": "start",
        })
        self.assertEqual((report["versions"], report["used_bytes"]), ({"sdk": "v2"}, 820))
        self.assertNotIn("fixture", repr(report))

    def test_duplicate_and_empty_reports_match_acceptance_examples(self):
        first = {"check": "unit", "run": "R1", "outcome": "pass"}
        second = {"check": "boundary", "run": "R1", "outcome": "pass"}
        report = summarize_check_results([first, second, second])
        self.assertEqual(report, {"passed": 2, "unique_checks": 2, "pass_rate": 1.0, "acceptance": "pass"})
        empty = summarize_check_results([])
        self.assertEqual(empty["acceptance"], "missing_evidence")
        self.assertIsNone(empty["pass_rate"])
        bad = summarize_check_results([first, dict(first, outcome="fail")])
        self.assertEqual((bad["passed"], bad["unique_checks"], bad["acceptance"]), (0, 1, "fail"))
