"""Issue immutable handoff snapshots and compare them with retellings."""

import json


def _is_text(value):
    return isinstance(value, str) and bool(value.strip())


def _is_text_list(value):
    return isinstance(value, list) and all(_is_text(item) for item in value)


def _is_nonempty_text_list(value):
    return _is_text_list(value) and bool(value)


def _is_source_entry(value):
    if not isinstance(value, dict):
        return False
    revision = value.get("revision")
    return (
        _is_text(value.get("rule_id"))
        and (type(revision) is int or _is_text(revision))
        and _is_text(value.get("source"))
    )


def _is_source(value):
    return (
        isinstance(value, list)
        and bool(value)
        and all(_is_source_entry(entry) for entry in value)
    )


_FIELD_VALIDATORS = {
    "goal": _is_text,
    "artifact": _is_text,
    "verification": _is_text,
    "boundary": _is_text,
    "assignee": _is_text,
    "constraints": _is_nonempty_text_list,
    "criteria": _is_nonempty_text_list,
    "exceptions": _is_text_list,
    "source": _is_source,
}


def _canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def issue_handoff(request):
    """Validate the required fields and issue a versioned JSON snapshot."""
    fields = request if isinstance(request, dict) else {}
    missing = [
        name for name, validate in _FIELD_VALIDATORS.items()
        if not validate(fields.get(name))
    ]
    if missing:
        return {
            "outcome": "needs_clarification",
            "missing": missing,
            "packages_issued": 0,
        }

    snapshot = {name: fields[name] for name in _FIELD_VALIDATORS}
    snapshot["schema_version"] = 1
    return {
        "outcome": "issued",
        "packages_issued": 1,
        "packet": _canonical_json(snapshot),
    }


def compare_handoff(packet, retelling):
    """Return the agreed fields that are missing or changed in the retelling."""
    snapshot = json.loads(packet)
    fields = retelling if isinstance(retelling, dict) else {}
    return [
        name for name in _FIELD_VALIDATORS
        if name not in fields
        or _canonical_json(snapshot[name]) != _canonical_json(fields[name])
    ]
