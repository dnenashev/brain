"""Summaries of redacted audit events and check deliveries."""

from datetime import datetime


def summarize(events, *, now, versions, used_bytes, queue_oldest_at=None):
    """Aggregate step durations and locate the latest failure in audit order."""
    steps = {}
    returns = 0
    policy_denials = 0
    failure = None
    last_success_by_trace = {}

    for event in sorted(events, key=lambda event: event["seq"]):
        trace_id = event["trace_id"]
        step = event["step"]
        outcome = event["outcome"]
        duration_ms = event["details"].get("duration_ms")
        if type(duration_ms) in (int, float):
            totals = steps.setdefault(step, {"count": 0, "total_ms": 0})
            totals["count"] += 1
            totals["total_ms"] += duration_ms

        if outcome == "returned":
            returns += 1
        if outcome == "policy_denied":
            policy_denials += 1

        if outcome == "ok":
            last_success_by_trace[trace_id] = step
        elif outcome in ("failed", "policy_denied"):
            failure = {
                "trace_id": trace_id,
                "step": step,
                "reason": outcome,
                "last_success": last_success_by_trace.get(trace_id),
            }

    queue_lag_seconds = 0
    if queue_oldest_at is not None:
        current_time = datetime.fromisoformat(now.replace("Z", "+00:00"))
        oldest_time = datetime.fromisoformat(queue_oldest_at.replace("Z", "+00:00"))
        queue_lag_seconds = max(0, (current_time - oldest_time).total_seconds())

    return {
        "steps": steps,
        "returns": returns,
        "policy_denials": policy_denials,
        "queue_lag_seconds": queue_lag_seconds,
        "used_bytes": used_bytes,
        "versions": versions,
        "failure": failure,
    }


def summarize_check_results(deliveries):
    """Count each check and run pair once, requiring every delivery to pass."""
    results = {}
    for delivery in deliveries:
        key = (delivery["check"], delivery["run"])
        results[key] = results.get(key, True) and delivery["outcome"] == "pass"

    passed = sum(results.values())
    unique_checks = len(results)
    if unique_checks:
        pass_rate = passed / unique_checks
        acceptance = "pass" if passed == unique_checks else "fail"
    else:
        pass_rate = None
        acceptance = "missing_evidence"

    return {
        "passed": passed,
        "unique_checks": unique_checks,
        "pass_rate": pass_rate,
        "acceptance": acceptance,
    }
