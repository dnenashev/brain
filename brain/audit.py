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
                    details TEXT NOT NULL
                )"""
            )

        reader_uri = database.as_uri() + "?mode=ro"

        def read_after(cursor=0):
            with closing(sqlite3.connect(reader_uri, uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT seq, trace_id, step, outcome, timestamp, details "
                    "FROM audit_events WHERE seq > ? ORDER BY seq",
                    (cursor,),
                ).fetchall()
            return [dict(row, details=json.loads(row["details"])) for row in rows]

        self.read_after = read_after

    def append(self, *, trace_id, step, outcome, details: Mapping[str, object] | None = None):
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
                "INSERT INTO audit_events (trace_id, step, outcome, timestamp, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (trace_id, step, outcome, datetime.now(timezone.utc).isoformat(), json.dumps(redacted)),
            )
            return row.lastrowid

    def close(self):
        with self._lock:
            self._closed = True
