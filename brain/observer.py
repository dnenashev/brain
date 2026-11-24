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


class AuditReadiness:
    def __init__(self, audit, *, recovery_owner):
        self._audit = audit
        self._observer = Observer(audit.read_after, recovery_owner=recovery_owner)
        self._cursor = 0
        self._gap = False

    def check(self):
        if not self._gap:
            observation = self._observer.inspect(self._cursor)
            self._cursor = observation["cursor"]
            self._gap = any(issue["kind"] == "sequence_gap" for issue in observation["issues"])
            # Detect deletion of an event that a previous probe already observed.
            self._gap = self._gap or self._audit.sequence_has_gap()
        return not self._gap
