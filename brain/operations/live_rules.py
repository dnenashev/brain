"""Active rule resolution and impact links in the local knowledge store."""

import json
from datetime import date, datetime, timedelta, timezone

from ..grounding import resolve_rule
from ..impact import ImpactIndex
from ..knowledge import KnowledgeRevision
from ..knowledge_workflow import lint


class RuleValidationError(ValueError):
    def __init__(self, issues):
        self.issues = issues


def parse_reference(ref):
    if not isinstance(ref, str) or not ref.startswith("doc:"):
        return None
    doc_id, separator, version = ref[4:].rpartition("@")
    if (not separator or not 1 <= len(doc_id) <= 128 or not version.isascii()
            or not version.isdecimal() or not 1 <= len(version) <= 18 or version[0] == "0"):
        return None
    return doc_id, int(version)


def _today():
    return datetime.now(timezone.utc).date()


def _revision(row):
    return KnowledgeRevision(**{
        **dict(row), "effective_on": date.fromisoformat(row["effective_on"]),
        "review_on": date.fromisoformat(row["review_on"]),
    })


def record_publication(store, actor, connection, change, version, author):
    project_id, subject = actor["project_id"], actor["actor_id"]
    doc_id = change["doc_id"]
    metadata = change.get("rule", {})
    if not isinstance(metadata, dict):
        raise RuleValidationError(["invalid_metadata"])
    today = _today()
    try:
        card = KnowledgeRevision(
            knowledge_id=doc_id, revision=version, statement=change["text"],
            source=metadata.get("source", f"doc:{doc_id}@{version}"), scope=project_id,
            owner=author, status="active", effective_on=date.fromisoformat(metadata.get("effective_on", today.isoformat())),
            review_on=date.fromisoformat(metadata.get("review_on", (today + timedelta(days=90)).isoformat())),
            author=author, changed_by=subject,
        )
    except (TypeError, ValueError):
        raise RuleValidationError(["invalid_metadata"]) from None

    def source_exists(ref):
        return _read_ref(store, connection, actor, ref) is not None

    issues = lint(card, today=today, source_exists=source_exists)["issues"]
    if issues:
        raise RuleValidationError(issues)
    connection.execute(
        "INSERT INTO knowledge_cards (scope, knowledge_id, revision, statement, source, owner, status, "
        "effective_on, review_on, author, changed_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card.scope, card.knowledge_id, card.revision, card.statement, card.source, card.owner, card.status,
         card.effective_on.isoformat(), card.review_on.isoformat(), card.author, card.changed_by),
    )


def _read_ref(store, connection, actor, ref):
    parsed = parse_reference(ref)
    if parsed is None:
        return None
    return store.read_in_transaction(
        connection, actor["project_id"], parsed[0], subject=actor["actor_id"], version=parsed[1]
    )


def resolve(store, audit, actor, rule_id, trace_id):
    project_id = actor["project_id"]
    today = _today()
    with store.connection() as connection:
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT * FROM knowledge_cards WHERE scope = ? AND knowledge_id = ?", (project_id, rule_id)
        )
        cards = [
            _revision(row) for row in rows if store.read_in_transaction(
                connection, project_id, rule_id, subject=actor["actor_id"], version=row["revision"]
            ) is not None
        ]

        def source(ref, version):
            document = _read_ref(store, connection, actor, ref)
            return document["text"] if document is not None else None

        def record(event):
            audit.append(trace_id=trace_id, project_id=project_id, step="source_read",
                         outcome="ok" if event["available"] else "failed")

        response = resolve_rule(
            cards, knowledge_id=rule_id, scope=project_id, today=today, read_source=source, record_read=record,
        )
    if response["outcome"] == "result":
        selected = next(card for card in cards if card.revision == response["revision"])
        if selected.review_on < today:
            return {"outcome": "needs_clarification", "reason": "review_overdue"}
    return response


