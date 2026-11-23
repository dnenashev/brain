"""Independent lifecycle checks for Company Brain knowledge revisions."""

from datetime import date
import unittest

from brain.knowledge import KnowledgeRevision, transition


VALUES = {
    "knowledge_id": "policy-17",
    "revision": 1,
    "statement": "Предел включителен.",
    "source": "docs/current/development.md#timeout",
    "scope": "synthetic-project",
    "owner": "rule-owner",
    "status": "draft",
    "effective_on": date(2026, 11, 23),
    "review_on": date(2027, 1, 15),
    "author": "initial-author",
    "changed_by": "initial-author",
}


class KnowledgeRevisions(unittest.TestCase):
    def test_lifecycle_preserves_content_and_author_in_all_revisions(self):
        draft = KnowledgeRevision(**VALUES)
        active = transition(draft, "active", changed_by="owner")
        superseded = transition(active, "superseded", changed_by="operator")
        archived = transition(superseded, "archived", changed_by="archivist")
        series = (draft, active, superseded, archived)
        self.assertEqual([row.status for row in series], ["draft", "active", "superseded", "archived"])
        self.assertEqual([row.revision for row in series], [1, 2, 3, 4])
        self.assertEqual([row.changed_by for row in series], ["initial-author", "owner", "operator", "archivist"])
        for row in series:
            for field in ("knowledge_id", "statement", "source", "scope", "owner", "effective_on", "review_on", "author"):
                with self.subTest(field=field, revision=row.revision):
                    self.assertEqual(getattr(row, field), VALUES[field])

    def test_revisions_reject_direct_mutation(self):
        draft = KnowledgeRevision(**VALUES)
        with self.assertRaises((AttributeError, TypeError)):
            draft.author = "rewritten"
        self.assertEqual(draft.author, VALUES["author"])

    def test_mandatory_fields_and_transition_invariants(self):
        for key, value in (("source", " "), ("scope", ""), ("owner", " "),
                           ("review_on", None), ("status", "unknown"), ("revision", 0)):
            with self.subTest(field=key), self.assertRaises(ValueError):
                KnowledgeRevision(**{**VALUES, key: value})
        draft = KnowledgeRevision(**VALUES)
        for status, actor in (("superseded", "operator"), ("active", " ")):
            with self.subTest(status=status, actor=actor), self.assertRaises(ValueError):
                transition(draft, status, changed_by=actor)
        self.assertEqual(draft.status, "draft")
