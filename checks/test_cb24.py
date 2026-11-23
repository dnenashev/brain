"""Independent checks for the knowledge publication workflow."""

from datetime import date
import unittest

from brain.knowledge_workflow import ingest, lint, publish, query


TODAY = date(2026, 11, 23)
FIELDS = {
    "knowledge_id": "case-1", "statement": "Синтетическое правило", "source": "docs/example.md#rule",
    "scope": "P1", "owner": "rule-owner", "effective_on": TODAY, "review_on": date(2027, 1, 15),
    "author": "author",
}


class KnowledgeWorkflow(unittest.TestCase):
    def test_draft_is_invisible_until_checked_and_published(self):
        draft = ingest(**FIELDS)
        self.assertEqual((draft.status, draft.revision, draft.changed_by), ("draft", 1, "author"))
        self.assertEqual(query([draft], scope="P1", today=TODAY), [])
        calls = []

        def source_exists(reference):
            calls.append(reference)
            return True

        active = publish(draft, today=TODAY, source_exists=source_exists, changed_by="operator")
        self.assertEqual(calls, [FIELDS["source"]])
        self.assertEqual((active.status, active.revision, active.author), ("active", 2, "author"))
        self.assertEqual(query([draft, active], scope="P1", today=TODAY), [active])
        self.assertEqual(query([active], scope="P2", today=TODAY), [])

    def test_broken_source_and_overdue_review_are_reported_to_owner(self):
        draft = ingest(**{**FIELDS, "review_on": date(2026, 11, 20)})
        result = lint(draft, today=TODAY, source_exists=lambda reference: False)
        self.assertEqual(result["owner"], "rule-owner")
        self.assertCountEqual(result["issues"], ["broken_source", "review_overdue"])
        with self.assertRaises(ValueError):
            publish(draft, today=TODAY, source_exists=lambda reference: False, changed_by="operator")
        self.assertEqual((draft.status, draft.revision), ("draft", 1))
