from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import Lock
from typing import Any


_ROLE_OPERATIONS = {
    "reader": frozenset({"read", "search", "check"}),
    "writer": frozenset({"read", "search", "check", "draft"}),
    "operator": frozenset({"read", "search", "check", "draft", "task"}),
}


class VersionedSDK:
    """Dispatch authorized operations through the revision pinned to a session."""

    def __init__(self) -> None:
        self._revisions: dict[str, dict[str, Callable[..., Any]]] = {}
        self._sessions: dict[str, tuple[str, str]] = {}
        self._current_revision: str | None = None
        self._lock = Lock()

    def register(self, revision: str, handlers: Mapping[str, Callable[..., Any]]) -> None:
        with self._lock:
            if revision in self._revisions:
                raise ValueError(f"SDK revision already registered: {revision!r}")
            registered = dict(handlers)
            for operation, handler in registered.items():
                if operation not in _ROLE_OPERATIONS["operator"]:
                    raise ValueError(f"Unknown SDK operation: {operation!r}")
                if not callable(handler):
                    raise TypeError(f"Handler for {operation!r} must be callable")
            self._revisions[revision] = registered
            self._current_revision = revision

    def open_session(self, session_id: str, role: str) -> str:
        if role not in _ROLE_OPERATIONS:
            raise ValueError(f"Unknown SDK role: {role!r}")
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                revision, existing_role = existing
                if role != existing_role:
                    raise ValueError("Cannot change the role of an open SDK session")
                return revision

            revision = self._current_revision
            if revision is None:
                raise RuntimeError("No SDK revision has been registered")
            self._sessions[session_id] = (revision, role)
            return revision

    def describe(self, session_id: str) -> dict[str, str | list[str]]:
        with self._lock:
            revision, role = self._session(session_id)
            operations = sorted(_ROLE_OPERATIONS[role].intersection(self._revisions[revision]))
            return {"revision": revision, "operations": operations}

    def call(self, session_id: str, operation: str, **parameters: Any) -> Any:
        with self._lock:
            revision, role = self._session(session_id)
            handlers = self._revisions[revision]
            if operation not in _ROLE_OPERATIONS[role] or operation not in handlers:
                raise PermissionError(f"SDK operation unavailable: {operation!r}")
            handler = handlers[operation]
        return handler(**parameters)

    def close_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _session(self, session_id: str) -> tuple[str, str]:
        session = self._sessions.get(session_id)
        if session is None:
            raise PermissionError("SDK session is not open")
        return session
