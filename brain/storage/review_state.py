"""SQLite inbox and decision history for the local review UI."""

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from time import monotonic

from ..review import ReviewLedger


class ReviewConflict(ValueError):
    pass


_FIELDS = "project_id, task_id, candidate_sha, draft_id, draft_revision, queued_at, card_json, impact_json"
_QUEUE_INSERT = f"INSERT INTO review_queue ({_FIELDS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
_DECISION_INSERT = (
    f"INSERT INTO review_decisions ({_FIELDS}, decided_at, decision, actor_id, comment, "
    "published_change_id, compensated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _fields(card):
    return (
        card["project_id"], card["task_id"], card["candidate_sha"],
        card.get("draft_id"), card.get("revision"), card.get("queued_at") or _utc_now(),
        json.dumps(card, ensure_ascii=False), json.dumps(card.get("impact", {}), ensure_ascii=False),
    )


def _card(row):
    card = json.loads(row["card_json"])
    card.update(queued_at=row["queued_at"], impact=json.loads(row["impact_json"]))
    if "published_change_id" in row.keys():
        card.update(published_change_id=row["published_change_id"], compensated_by=row["compensated_by"])
    return card


class ReviewState:
    def __init__(self, store, legacy_path, seed_queue, audit):
        self.store = store
        self.audit = audit
        self._import(legacy_path, seed_queue)

    def _import(self, legacy_path, seed_queue):
        with self.store.transaction() as connection:
            if connection.execute("SELECT 1 FROM review_import").fetchone():
                return
            existing = connection.execute(
                "SELECT 1 FROM review_queue UNION ALL SELECT 1 FROM review_decisions LIMIT 1"
            ).fetchone()
            if existing is None:
                if legacy_path.is_file():
                    history = json.loads(legacy_path.read_text(encoding="utf-8"))
                else:
                    history = {"queue": seed_queue(), "decisions": []}
                for card in history["queue"]:
                    connection.execute(_QUEUE_INSERT, _fields(card))
                for record in history["decisions"]:
                    self._import_decision(connection, record)
            connection.execute("INSERT INTO review_import VALUES (1)")

    @staticmethod
    def _import_decision(connection, record):
        status = "approved" if record["decision"] == "approve" else "changes_requested"
        card = {**record.get("card", {}), "status": status}
        for key in ("project_id", "task_id", "candidate_sha"):
            card[key] = record[key]
        receipt = connection.execute(
            "SELECT change_id FROM drafts WHERE project_id = ? AND draft_id = ? AND revision = ?",
            (card["project_id"], card.get("draft_id"), card.get("revision")),
        ).fetchone()
        published = receipt["change_id"] if receipt and record["decision"] == "approve" else None
        undone = connection.execute(
            "SELECT change_id FROM changesets WHERE compensates = ? LIMIT 1", (published,)
        ).fetchone()
        connection.execute(_DECISION_INSERT, (
            *_fields(card), record.get("decided_at") or _utc_now(), record["decision"], record["actor_id"],
            record["comment"], published, undone["change_id"] if undone else None,
        ))

    def list_reviews(self, project_id):
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM review_queue WHERE project_id = ? ORDER BY queued_at, task_id", (project_id,)
            )
            return [_card(row) for row in rows]

    def get_review(self, project_id, task_id):
        with self.store.connection() as connection:
            key = (project_id, task_id)
            row = connection.execute("SELECT * FROM review_queue WHERE project_id = ? AND task_id = ?", key).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM review_decisions WHERE project_id = ? AND task_id = ? ORDER BY event_id DESC LIMIT 1",
                    key,
                ).fetchone()
            return _card(row) if row is not None else None

    def oldest(self, project_id):
        with self.store.connection() as connection:
            return connection.execute(
                "SELECT MIN(queued_at) FROM review_queue WHERE project_id = ?", (project_id,)
            ).fetchone()[0]

    @contextmanager
    def _mutation(self):
        events = []
        with self.store.connection() as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                yield connection, events.append
            for event in events:
                self.audit.append(**event)

    def enqueue(self, card, *, record_links=None):
        started = monotonic()
        with self._mutation() as (connection, audit):
            created, recorded = self._enqueue(connection, card, record_links)
            audit({
                "trace_id": recorded["trace_id"], "project_id": recorded["project_id"], "step": "review_enqueued",
                "outcome": "ok" if created else "duplicate",
                "details": {"duration_ms": (monotonic() - started) * 1000},
            })
        return created

    @staticmethod
    def _enqueue(connection, card, record_links):
        task = (card["project_id"], card["task_id"])
        sha = card["candidate_sha"]
        queued = connection.execute(
            "SELECT * FROM review_queue WHERE project_id = ? AND task_id = ?", task
        ).fetchone()
        prior = connection.execute(
            "SELECT * FROM review_decisions WHERE project_id = ? AND task_id = ? ORDER BY event_id DESC", task
        ).fetchall()
        exact = next((row for row in [*prior, queued] if row and row["candidate_sha"] == sha), None)
        if exact is not None:
            previous = _card(exact)
            if all(previous.get(key) == card[key] for key in ("author", "draft_id", "revision", "handoff")):
                return False, previous
            raise ReviewConflict("candidate_payload_conflict")
        if queued is not None or (prior and prior[0]["decision"] == "approve"):
            raise ReviewConflict("task_in_review_or_approved")
        if prior:
            previous = _card(prior[0])
            if (previous.get("author"), previous.get("draft_id")) != (card["author"], card["draft_id"]):
                raise ReviewConflict("candidate_owner_conflict")
            if card["revision"] <= previous["revision"]:
                raise ReviewConflict("candidate_revision_conflict")
            card = {**card, "feedback": previous["feedback"]}
        draft = (card["project_id"], card["draft_id"], card["revision"])
        if connection.execute(
            "SELECT 1 FROM review_queue WHERE project_id = ? AND draft_id = ? AND draft_revision = ? "
            "UNION ALL SELECT 1 FROM review_decisions WHERE project_id = ? AND draft_id = ? AND draft_revision = ?",
            (*draft, *draft),
        ).fetchone():
            raise ReviewConflict("draft_revision_already_in_review")
        if record_links is not None:
            card = {**card, "impact": record_links(connection)}
        connection.execute(_QUEUE_INSERT, _fields(card))
        return True, card

    def record_decision(self, project_id, task_id, candidate_sha, decision, comment, actor_id):
        started = monotonic()
        with self._mutation() as (connection, audit):
            key = (project_id, task_id, candidate_sha)
            row = connection.execute(
                "SELECT * FROM review_queue WHERE project_id = ? AND task_id = ? AND candidate_sha = ?", key
            ).fetchone()
            if row is None:
                return False
            card = _card(row)
            feedback = comment if decision == "return" else card.get("feedback")
            if card.get("draft_id") is not None:
                feedback = self._record_ledger(connection, key, decision, comment, actor_id, feedback)
            card.update(status="changes_requested" if decision == "return" else "approved", feedback=feedback)
            connection.execute(_DECISION_INSERT, (*_fields(card), _utc_now(), decision, actor_id, comment, None, None))
            connection.execute(
                "DELETE FROM review_queue WHERE project_id = ? AND task_id = ? AND candidate_sha = ?", key
            )
            audit({
                "trace_id": card.get("trace_id") or task_id, "project_id": project_id, "step": "decision",
                "outcome": "returned" if decision == "return" else "approved",
                "details": {"duration_ms": (monotonic() - started) * 1000},
            })
        return True

    @staticmethod
    def _record_ledger(connection, key, decision, comment, actor_id, feedback):
        project_id, task_id, sha = key
        ledger = ReviewLedger()
        if decision == "approve":
            ledger.approve(task_id, sha, operator=actor_id)
            return feedback
        previous = None
        history = connection.execute(
            "SELECT candidate_sha, comment FROM review_decisions WHERE project_id = ? AND task_id = ? "
            "AND draft_id IS NOT NULL AND decision = 'return' ORDER BY event_id",
            (project_id, task_id),
        )
        for item in history:
            prior = item["candidate_sha"]
            ledger.record_feedback(prior, task_id=task_id, candidate_sha=prior, text=item["comment"],
                                   previous_id=previous)
            previous = prior
        return ledger.record_feedback(sha, task_id=task_id, candidate_sha=sha,
                                      text=comment, previous_id=previous).text
