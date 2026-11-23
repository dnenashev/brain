from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.audit import AuditLog
from brain.observer import Observer


class ObserverAudit(unittest.TestCase):
    def test_redacted_audit_persists_across_connections(self):
        with TemporaryDirectory() as name:
            db = Path(name) / "audit.db"
            audit = AuditLog(db)
            audit.append(trace_id="req-1", step="policy", outcome="policy_denied",
                         details={"duration_ms": 5, "document": "raw-private-payload", "other": {"secret": "private"}})
            events = AuditLog(db).read_after(0)
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual((event["seq"], event["trace_id"], event["outcome"]), (1, "req-1", "policy_denied"))
            self.assertEqual(event["details"], {"duration_ms": 5})
            self.assertNotIn(b"raw-private-payload", db.read_bytes())

    def test_read_only_observer_detects_gaps_and_assigns_recovery(self):
        seen = []
        def sample(cursor):
            seen.append(cursor)
            return [
                {"seq": 6, "trace_id": "req-1", "step": "read", "outcome": "ok"},
                {"seq": 8, "trace_id": "req-1", "step": "policy", "outcome": "policy_denied"},
            ]
        observer = Observer(sample, recovery_owner="owner-1")
        report = observer.inspect(cursor=5)
        self.assertEqual(seen, [5])
        self.assertEqual(report["cursor"], 8)
        issues = {item["kind"]: item for item in report["issues"]}
        self.assertEqual(set(issues), {"sequence_gap", "policy_denied"})
        self.assertEqual((issues["sequence_gap"]["expected_seq"], issues["sequence_gap"]["actual_seq"]), (7, 8))
        for entry in issues.values():
            self.assertEqual((entry["trace_id"], entry["owner"]), ("req-1", "owner-1"))
