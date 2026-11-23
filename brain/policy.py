import hashlib
import json


_ROLE_ACTIONS = {
    "reader": ("read",),
    "writer": ("read", "write"),
    "operator": ("read", "write", "publish"),
}


def request_digest(request):
    canonical = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def authorize(actor, request, *, current_revision, approval=None):
    if not isinstance(actor, dict) or not isinstance(request, dict):
        return False
    if actor.get("verified") is not True or not actor.get("actor_id"):
        return False

    project_id = request.get("project_id")
    actor_project = actor.get("project_id")
    if (
        project_id is None
        or type(project_id) is not type(actor_project)
        or project_id != actor_project
    ):
        return False

    revision = request.get("revision")
    if (
        revision is None
        or type(revision) is not type(current_revision)
        or revision != current_revision
    ):
        return False

    role = actor.get("role")
    action = request.get("action")
    if not isinstance(role, str) or action not in _ROLE_ACTIONS.get(role, ()):
        return False
    if action != "publish":
        return True

    if not isinstance(approval, dict) or approval.get("verified") is not True:
        return False
    approved_project = approval.get("project_id")
    approved_revision = approval.get("revision")
    if (
        type(approved_project) is not type(project_id)
        or approved_project != project_id
        or type(approved_revision) is not type(revision)
        or approved_revision != revision
    ):
        return False

    try:
        digest = request_digest(request)
    except (TypeError, ValueError):
        return False
    return approval.get("request_digest") == digest


def apply_write(actor, request, *, current_revision, approval=None, commit):
    if not authorize(actor, request, current_revision=current_revision, approval=approval):
        return False
    if request["action"] not in ("write", "publish"):
        return False
    return bool(commit(request, current_revision))
