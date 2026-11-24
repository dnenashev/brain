import json

from .sqlite_store import SQLiteKnowledgeStore, put_revision_in_transaction


class DraftConflict(ValueError):
    pass


class ChangeSetStore(SQLiteKnowledgeStore):
    def apply(self, change_id, project_id, actor, changes, *, authorize_change=None, validate_batch=None):
        with self._lock, self._database() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._record_changeset(
                connection, change_id, project_id, actor, changes, compensates=None,
                authorize_change=authorize_change, validate_batch=validate_batch,
            )

    def compensate(self, change_id, *, compensation_id, actor, project_id=None, authorize_change=None):
        with self._lock, self._database() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            original = connection.execute(
                "SELECT project_id, before_state, receipt, compensates FROM changesets WHERE change_id = ?",
                (change_id,),
            ).fetchone()
            if project_id is not None and (original is None or original["project_id"] != project_id):
                raise PermissionError("Changeset must belong to the project")
            if original is None:
                raise ValueError(f"Unknown changeset {change_id!r}")

            if project_id is not None:
                if original["compensates"] is not None:
                    raise DraftConflict("Compensation cannot itself be compensated")
                executed = connection.execute(
                    "SELECT change_id FROM changesets WHERE compensates = ? LIMIT 1", (change_id,)
                ).fetchone()
                if executed is not None and executed["change_id"] != compensation_id:
                    raise DraftConflict("The changeset was already compensated")

            before_state = json.loads(original["before_state"])
            versions = json.loads(original["receipt"])["versions"]
            changes = [
                {
                    "doc_id": doc_id,
                    "expected_version": version,
                    "text": before_state[doc_id]["text"] if before_state[doc_id] else "",
                    "readers": before_state[doc_id]["readers"] if before_state[doc_id] else [],
                }
                for doc_id, version in versions.items()
            ]
            receipt = self._record_changeset(
                connection,
                compensation_id,
                original["project_id"],
                actor,
                changes,
                compensates=change_id, authorize_change=authorize_change,
            )
            connection.execute(
                "UPDATE review_decisions SET compensated_by = ? WHERE project_id = ? AND published_change_id = ?",
                (compensation_id, original["project_id"], change_id),
            )
            for doc_id, version in versions.items():
                connection.execute(
                    "UPDATE knowledge_cards SET status = 'superseded', changed_by = ? WHERE scope = ? "
                    "AND knowledge_id = ? AND revision = ? AND status = 'active'",
                    (actor, original["project_id"], doc_id, version),
                )
            return receipt

    def save_draft(self, project_id, draft_id, actor, change, *, authorize_change, validate_batch, max_drafts):
        with self._lock, self._database() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            versions = connection.execute(
                "SELECT COUNT(*), MAX(revision) FILTER (WHERE draft_id = ?) FROM drafts WHERE project_id = ?",
                (draft_id, project_id),
            ).fetchone()
            if versions[0] >= max_drafts:
                return None
            owner = connection.execute(
                "SELECT author FROM drafts WHERE project_id = ? AND draft_id = ? LIMIT 1",
                (project_id, draft_id),
            ).fetchone()
            if owner is not None and owner["author"] != actor:
                raise PermissionError("Draft belongs to another author")
            doc_id = change["doc_id"]
            previous = self._current_state(connection, project_id, doc_id)
            authorize_change(change, previous)
            if change["expected_version"] != (previous["version"] if previous else 0):
                raise DraftConflict("The knowledge version changed")
            validate_batch(connection, {doc_id: previous}, [change])
            revision = (versions[1] or 0) + 1
            connection.execute(
                "INSERT INTO drafts (project_id, draft_id, revision, author, payload) VALUES (?, ?, ?, ?, ?)",
                (project_id, draft_id, revision, actor, json.dumps(change, sort_keys=True)),
            )
            return revision

    def get_review_draft(self, project_id, draft_id, revision, *, actor):
        with self._lock, self._database() as connection:
            draft = connection.execute(
                "SELECT payload FROM drafts WHERE project_id = ? AND draft_id = ? AND revision = ? AND author = ?",
                (project_id, draft_id, revision, actor),
            ).fetchone()
            return json.loads(draft["payload"]) if draft is not None else None

    def publish_draft(self, project_id, draft_id, revision, *, actor, change_id,
                      authorize_publish, authorize_change, validate_batch, review=None, record_rule=None):
        with self._lock, self._database() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = connection.execute(
                "SELECT revision, payload, change_id, author FROM drafts "
                "WHERE project_id = ? AND draft_id = ? ORDER BY revision DESC LIMIT 1",
                (project_id, draft_id),
            ).fetchone()
            if draft is None or not authorize_publish(draft["revision"]):
                raise PermissionError("Approval for the latest draft revision is required")
            decision_key = (project_id, *review, draft_id, revision) if review else None
            if decision_key is not None:
                decision = connection.execute(
                    "SELECT event_id FROM review_decisions WHERE project_id = ? AND task_id = ? "
                    "AND candidate_sha = ? AND draft_id = ? AND draft_revision = ? AND decision = 'approve'",
                    decision_key,
                ).fetchone()
                if decision is None:
                    raise PermissionError("Candidate must be approved for this draft revision")
            if draft["change_id"] not in (None, change_id):
                raise DraftConflict("The draft was already published")
            if draft["change_id"] is None and connection.execute(
                "SELECT 1 FROM changesets WHERE change_id = ?", (change_id,)
            ).fetchone():
                raise DraftConflict("Publication requires a new changeset ID")
            change = json.loads(draft["payload"])
            receipt = self._record_changeset(
                connection, change_id, project_id, actor, [change],
                compensates=None, authorize_change=authorize_change, validate_batch=validate_batch,
            )
            connection.execute(
                "UPDATE drafts SET change_id = ? WHERE project_id = ? AND draft_id = ? AND revision = ?",
                (change_id, project_id, draft_id, revision),
            )
            if record_rule is not None and draft["change_id"] is None:
                record_rule(connection, change, receipt["versions"][change["doc_id"]], draft["author"])
            if decision_key is not None:
                connection.execute("UPDATE review_decisions SET published_change_id = ? WHERE event_id = ?",
                                   (change_id, decision["event_id"]))
            return receipt

    @staticmethod
    def _current_state(connection, project_id, doc_id):
        previous = connection.execute(
            "SELECT id, version, text FROM document_revisions WHERE project_id = ? AND doc_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (project_id, doc_id),
        ).fetchone()
        if previous is None:
            return None
        readers = connection.execute(
            "SELECT subject FROM revision_readers WHERE revision_id = ? ORDER BY subject", (previous["id"],)
        )
        return {
            "version": previous["version"], "text": previous["text"],
            "readers": [row["subject"] for row in readers],
        }

    @staticmethod
    def _record_changeset(connection, change_id, project_id, actor, changes, *, compensates,
                          authorize_change=None, validate_batch=None):
        payload = json.dumps(changes, sort_keys=True)
        existing = connection.execute(
            """
            SELECT project_id, actor, payload, compensates, receipt
            FROM changesets
            WHERE change_id = ?
            """,
            (change_id,),
        ).fetchone()
        if existing is not None:
            if (existing["project_id"], existing["actor"], existing["payload"],
                    existing["compensates"]) != (project_id, actor, payload, compensates):
                raise ValueError(f"Changeset {change_id!r} already has a different payload")
            return json.loads(existing["receipt"])

        before_state = {}
        for change in changes:
            doc_id = change["doc_id"]
            expected_version = change["expected_version"]
            if not isinstance(doc_id, str):
                raise ValueError("doc_id must be a string")
            if type(expected_version) is not int or expected_version < 0:
                raise ValueError("expected_version must be a nonnegative integer")
            if doc_id in before_state:
                raise ValueError(f"Duplicate document {doc_id!r} in changeset")

            previous = ChangeSetStore._current_state(connection, project_id, doc_id)
            current_version = previous["version"] if previous is not None else 0
            before_state[doc_id] = previous
            if authorize_change is not None:
                authorize_change(change, before_state[doc_id])
            if current_version != expected_version:
                raise ValueError(
                    f"Version conflict for document {doc_id!r}. "
                    f"Expected {expected_version}, found {current_version}."
                )

        if validate_batch is not None:
            validate_batch(connection, before_state, changes)

        versions = {}
        for change in changes:
            doc_id = change["doc_id"]
            versions[doc_id] = put_revision_in_transaction(
                connection, project_id, doc_id, change["text"], change["readers"]
            )

        receipt = {
            "change_id": change_id,
            "project_id": project_id,
            "actor": actor,
            "versions": versions,
            "compensates": compensates,
        }
        connection.execute(
            """
            INSERT INTO changesets
                (change_id, project_id, actor, payload, before_state, receipt, compensates)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change_id, project_id, actor, payload, json.dumps(before_state),
                json.dumps(receipt), compensates,
            ),
        )
        return receipt
