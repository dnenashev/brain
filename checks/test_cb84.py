import unittest

from brain.operations.hygiene import lint_before_write, maintenance_plan


class MaintenancePolicies(unittest.TestCase):
    def test_quotas_preserve_pinned_references_and_order_by_age(self):
        events = [
            {"ref": "pinned", "owner": "one", "body_bytes": 67, "status": "active", "updated_at": "2025-12-01T00:00:00+00:00"},
            {"ref": "old", "owner": "two", "body_bytes": 33, "status": "active", "updated_at": "2026-01-01T00:00:00+00:00"},
            {"ref": "recent", "owner": "three", "body_bytes": 18, "status": "active", "updated_at": "2026-07-01T00:00:00+00:00"},
        ]
        result = maintenance_plan(events, now="2026-07-10T00:00:00+00:00", protected_refs={"pinned"},
                                  max_active=2, max_bytes=100, review_after_days=90)
        self.assertEqual((result["active_count"], result["used_bytes"]), (3, 118))
        self.assertEqual(result["archive"], ["old"])
        self.assertTrue(result["quota_met"])
        self.assertEqual({(e["ref"], e["owner"]) for e in result["review"]}, {("pinned", "one"), ("old", "two")})

    def test_linter_rejects_out_of_scope_source_and_utf8_quota(self):
        entry = {"project_id": "p1", "body": "проверка", "sources": ["readable"]}
        ok = lint_before_write(entry, project_id="p1", readable_refs={"readable"}, max_bytes=40)
        self.assertTrue(ok["ok"])
        failed = lint_before_write(dict(entry, sources=["outside"]), project_id="p1",
                                   readable_refs={"readable"}, max_bytes=8)
        self.assertFalse(failed["ok"])
        self.assertEqual(set(failed["violations"]), {"quota", "unreadable_source"})
