import ipaddress
import json
import socket
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

from . import policy


_DATA_ACTIONS = {"/read": "read", "/search": "read", "/write": "write"}


class KnowledgeService:
    def __init__(self, store, changesets, sessions):
        self._store = store
        self._changesets = changesets
        self._sessions = sessions
        self._lock = Lock()
        self._closed = False

    def dispatch(self, method, path, *, token=None, body=None):
        with self._lock:
            if method == "GET" and path == "/health":
                return 200, {"status": "ok"}
            if self._closed:
                return 503, {"error": "service_unavailable"}
            if method == "GET" and path == "/ready":
                return 200, {"status": "ready"}
            if method != "POST":
                return 404, {"error": "not_found"}
            if path == "/session":
                return self._open_session(body)
            action = _DATA_ACTIONS.get(path)
            if action is None:
                return 404, {"error": "not_found"}

            actor = self._sessions.resolve(token)
            if actor is None:
                return 401, {"error": "unauthorized"}
            if not isinstance(body, dict) or "project_id" not in body:
                return 400, {"error": "invalid_request"}
            project_id = body["project_id"]
            # Document versions belong to the stores. These routes have no publish revision.
            revision = 0
            request = {"project_id": project_id, "action": action, "revision": revision}
            if not policy.authorize(actor, request, current_revision=revision):
                return 403, {"error": "forbidden"}

            subject = actor["actor_id"]
            try:
                if path == "/read":
                    return self._read(project_id, subject, body)
                if path == "/search":
                    query = body.get("query")
                    if not isinstance(query, str):
                        return 400, {"error": "invalid_request"}
                    return 200, {"results": self._store.search(project_id, query, subject=subject)}
                return self._write(project_id, subject, body)
            except (OverflowError, UnicodeError):
                return 400, {"error": "invalid_request"}
            except sqlite3.Error:
                return 500, {"error": "storage_error"}

    def _open_session(self, body):
        if not isinstance(body, dict) or "credential" not in body:
            return 400, {"error": "invalid_request"}
        try:
            token = self._sessions.open(body["credential"])
        except (TypeError, ValueError):
            return 400, {"error": "invalid_request"}
        if token is None:
            return 401, {"error": "unauthorized"}
        return 200, {"token": token}

    def _read(self, project_id, subject, body):
        doc_id = body.get("doc_id")
        version = body.get("version")
        if not isinstance(doc_id, str) or (version is not None and type(version) is not int):
            return 400, {"error": "invalid_request"}
        document = self._store.read(project_id, doc_id, subject=subject, version=version)
        if document is None:
            return 404, {"error": "not_found"}
        return 200, document

    def _write(self, project_id, subject, body):
        change_id = body.get("change_id")
        changes = body.get("changes")
        if not isinstance(change_id, str) or not isinstance(changes, list):
            return 400, {"error": "invalid_request"}
        for change in changes:
            if not isinstance(change, dict):
                return 400, {"error": "invalid_request"}
            readers = change.get("readers")
            if (
                not isinstance(change.get("doc_id"), str)
                or not isinstance(change.get("text"), str)
                or type(change.get("expected_version")) is not int
                or not isinstance(readers, list)
                or any(not isinstance(reader, str) for reader in readers)
            ):
                return 400, {"error": "invalid_request"}
        try:
            receipt = self._changesets.apply(change_id, project_id, subject, changes)
        except ValueError as error:
            message = str(error)
            if message.startswith("Version conflict ") or (
                message.startswith("Changeset ") and message.endswith(" already has a different payload")
            ):
                return 409, {"error": "conflict"}
            return 400, {"error": "invalid_request"}
        return 200, receipt

    def close(self):
        with self._lock:
            self._closed = True


class _KnowledgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        try:
            body = self._read_json()
        except (ValueError, OverflowError, RecursionError):
            self._send_json(400, {"error": "invalid_request"})
            return
        self._dispatch(body)

    def _read_json(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or len(lengths) > 1:
            raise ValueError("Unsupported request framing")
        if not lengths:
            return None
        length = lengths[0].strip()
        if not length.isascii() or not length.isdecimal():
            raise ValueError("Invalid content length")
        remaining = int(length)
        if remaining == 0:
            return None
        payload = bytearray()
        while remaining:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                raise ValueError("Incomplete request body")
            payload.extend(chunk)
            remaining -= len(chunk)
        return json.loads(payload)

    def _dispatch(self, body=None):
        authorization = self.headers.get_all("Authorization", [])
        token = None
        if len(authorization) == 1:
            parts = authorization[0].split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]
        path = self.path.partition("?")[0]
        status, response = self.server.service.dispatch(self.command, path, token=token, body=body)
        self._send_json(status, response)

    def _send_json(self, status, response):
        payload = json.dumps(response).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


class _KnowledgeHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, service, address_family):
        self.service = service
        self.address_family = address_family
        super().__init__(address, _KnowledgeHandler)


def make_http_server(service, *, host="127.0.0.1", port=0):
    if host == "localhost":
        host = "127.0.0.1"
    address = ipaddress.ip_address(host)
    if not address.is_loopback:
        raise ValueError("A loopback address is required")
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    return _KnowledgeHTTPServer((str(address), port), service, family)
