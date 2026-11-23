from collections.abc import Mapping
from urllib.parse import unquote

from brain.policy import authorize


def _safe_relative_path(path):
    if not isinstance(path, str):
        return False

    decoded = unquote(path)
    if unquote(decoded) != decoded:
        return False

    for candidate in (path, decoded):
        parts = candidate.split("/")
        if (
            "\\" in candidate
            or ":" in parts[0]
            or any(part in ("", ".", "..") for part in parts)
            or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
        ):
            return False
    return True


def _refused(reason):
    return {"outcome": "refused", "reason": reason}


class SourceAdapter:
    def __init__(self, destinations, *, allowlist, broker, read_transport, write_transport=None):
        self._destinations = destinations
        self._allowlist = frozenset(allowlist)
        self._broker = broker
        self._read_transport = read_transport
        self._write_transport = write_transport

    def _resolve(self, actor, request, *, action, current_revision):
        if (
            not isinstance(request, dict)
            or request.get("action") != action
            or not authorize(actor, request, current_revision=current_revision)
        ):
            return None

        source_id = request.get("source_id")
        path = request.get("path")
        if source_id is None or not _safe_relative_path(path):
            return None

        config = self._destinations.get(source_id)
        if not isinstance(config, Mapping):
            return None

        project_id = request["project_id"]
        source_project = config.get("project_id")
        destination = config.get("destination")
        if (
            type(source_project) is not type(project_id)
            or source_project != project_id
            or not isinstance(destination, str)
            or not destination
            or destination not in self._allowlist
        ):
            return None

        if action == "write" and (config.get("writable") is not True or "content" not in request):
            return None

        return source_id, destination, path

    def read(self, actor, request, *, current_revision):
        try:
            source = self._resolve(actor, request, action="read", current_revision=current_revision)
            if source is None:
                return _refused("source access denied")
            source_id, destination, path = source
            content = self._broker.invoke(
                source_id, lambda secret: self._read_transport(destination, path, secret)
            )
        except Exception:
            return _refused("source read failed")

        if type(content) is not str:
            return _refused("source read failed")
        return {"outcome": "result", "content": content}

    def write(self, actor, request, *, current_revision):
        try:
            source = self._resolve(actor, request, action="write", current_revision=current_revision)
            if source is None or self._write_transport is None:
                return _refused("source write denied")
            source_id, destination, path = source
            content = request["content"]
            confirmed = self._broker.invoke(
                source_id,
                lambda secret: self._write_transport(
                    destination, path, content, current_revision, secret
                ),
            )
        except Exception:
            return _refused("source write failed")

        if confirmed is not True:
            return _refused("source write unconfirmed")
        return {"outcome": "written"}
