"""Policy and workflow operations for the deployed knowledge service."""

import re
from datetime import datetime, timezone

from . import policy
from .handoff import issue_handoff
from .instructions import resolve_directives
from .observer import Observer
from .operations.hygiene import lint_before_write
from .operations.telemetry import summarize, summarize_check_results
from .storage.changesets import DraftConflict
from .write_access import check_write_access


ACTIONS = {
    "/identity": "read",
    "/check": "read",
    "/drafts": "write",
    "/reviews": "write",
    "/compensate": "write",
    "/publish": "write",
    "/ops/audit-summary": "read",
}
_OPERATOR_ONLY = {"/compensate", "/publish", "/ops/audit-summary"}
DEFAULT_LIMITS = {"document_bytes": 65536, "active_documents": 1000, "versions": 10000, "drafts": 1000}
_CONTEXT_LEVELS = ("root", "organization", "project", "workflow")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_REF = re.compile(r"doc:(.{1,128})@([1-9][0-9]{0,17})")
_SHA = re.compile(r"[0-9a-f]{40}")


class WorkflowError(ValueError):
    def __init__(self, status, error, **details):
        self.status = status
        self.response = {"error": error, **details}


def parse_reference(ref):
    match = _REF.fullmatch(ref) if isinstance(ref, str) else None
    return (match[1], int(match[2])) if match else None


def validate_change(change):
    if not isinstance(change, dict):
        raise ValueError("Invalid change")
    if (
        not isinstance(change.get("doc_id"), str) or not change["doc_id"]
        or not isinstance(change.get("text"), str)
        or type(change.get("expected_version")) is not int or change["expected_version"] < 0
        or not isinstance(change.get("readers"), list)
        or any(not isinstance(reader, str) for reader in change["readers"])
    ):
        raise ValueError("Invalid document change")


