import ipaddress
import json
import re
import socket
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from uuid import uuid4

from . import policy
from .live_workflow import ACTIONS, WorkflowError, validate_change
from .operations.live_rules import RuleValidationError
from .storage.changesets import DraftConflict
from .storage.review_state import ReviewConflict
from .write_access import ApprovalRegistry, check_write_access


_MAX_JSON_BYTES = 256 * 1024
_TRACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def valid_trace_id(value):
    return isinstance(value, str) and _TRACE.fullmatch(value) is not None


def _invalid_json_constant(value):
    raise ValueError("JSON constants must be finite")


class PayloadTooLarge(ValueError):
    pass


_DATA_ACTIONS = {"/read": "read", "/search": "read", "/write": "write"}


class KnowledgeService:
    def __init__(self, store, changesets, sessions, *, project_subjects=None, workflow=None, readiness=None):
        self._store = store
        self._changesets = changesets
        self._sessions = sessions
        self._project_subjects = project_subjects or {}
        self._workflow = workflow
        self._readiness = readiness
        self._approvals = ApprovalRegistry()
        self._lock = Lock()
        self._closed = False

    def dispatch(self, method, path, *, token=None, body=None, trace_id=None):
        with self._lock:
            if method == "GET" and path == "/health":
                return 200, {"status": "ok"}
            if self._closed:
                return 503, {"error": "service_unavailable"}
            if method == "GET" and path == "/ready":
                if self._readiness is not None:
                    try:
                        if not self._readiness():
                            return 503, {"ready": False, "reason": "audit_sequence_gap"}
                    except (sqlite3.Error, ValueError):
                        return 503, {"ready": False, "reason": "audit_unavailable"}
                return 200, {"status": "ready"}
            if method != "POST":
                return 404, {"error": "not_found"}
            if path == "/session":
                return self._open_session(body)
            if path == "/logout":
                self._sessions.revoke(token)
                return 200, {"status": "logged_out"}
            action = "publish" if path == "/approval" else _DATA_ACTIONS.get(path)
            if action is None and self._workflow is not None:
                action = ACTIONS.get(path)
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
            if path == "/approval":
                return self._issue_approval(actor, token, body)
            if not policy.authorize(actor, request, current_revision=revision):
                return 403, {"error": "forbidden"}

            subject = actor["actor_id"]
            try:
                if self._workflow is not None and path not in ("/identity", "/ops/audit-summary"):
                    self._workflow.check_directives(actor, body, trace_id or uuid4().hex)
                if path in ACTIONS:
                    return self._workflow.dispatch(path, actor, token, body, trace_id, self._approvals)
                if path == "/read":
                    return self._read(project_id, subject, body)
                if path == "/search":
                    query = body.get("query")
                    if not isinstance(query, str):
                        return 400, {"error": "invalid_request"}
                    return 200, {"results": self._store.search(project_id, query, subject=subject)}
                return self._write(actor, token, body)
            except WorkflowError as error:
                return error.status, error.response
            except RuleValidationError as error:
                return 422, {"error": "rule_lint", "issues": error.issues}
            except ReviewConflict as error:
                return 409, {"error": str(error)}
            except ValueError as error:
                message = str(error)
                if isinstance(error, DraftConflict) or message.startswith("Version conflict ") or (
                    message.startswith("Changeset ") and message.endswith(" already has a different payload")
                ):
                    return 409, {"error": "conflict"}
                return 400, {"error": "invalid_request"}
            except PermissionError:
                return 403, {"error": "forbidden"}
            except (OverflowError, TypeError, RecursionError):
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

    def _issue_approval(self, actor, token, body):
        if (actor.get("verified") is not True or actor.get("role") != "operator"
                or body["project_id"] != actor.get("project_id")):
            return 403, {"error": "forbidden"}
        action = body.get("action")
        request = body.get("request")
        if (action not in ("write", "publish") or not isinstance(request, dict)
                or request.get("project_id") != actor["project_id"] or "approval" in request):
            return 400, {"error": "invalid_request"}
        try:
            approval = self._approvals.issue(token, action, request)
        except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
            return 400, {"error": "invalid_request"}
        return 200, {"approval": approval}

    def _write(self, actor, token, body):
        change_id = body.get("change_id")
        changes = body.get("changes")
        if not isinstance(change_id, str) or not isinstance(changes, list):
            return 400, {"error": "invalid_request"}
        for change in changes:
            validate_change(change)
        with self._approvals.commit(token, "write", body) as approved:
            return self._apply_write(actor, body, approved=approved)

    def _apply_write(self, actor, body, *, approved):
        project_id, subject = actor["project_id"], actor["actor_id"]
        change_id, changes = body["change_id"], body["changes"]
        if self._workflow is not None:
            return 200, self._workflow.write(actor, body, approved=approved)

        members = self._project_subjects.get(project_id, {subject})
        for change in changes:
            if not set(change["readers"]).issubset(members):
                raise PermissionError("Readers must belong to the project")

        def authorize_change(change, previous):
            check_write_access(actor, change, previous, approved=approved)

        return 200, self._changesets.apply(
            change_id, project_id, subject, changes, authorize_change=authorize_change
        )

    def close(self):
        with self._lock:
            self._closed = True
            self._store.close()
            if self._changesets is not self._store:
                self._changesets.close()


class _KnowledgeHandler(BaseHTTPRequestHandler):
    def _set_trace(self):
        values = self.headers.get_all("X-Trace-Id", [])
        self.trace_id = None
        if len(values) > 1 or (values and not valid_trace_id(values[0])):
            self.close_connection = True
            self._send_json(400, {"error": "invalid_trace_id"})
            return False
        self.trace_id = values[0] if values else uuid4().hex
        return True

    def do_GET(self):
        if self._set_trace():
            self._dispatch()

    def do_POST(self):
        if not self._set_trace():
            return
        try:
            body = self._read_json()
        except PayloadTooLarge:
            self.close_connection = True
            self._send_json(413, {"error": "payload_too_large"})
            return
        except (ValueError, OverflowError, RecursionError):
            self.close_connection = True
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
        if remaining > _MAX_JSON_BYTES:
            raise PayloadTooLarge
        if remaining == 0:
            return None
        payload = bytearray()
        while remaining:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                raise ValueError("Incomplete request body")
            payload.extend(chunk)
            remaining -= len(chunk)
        return json.loads(payload, parse_constant=_invalid_json_constant)

    def _dispatch(self, body=None):
        authorization = self.headers.get_all("Authorization", [])
        token = None
        if len(authorization) == 1:
            parts = authorization[0].split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]
        path = self.path.partition("?")[0]
        status, response = self.server.service.dispatch(
            self.command, path, token=token, body=body, trace_id=self.trace_id
        )
        self._send_json(status, response)

    def _send_json(self, status, response):
        payload = json.dumps(response).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if self.trace_id is not None:
            self.send_header("X-Trace-Id", self.trace_id)
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
