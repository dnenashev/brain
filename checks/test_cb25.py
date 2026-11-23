"""Independent grounding and source-read evidence checks."""

from dataclasses import replace
from datetime import date
import unittest

from brain.grounding import resolve_rule
from brain.knowledge import KnowledgeRevision


TODAY = date(2026, 11, 23)
RULE = KnowledgeRevision(
    knowledge_id="timeout", revision=4, statement="Таймаут 5 с", source="rules.md#limit",
    scope="P1", owner="owner", status="active", effective_on=TODAY,
    review_on=date(2027, 1, 15), author="author", changed_by="operator",
)


class Grounding(unittest.TestCase):
    def resolve(self, cards, reader=lambda source, revision: "Правило: 5 с"):
        self.read_events = []
        return resolve_rule(cards, knowledge_id="timeout", scope="P1", today=TODAY,
                            read_source=reader, record_read=self.read_events.append)

    def test_current_rule_provides_exact_grounding_and_records_read(self):
        calls = []

        def reader(source, revision):
            calls.append((source, revision))
            return "Правило: 5 с"

        old = replace(RULE, revision=2, status="superseded", statement="Таймаут 8 с")
        draft = replace(RULE, revision=5, status="draft", statement="Таймаут 10 с")
        older_active = replace(RULE, revision=3, statement="Таймаут 6 с")
        result = self.resolve([old, RULE, draft, older_active], reader)
        self.assertEqual(result, {"outcome": "result", "statement": RULE.statement, "source": RULE.source,
                                  "revision": 4, "fragment": "Правило: 5 с"})
        self.assertEqual(calls, [(RULE.source, 4)])
        self.assertEqual(self.read_events, [{"source": RULE.source, "revision": 4, "available": True}])

    def test_unknown_or_conflicting_rules_open_clarification(self):
        self.assertEqual(self.resolve([]), {"outcome": "needs_clarification", "reason": "no_applicable_rule"})
        conflict = replace(RULE, source="competing.md#limit", statement="Таймаут 8 с")
        self.assertEqual(self.resolve([RULE, conflict]), {"outcome": "needs_clarification", "reason": "conflicting_rules"})
        self.assertEqual(self.read_events, [])

    def test_broken_grounding_is_recorded_and_opens_clarification(self):
        self.assertEqual(self.resolve([RULE], lambda source, revision: None),
                         {"outcome": "needs_clarification", "reason": "source_unavailable"})
        self.assertEqual(self.read_events, [{"source": RULE.source, "revision": 4, "available": False}])
