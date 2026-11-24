"""SQLite storage for redacted audit events."""

import json
import math
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


_DIAGNOSTIC_FIELDS = ("duration_ms", "queue_depth", "bytes")


class AuditLog:
    def __init__(self, path):
        self._database = database = Path(path).resolve()
        self._lock = Lock()
        self._closed = False
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS audit_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    step TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    details TEXT NOT NULL,
                    project_id TEXT
                )"""
            )

            columns = {row[1] for row in connection.execute("PRAGMA table_info(audit_events)")}
            if "project_id" not in columns:
                connection.execute("ALTER TABLE audit_events ADD COLUMN project_id TEXT")

        self._reader_uri = database.as_uri() + "?mode=ro"

        def read_after(cursor=0, *, include_project=False):
            with closing(sqlite3.connect(self._reader_uri, uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT seq, trace_id, step, outcome, timestamp, details, project_id "
                    "FROM audit_events WHERE seq > ? ORDER BY seq",
                    (cursor,),
                ).fetchall()
            events = [dict(row, details=json.loads(row["details"])) for row in rows]
            if not include_project:
                for event in events:
                    event.pop("project_id")
            return events

        self.read_after = read_after

    def sequence_has_gap(self):
        with closing(sqlite3.connect(self._reader_uri, uri=True)) as connection:
            count, final = connection.execute(
                "SELECT COUNT(*), COALESCE((SELECT seq FROM sqlite_sequence WHERE name = 'audit_events'), 0) "
                "FROM audit_events"
            ).fetchone()
        return count != final

    def append(self, *, trace_id, step, outcome, project_id=None, details: Mapping[str, object] | None = None):
        redacted = {}
        if details is not None:
            for key in _DIAGNOSTIC_FIELDS:
                value = details.get(key)
                if type(value) is int and value >= 0:
                    redacted[key] = value
                elif type(value) is float and math.isfinite(value) and value >= 0:
                    redacted[key] = value

        with self._lock, closing(sqlite3.connect(self._database)) as connection, connection:
            if self._closed:
                raise sqlite3.ProgrammingError("Audit log is closed")
            row = connection.execute(
                "INSERT INTO audit_events (trace_id, step, outcome, timestamp, details, project_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (trace_id, step, outcome, datetime.now(timezone.utc).isoformat(), json.dumps(redacted), project_id),
            )
            return row.lastrowid

    def close(self):
        with self._lock:
            self._closed = True
