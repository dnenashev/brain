"""Adapt public handoffs and review status to Swarm records."""

import json
from collections.abc import Mapping

from brain.handoff import issue_handoff


def _is_text(value):
    return isinstance(value, str) and bool(value.strip())


def _unique_fields(pairs):
    fields = {}
    for name, value in pairs:
        if name in fields:
            raise ValueError("handoff packet contains a duplicate JSON field")
        fields[name] = value
    return fields


def public_task_spec(packet, *, task_id, allowed_paths, verify, base_ref, data_class="PUBLIC"):
    """Build a public TaskSpec from a version 1 handoff snapshot."""
    if data_class != "PUBLIC":
        raise ValueError("data_class must be PUBLIC")
    for name, value in (("task_id", task_id), ("base_ref", base_ref)):
        if not _is_text(value):
            raise ValueError(f"{name} must be nonempty text")
    for name, values in (("allowed_paths", allowed_paths), ("verify", verify)):
        if not isinstance(values, (list, tuple)) or not values or not all(
            _is_text(value) for value in values
        ):
            raise ValueError(f"{name} must be a nonempty sequence of nonempty strings")

    if not isinstance(packet, str):
        raise ValueError("handoff packet must be a JSON string")
    try:
        snapshot = json.loads(packet, object_pairs_hook=_unique_fields)
        version = snapshot.get("schema_version") if isinstance(snapshot, dict) else None
        if type(version) is not int or version != 1:
            raise ValueError("handoff packet must have schema_version 1")
        issued = issue_handoff(snapshot)
    except RecursionError as error:
        raise ValueError("handoff packet exceeds the JSON nesting limit") from error

    if issued["outcome"] != "issued":
        raise ValueError("handoff packet is missing valid required fields")
    brief = issued["packet"]
    if snapshot.keys() != json.loads(brief).keys():
        raise ValueError("handoff packet contains unexpected fields")

    return {
        "task_id": task_id,
        "brief": brief,
        "allowed_paths": list(allowed_paths),
        "verify": list(verify),
        "base_ref": base_ref,
        "data_class": data_class,
    }


def review_state(status, *, packet=None, review_ref=None):
    """Return the Swarm status with a verdict tied to its task and candidate."""
    if not isinstance(status, Mapping):
        raise ValueError("status must be a mapping")
    task_id = status.get("task_id")
    phase = status.get("status")
    candidate_sha = status.get("candidate_sha")
    if not _is_text(task_id) or not _is_text(phase):
        raise ValueError("status must provide a task_id and status")

    verdict = None
    if packet is None:
        if review_ref is not None:
            raise ValueError("review_ref requires a ReviewPacket")
    else:
        if not isinstance(packet, Mapping) or not _is_text(review_ref):
            raise ValueError("a ReviewPacket requires a nonempty review_ref")
        if (
            not _is_text(candidate_sha)
            or packet.get("task_id") != task_id
            or packet.get("candidate_sha") != candidate_sha
        ):
            raise ValueError("ReviewPacket must match the status task and candidate SHA")
        verdict = packet.get("verdict")
        if not _is_text(verdict):
            raise ValueError("ReviewPacket must provide a verdict")

    return {
        "task_id": task_id,
        "status": phase,
        "candidate_sha": candidate_sha,
        "verdict": verdict,
        "review_ref": review_ref,
    }