class LiveWorkflow:
    def __init__(self, store, changesets, reviews, audit, subjects, *, limits=None, directives=None):
        self.store = store
        self.changesets = changesets
        self.reviews = reviews
        self.audit = audit
        self.subjects = subjects
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self.directives = directives or {}

    def check_directives(self, actor, body, trace_id):
        project_id = actor["project_id"]
        configured = self.directives.get(project_id, {})
        directives = [
            {"level": level, "ref": "policy:" + name, "value": project_id if level == 1 else configured.get(name, project_id)}
            for level, name in enumerate(_CONTEXT_LEVELS, 1)
        ]
        scope = body.get("instruction_scope", project_id)
        if not isinstance(scope, str) or not scope:
            raise ValueError("Invalid requested instruction scope")
        directives.append({"level": 5, "ref": "request:scope", "value": scope})
        outcome = resolve_directives(
            directives, audit=lambda _: self.audit.append(
                trace_id=trace_id, project_id=project_id, step="directives", outcome="needs_clarification"
            ),
        )
        if outcome["outcome"] == "OUTCOME_NONE_CLARIFICATION":
            raise WorkflowError(422, "directive_conflict", **outcome)

    def _access(self, actor, *, approved=False, restore=False, stage=False):
        subject = actor["actor_id"]
        members = self.subjects[actor["project_id"]]

        def authorize(change, previous):
            if not set(change["readers"]).issubset(members):
                raise PermissionError("Readers must belong to the project")
            if restore:
                if previous is None or subject not in previous["readers"]:
                    raise PermissionError("Document access is required")
            elif stage:
                if subject not in change["readers"] or (previous and subject not in previous["readers"]):
                    raise PermissionError("Document access is required")
            else:
                check_write_access(actor, change, previous, approved=approved)

        return authorize

    def _validate_batch(self, actor):
        project_id, subject = actor["project_id"], actor["actor_id"]

        def validate(connection, before, changes):
            usage = self.store.usage_in_transaction(connection, project_id)
            added = sum(not old or not old["readers"] for old in before.values())
            if (usage["active"] + added > self.limits["active_documents"]
                    or usage["versions"] + len(changes) > self.limits["versions"]):
                raise WorkflowError(422, "project_quota")
            for change in changes:
                # Direct authenticated writes have explicit server-owned provenance.
                source = "submission:" + subject
                refs = change.get("sources", [source])
                readable = {source} if "sources" not in change else set()
                if isinstance(refs, list):
                    for ref in refs:
                        parsed = parse_reference(ref)
                        if parsed and self.store.read_in_transaction(
                            connection, project_id, parsed[0], subject=subject, version=parsed[1]
                        ):
                            readable.add(ref)
                lint = lint_before_write(
                    {"project_id": project_id, "body": change["text"], "sources": refs},
                    project_id=project_id, readable_refs=readable, max_bytes=self.limits["document_bytes"],
                )
                if not lint["ok"]:
                    raise WorkflowError(413 if "quota" in lint["violations"] else 422,
                                        "write_lint", violations=lint["violations"])

        return validate

    def write(self, actor, body, *, approved):
        changes = body["changes"]
        if not changes:
            raise ValueError("A changeset requires at least one document")
        return self.changesets.apply(
            body["change_id"], actor["project_id"], actor["actor_id"], changes,
            authorize_change=self._access(actor, approved=approved), validate_batch=self._validate_batch(actor),
        )

    def dispatch(self, path, actor, token, body, trace_id, approvals):
        if path in _OPERATOR_ONLY and actor["role"] != "operator":
            raise PermissionError("Operator access is required")
        if path == "/identity":
            return 200, {key: actor[key] for key in ("actor_id", "project_id", "role")}
        if path == "/ops/audit-summary":
            return 200, self._audit_summary(actor, body.get("trace_id", trace_id))
        if path == "/check":
            deliveries = body.get("deliveries")
            if not isinstance(deliveries, list) or any(
                not isinstance(item, dict)
                or any(not isinstance(item.get(key), str) or not item[key] for key in ("check", "run"))
                or item.get("outcome") not in ("pass", "fail")
                for item in deliveries
            ):
                raise ValueError("Invalid check deliveries")
            return 200, summarize_check_results(deliveries)
        if path == "/drafts":
            return 201, self._draft(actor, body)
        if path == "/reviews":
            return self._enqueue(actor, body, trace_id)
        if path == "/compensate":
            change_id = body.get("change_id")
            if not isinstance(change_id, str):
                raise ValueError("Invalid change_id")
            return 200, self.changesets.compensate(
                change_id, compensation_id=self._id(body, "compensation_id"),
                actor=actor["actor_id"], project_id=actor["project_id"], authorize_change=self._access(actor, restore=True),
            )
        return 200, self._publish(actor, token, body, approvals)

    @staticmethod
    def _id(body, field):
        value = body.get(field)
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError("Invalid " + field)
        return value

    @staticmethod
    def _revision(body):
        revision = body.get("revision")
        if type(revision) is not int or not 0 < revision < 2**63:
            raise ValueError("Invalid draft revision")
        return revision

    def _draft(self, actor, body):
        change = body.get("change")
        validate_change(change)
        project_id = actor["project_id"]
        draft_id = self._id(body, "draft_id")
        # A draft can propose a new ACL. Publication still requires explicit operator approval.
        revision = self.changesets.save_draft(
            project_id, draft_id, actor["actor_id"], change,
            authorize_change=self._access(actor, stage=True), validate_batch=self._validate_batch(actor),
            max_drafts=self.limits["drafts"],
        )
        if revision is None:
            raise WorkflowError(422, "draft_quota")
        return {"draft_id": draft_id, "revision": revision}

    def _enqueue(self, actor, body, trace_id):
        project_id, subject = actor["project_id"], actor["actor_id"]
        task_id = self._id(body, "task_id")
        sha = body.get("candidate_sha")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise ValueError("Invalid candidate SHA")
        draft_id, revision = self._id(body, "draft_id"), self._revision(body)
        draft = self.changesets.get_review_draft(project_id, draft_id, revision, actor=subject)
        if draft is None:
            raise PermissionError("Candidate must reference the author's own draft")
        handoff = issue_handoff(body.get("handoff"))
        if handoff["outcome"] != "issued":
            raise WorkflowError(422, "handoff_requires_clarification", missing=handoff["missing"])
        request = body["handoff"]
        refs = []
        for entry in request["source"]:
            ref = entry["source"]
            parsed = parse_reference(ref)
            if parsed != (entry["rule_id"], entry["revision"]) or not self.store.read(
                project_id, parsed[0], subject=subject, version=parsed[1]
            ):
                raise WorkflowError(422, "unreadable_source")
            refs.append(ref)
        card = {
            "project_id": project_id, "task_id": task_id, "candidate_sha": sha, "author": subject,
            "draft_id": draft_id, "revision": revision, "trace_id": trace_id, "status": "pending",
            "handoff": handoff["packet"], "brief": request["goal"],
            "changes": {
                "Артефакт": request["artifact"], "Документ": draft["doc_id"],
                "Базовая версия": draft["expected_version"], "Черновик": draft["text"],
                "Читатели": draft["readers"], "Ссылки черновика": draft.get("sources", []),
            },
            "risks": request["exceptions"], "sources": refs,
            "tests": [request["verification"], *request["criteria"]],
            "rollback": request["boundary"],
        }
        created = self.reviews.enqueue(card)
        return (201 if created else 200), {"task_id": task_id, "candidate_sha": sha}

    def _publish(self, actor, token, body, approvals):
        with approvals.commit(token, "publish", body, required=True):
            return self._commit_publication(actor, body)

    def _commit_publication(self, actor, body):
        project_id = actor["project_id"]
        draft_id, revision = self._id(body, "draft_id"), self._revision(body)
        task_id, change_id = self._id(body, "task_id"), self._id(body, "change_id")
        sha = body.get("candidate_sha")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise ValueError("Invalid candidate SHA")
        if not self.reviews.is_approved(project_id, task_id, sha, draft_id, revision):
            raise PermissionError("Candidate must be approved for this draft revision")
        request = {**{key: value for key, value in body.items() if key != "approval"}, "action": "publish"}
        approval = {"verified": True, "project_id": project_id, "revision": revision,
                    "request_digest": policy.request_digest(request)}
        return self.changesets.publish_draft(
            project_id, draft_id, revision, actor=actor["actor_id"], change_id=change_id,
            authorize_publish=lambda current: policy.authorize(actor, request, current_revision=current, approval=approval),
            authorize_change=self._access(actor, approved=True), validate_batch=self._validate_batch(actor),
        )

    def _audit_summary(self, actor, trace_id):
        from .server import valid_trace_id

        if not valid_trace_id(trace_id):
            raise ValueError("Invalid trace ID")
        project_id = actor["project_id"]
        events = self.audit.read_after(include_project=True)
        selected = [
            {key: value for key, value in event.items() if key != "project_id"}
            for event in events if (event["project_id"], event["trace_id"]) == (project_id, trace_id)
        ]
        selected_sequences = {event["seq"] for event in selected}
        observation = Observer(lambda _: events, recovery_owner=actor["actor_id"]).inspect()
        issues = [
            issue for issue in observation["issues"]
            if issue.get("seq", issue.get("actual_seq")) in selected_sequences
        ]
        usage = self.store.project_usage(project_id)
        return {
            "trace_id": trace_id, "events": selected,
            "observer": {"issues": issues, "sequence_gap": sum(issue["kind"] == "sequence_gap" for issue in issues)},
            "telemetry": summarize(selected, now=datetime.now(timezone.utc).isoformat(),
                                   versions=usage["versions"], used_bytes=usage["used_bytes"]),
        }