def _task(connection, project_id, task_id):
    params = (project_id, task_id)
    for table in ("review_queue", "review_decisions"):
        order = " ORDER BY event_id DESC" if table == "review_decisions" else ""
        row = connection.execute(
            f"SELECT draft_id, draft_revision FROM {table} WHERE project_id = ? AND task_id = ?{order} LIMIT 1", params
        ).fetchone()
        if row is not None:
            return row
    return None


def _task_is_readable(store, connection, actor, task_id):
    project_id, subject = actor["project_id"], actor["actor_id"]
    task = _task(connection, project_id, task_id)
    if task is None or task["draft_id"] is None:
        return False
    draft = connection.execute(
        "SELECT payload FROM drafts WHERE project_id = ? AND draft_id = ? AND revision = ?",
        (project_id, task["draft_id"], task["draft_revision"]),
    ).fetchone()
    if draft is None:
        return False
    change = json.loads(draft["payload"])
    if subject not in change["readers"]:
        return False
    seen = connection.execute(
        "SELECT 1 FROM document_revisions WHERE project_id = ? AND doc_id = ? LIMIT 1",
        (project_id, change["doc_id"]),
    ).fetchone()
    return seen is None or store.read_in_transaction(connection, project_id, change["doc_id"], subject=subject) is not None


def _record_links(connection, project_id, task_id, rule_ids, deliveries=()):
    bindings = [(project_id, rule_id, task_id, "", "", "") for rule_id in rule_ids]
    bindings.extend(
        (project_id, rule_id, task_id, item["check"], item["run"], item.get("result_id", ""))
        for rule_id in rule_ids for item in deliveries
    )
    connection.executemany("INSERT OR IGNORE INTO impact_links VALUES (?, ?, ?, ?, ?, ?)", bindings)
    return {"rule_ids": sorted(set(rule_ids))}


def enqueue_links(store, actor, connection, task_id, refs):
    for ref in refs:
        if _read_ref(store, connection, actor, ref) is None:
            raise RuleValidationError(["source_unavailable"])
    return _record_links(connection, actor["project_id"], task_id, {parse_reference(ref)[0] for ref in refs})


def record_checks(store, actor, task_id, rule_ids, deliveries):
    project_id, subject = actor["project_id"], actor["actor_id"]
    with store.transaction() as connection:
        if not _task_is_readable(store, connection, actor, task_id):
            raise PermissionError("Impact task must be readable in the project")
        for rule_id in rule_ids:
            linked = connection.execute(
                "SELECT 1 FROM impact_links WHERE project_id = ? AND rule_id = ? AND task_id = ? LIMIT 1",
                (project_id, rule_id, task_id),
            ).fetchone()
            if not linked or store.read_in_transaction(connection, project_id, rule_id, subject=subject) is None:
                raise PermissionError("Impact rule must be linked and readable in the project")
        _record_links(connection, project_id, task_id, rule_ids, deliveries)


def impact_index(store, actor):
    project_id, subject = actor["project_id"], actor["actor_id"]
    index = ImpactIndex()
    visible_rules = set()
    task_access = {}
    with store.connection() as connection:
        connection.execute("BEGIN")
        for row in connection.execute(
            "SELECT knowledge_id AS rule_id FROM knowledge_cards WHERE scope = ? AND status = 'active' AND effective_on <= ? "
            "UNION SELECT rule_id FROM impact_links WHERE project_id = ?",
            (project_id, _today().isoformat(), project_id),
        ):
            rule_id = row["rule_id"]
            if store.read_in_transaction(connection, project_id, rule_id, subject=subject) is not None:
                visible_rules.add(rule_id)
                index.add_rule(rule_id)
        for row in connection.execute("SELECT * FROM impact_links WHERE project_id = ?", (project_id,)):
            if row["rule_id"] not in visible_rules:
                continue
            task_id = row["task_id"]
            if task_id not in task_access:
                task_access[task_id] = _task_is_readable(store, connection, actor, task_id)
            if task_access[task_id]:
                index.link(
                    row["rule_id"], task_id=task_id, check_id=row["check_id"] or None,
                    run_id=row["run_id"] or None, result_id=row["result_id"] or None,
                )
    return index, visible_rules
