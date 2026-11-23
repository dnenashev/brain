import sqlite3
from pathlib import Path
from threading import Lock


def _execute_migration(connection, migration):
    sql = migration.read_text(encoding="utf-8")
    start = 0
    for end, character in enumerate(sql, start=1):
        if character == ";":
            statement = sql[start:end]
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                start = end

    remainder = sql[start:]
    if remainder.strip():
        statement = remainder + "\n;"
        if not sqlite3.complete_statement(statement):
            raise RuntimeError(f"Incomplete SQLite migration {migration.name}")
        connection.execute(statement)


def put_revision_in_transaction(connection, project_id, doc_id, text, readers):
    """Write a revision using the caller's transaction."""
    previous = connection.execute(
        """
        SELECT id, version
        FROM document_revisions
        WHERE project_id = ? AND doc_id = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (project_id, doc_id),
    ).fetchone()
    previous_id, previous_version = previous if previous is not None else (None, 0)
    version = previous_version + 1
    revision_id = connection.execute(
        """
        INSERT INTO document_revisions (project_id, doc_id, version, text)
        VALUES (?, ?, ?, ?)
        """,
        (project_id, doc_id, version, text),
    ).lastrowid
    connection.executemany(
        "INSERT INTO revision_readers (revision_id, subject) VALUES (?, ?)",
        ((revision_id, reader) for reader in dict.fromkeys(readers)),
    )
    if previous_id is not None:
        connection.execute(
            "DELETE FROM current_revision_fts WHERE rowid = ?",
            (previous_id,),
        )
    connection.execute(
        "INSERT INTO current_revision_fts (rowid, text) VALUES (?, ?)",
        (revision_id, text),
    )
    return version


class SQLiteKnowledgeStore:
    def __init__(self, path):
        self._lock = Lock()
        self._connection = sqlite3.connect(
            path, isolation_level=None, check_same_thread=False
        )
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._migrate()
        except BaseException:
            self._connection.close()
            raise

    def _migrate(self):
        directory = Path(__file__).with_name("migrations")
        migrations = sorted(
            (int(path.stem), path)
            for path in directory.glob("*.sql")
            if path.stem.isascii() and path.stem.isdecimal()
        )
        if not migrations or any(
            number != expected
            for expected, (number, _) in enumerate(migrations, start=1)
        ):
            raise RuntimeError("SQLite migrations must be numbered consecutively from 001.sql")

        with self._connection as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if not 0 <= version <= len(migrations):
                raise RuntimeError(f"Unsupported SQLite schema version {version}")

            # Keep the version check, migration statements, and version updates in one transaction.
            for number, migration in migrations[version:]:
                _execute_migration(connection, migration)
                connection.execute(f"PRAGMA user_version = {number}")

    def put_revision(self, project_id, doc_id, text, readers):
        with self._lock, self._connection as connection:
            connection.execute("BEGIN IMMEDIATE")
            return put_revision_in_transaction(connection, project_id, doc_id, text, readers)

    def read(self, project_id, doc_id, *, subject, version=None):
        with self._lock:
            row = self._connection.execute(
                """
                WITH latest AS (
                    SELECT id, version
                    FROM document_revisions
                    WHERE project_id = :project_id AND doc_id = :doc_id
                    ORDER BY version DESC
                    LIMIT 1
                )
                SELECT revision.doc_id, revision.version, revision.text
                FROM latest
                JOIN revision_readers AS current_reader
                    ON current_reader.revision_id = latest.id
                    AND current_reader.subject = :subject
                JOIN document_revisions AS revision
                    ON revision.project_id = :project_id
                    AND revision.doc_id = :doc_id
                    AND revision.version = COALESCE(:version, latest.version)
                JOIN revision_readers AS requested_reader
                    ON requested_reader.revision_id = revision.id
                    AND requested_reader.subject = :subject
                """,
                {
                    "project_id": project_id,
                    "doc_id": doc_id,
                    "subject": subject,
                    "version": version,
                },
            ).fetchone()
        return dict(row) if row is not None else None

    def snapshot(self, project_id, *, subject):
        with self._lock:
            rows = self._connection.execute(
                """
                WITH latest AS (
                    SELECT doc_id, MAX(version) AS version
                    FROM document_revisions
                    WHERE project_id = :project_id
                    GROUP BY doc_id
                )
                SELECT revision.doc_id, revision.version, revision.text
                FROM latest
                JOIN document_revisions AS revision
                    ON revision.project_id = :project_id
                    AND revision.doc_id = latest.doc_id
                    AND revision.version = latest.version
                JOIN revision_readers AS reader
                    ON reader.revision_id = revision.id
                    AND reader.subject = :subject
                ORDER BY revision.doc_id
                """,
                {"project_id": project_id, "subject": subject},
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, project_id, query, *, subject):
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT revision.doc_id, revision.version,
                    snippet(current_revision_fts, 0, '[', ']', '...', 16) AS snippet
                FROM current_revision_fts
                JOIN document_revisions AS revision
                    ON revision.id = current_revision_fts.rowid
                JOIN revision_readers AS reader
                    ON reader.revision_id = revision.id
                    AND reader.subject = :subject
                WHERE revision.project_id = :project_id
                    AND current_revision_fts MATCH :query
                ORDER BY revision.doc_id
                """,
                {"project_id": project_id, "subject": subject, "query": query},
            ).fetchall()
        return [dict(row) for row in rows]
