import json

from .sqlite_store import SQLiteKnowledgeStore, put_revision_in_transaction


class ChangeSetStore(SQLiteKnowledgeStore):
    def apply(self, change_id, project_id, actor, changes):
        with self._lock, self._connection as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._record_changeset(
                connection, change_id, project_id, actor, changes, compensates=None
            )

    def compensate(self, change_id, *, compensation_id, actor):
        with self._lock, self._connection as connection:
            connection.execute("BEGIN IMMEDIATE")
            original = connection.execute(
                "SELECT project_id, before_state, receipt FROM changesets WHERE change_id = ?",
                (change_id,),
            ).fetchone()
            if original is None:
                raise ValueError(f"Unknown changeset {change_id!r}")

            before_state = json.loads(original["before_state"])
            versions = json.loads(original["receipt"])["versions"]
            changes = [
                {
                    "doc_id": doc_id,
                    "expected_version": version,
                    **(before_state[doc_id] or {"text": "", "readers": []}),
                }
                for doc_id, version in versions.items()
            ]
            return self._record_changeset(
                connection,
                compensation_id,
                original["project_id"],
                actor,
                changes,
                compensates=change_id,
            )

    @staticmethod
    def _record_changeset(connection, change_id, project_id, actor, changes, *, compensates):
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

            previous = connection.execute(
                """
                SELECT id, version, text
                FROM document_revisions
                WHERE project_id = ? AND doc_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (project_id, doc_id),
            ).fetchone()
            current_version = previous["version"] if previous is not None else 0
            if current_version != expected_version:
                raise ValueError(
                    f"Version conflict for document {doc_id!r}. "
                    f"Expected {expected_version}, found {current_version}."
                )

            if previous is None:
                before_state[doc_id] = None
            else:
                readers = connection.execute(
                    """
                    SELECT subject FROM revision_readers
                    WHERE revision_id = ?
                    ORDER BY subject
                    """,
                    (previous["id"],),
                )
                before_state[doc_id] = {
                    "text": previous["text"],
                    "readers": [row["subject"] for row in readers],
                }

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
