"""Validate knowledge writes and plan maintenance for active cards."""

from datetime import date, datetime, time, timedelta, timezone


def lint_before_write(entry, *, project_id, readable_refs, max_bytes):
    """Report violations before an entry is written."""
    violations = []
    if entry.get("project_id") != project_id:
        violations.append("scope")

    body = entry.get("body")
    if not isinstance(body, str) or not body.strip():
        violations.append("invalid_body")
    else:
        try:
            body_bytes = len(body.encode("utf-8"))
        except UnicodeEncodeError:
            violations.append("invalid_body")
        else:
            if body_bytes > max_bytes:
                violations.append("quota")

    sources = entry.get("sources")
    if (
        not isinstance(sources, list)
        or not sources
        or any(not isinstance(ref, str) or not ref.strip() for ref in sources)
        or len(set(sources)) != len(sources)
    ):
        violations.append("invalid_sources")
    elif not set(sources).issubset(readable_refs):
        violations.append("unreadable_source")

    return {"ok": not violations, "violations": violations}


def _utc_datetime(value):
    if isinstance(value, str):
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        value = datetime.fromisoformat(value)
    elif isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def maintenance_plan(
    entries, *, now, protected_refs, max_active, max_bytes, review_after_days=90
):
    """Report current usage and propose the oldest eligible cards for archive."""
    active = sorted(
        (
            (_utc_datetime(entry["updated_at"]), entry)
            for entry in entries
            if entry["status"] == "active"
        ),
        key=lambda item: (item[0], item[1]["ref"]),
    )
    active_count = len(active)
    used_bytes = sum(entry["body_bytes"] for _, entry in active)
    review_cutoff = _utc_datetime(now) - timedelta(days=review_after_days)
    review = [
        {"ref": entry["ref"], "owner": entry["owner"]}
        for updated_at, entry in active
        if updated_at <= review_cutoff
    ]

    protected = set(protected_refs)
    archive = []
    remaining_count = active_count
    remaining_bytes = used_bytes
    for _, entry in active:
        if remaining_count <= max_active and remaining_bytes <= max_bytes:
            break
        ref = entry["ref"]
        if ref not in protected:
            archive.append(ref)
            remaining_count -= 1
            remaining_bytes -= entry["body_bytes"]

    return {
        "active_count": active_count,
        "used_bytes": used_bytes,
        "archive": archive,
        "review": review,
        "quota_met": remaining_count <= max_active and remaining_bytes <= max_bytes,
    }


def apply_archive(plan, *, archive):
    """Pass each proposed ref to the storage owner's archive callback."""
    for ref in plan["archive"]:
        archive(ref)
