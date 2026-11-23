"""Inspect redacted audit events through a read-only interface."""


class Observer:
    def __init__(self, read_after, *, recovery_owner):
        self._read_after = read_after
        self._recovery_owner = recovery_owner

    def inspect(self, cursor=0):
        issues = []
        last_seq = cursor
        for event in self._read_after(cursor):
            seq = event["seq"]
            expected_seq = last_seq + 1
            context = {"trace_id": event["trace_id"], "owner": self._recovery_owner}
            if seq > expected_seq:
                issues.append({
                    "kind": "sequence_gap",
                    "expected_seq": expected_seq,
                    "actual_seq": seq,
                    **context,
                })
            if event["outcome"] == "policy_denied":
                issues.append({"kind": "policy_denied", "seq": seq, **context})
            last_seq = seq
        return {"cursor": last_seq, "issues": issues}
