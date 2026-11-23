from __future__ import annotations

from collections.abc import Callable
from typing import Any


class SwarmReadOnly:
    """Expose the trusted search function to the Swarm bridge."""

    def __init__(self, search: Callable[[str, str | None], Any]) -> None:
        self._search = search

    def start(self) -> dict[str, str | list[str]]:
        return {"profile": "swarm-read-only", "operations": ["search"]}

    def run(self, query: str, section: str | None = None) -> Any:
        if type(query) is not str or not query.strip():
            return {"status": "invalid_arguments", "result": None}
        if section is not None and type(section) is not str:
            return {"status": "invalid_arguments", "result": None}
        try:
            return self._search(query, section)
        except Exception:
            return {"status": "error", "result": None}
