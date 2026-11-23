"""Independent checks for bidirectional impact and missing coverage."""

import unittest

from brain.impact import ImpactIndex


class ImpactGraph(unittest.TestCase):
    def test_changes_find_tasks_checks_runs_and_results_without_duplicates(self):
        graph = ImpactIndex()
        graph.add_rule("R1")
        for _ in range(2):
            graph.link("R1", task_id="T1", check_id="C1", run_id="RUN1", result_id="RESULT1")
        graph.link("R1", task_id="T2", check_id="C2")
        self.assertEqual(graph.affected("R1"), {
            "tasks": ["T1", "T2"], "checks": ["C1", "C2"], "runs": ["RUN1"], "results": ["RESULT1"],
        })
        for kind, identifier in (("task", "T1"), ("check", "C1"), ("run", "RUN1"), ("result", "RESULT1")):
            with self.subTest(kind=kind):
                self.assertEqual(graph.rules_for(kind, identifier), ["R1"])

    def test_rules_without_checks_remain_visible(self):
        graph = ImpactIndex()
        graph.add_rule("no-links")
        graph.add_rule("task-only")
        graph.add_rule("covered")
        graph.link("task-only", task_id="T1")
        graph.link("covered", task_id="T1", check_id="C1")
        self.assertEqual(graph.uncovered_rules(), ["no-links", "task-only"])
        self.assertEqual(graph.rules_for("task", "T1"), ["covered", "task-only"])
        self.assertEqual(graph.rules_for("result", "unknown"), [])
        self.assertEqual(graph.uncovered_rules(), ["no-links", "task-only"])
