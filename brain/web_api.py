import json
import re
import socket
from collections.abc import Mapping
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from secrets import compare_digest
from urllib.parse import parse_qs, unquote

from .web import REVIEW_FIELDS, render_queue, render_review, review_url


_HTML = "text/html; charset=utf-8"
_JSON = "application/json"
_TEXT = "text/plain; charset=utf-8"
_TASK_ROUTE = re.compile(r"/(review|api/reviews)/([^/]+)(/decision)?")
_MAX_FORM_BYTES = 16 * 1024


def _error(status, code):
    return status, _JSON, json.dumps({"error": code})


class ReviewWeb:
    def __init__(self, sessions, *, list_reviews, get_review, record_decision, form_token):
        self._sessions = sessions
        self._list_reviews = list_reviews
        self._get_review = get_review
        self._record_decision = record_decision
        self._form_token = form_token

    def dispatch(self, method, path, *, token=None, form=None):
        actor = self._sessions.resolve(token)
        if (
            not isinstance(actor, Mapping)
            or actor.get("verified") is not True
            or not actor.get("actor_id")
            or not actor.get("project_id")
            or actor.get("role") != "operator"
        ):
            return _error(403, "forbidden")
        project_id = actor["project_id"]

        path = path.partition("?")[0]
        if method == "GET" and path == "/":
            return 200, _HTML, render_queue(self._list_reviews(project_id))

        route = _TASK_ROUTE.fullmatch(path)
        if route is None:
            return _error(404, "not_found")
        prefix, task_segment, decision_suffix = route.groups()
        try:
            task_id = unquote(task_segment, errors="strict")
        except UnicodeError:
            return _error(400, "invalid_task_id")

        if decision_suffix:
            if method == "POST" and prefix == "review":
                return self._decide(project_id, task_id, actor["actor_id"], form)
            return _error(404, "not_found")
        if method != "GET":
            return _error(404, "not_found")

        review = self._get_review(project_id, task_id)
        if review is None:
            return _error(404, "not_found")
        if prefix == "api/reviews":
            card = {field: review.get(field) for field in REVIEW_FIELDS}
            return 200, _JSON, json.dumps(card)
        return 200, _HTML, render_review(review, task_id=task_id, form_token=self._form_token)

    def _decide(self, project_id, task_id, actor_id, form):
        supplied_token = form.get("form_token") if isinstance(form, Mapping) else None
        expected_token = self._form_token
        if (
            not isinstance(supplied_token, str)
            or not isinstance(expected_token, str)
            or not expected_token
            or not compare_digest(
                supplied_token.encode("utf-8", errors="surrogatepass"),
                expected_token.encode("utf-8", errors="surrogatepass"),
            )
        ):
            return _error(403, "invalid_form_token")

        decision = form.get("decision")
        comment = form.get("comment", "")
        if decision not in ("approve", "return") or not isinstance(comment, str):
            return _error(400, "invalid_decision")
        if decision == "return" and not comment.strip():
            return _error(400, "reason_required")

        candidate_sha = form.get("candidate_sha")
        if not isinstance(candidate_sha, str) or not candidate_sha:
            return _error(400, "candidate_sha_required")
        review = self._get_review(project_id, task_id)
        if review is None:
            return _error(404, "not_found")
        if candidate_sha != review.get("candidate_sha"):
            return _error(409, "sha_conflict")
        if not self._record_decision(project_id, task_id, candidate_sha, decision, comment, actor_id):
            return _error(409, "sha_conflict")
        return 303, _TEXT, review_url(task_id)


def make_review_server(web, *, host="127.0.0.1", port=0):
    address = ip_address("127.0.0.1" if host == "localhost" else host)
    if not address.is_loopback:
        raise ValueError("review server requires a loopback address")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._dispatch()

        def do_POST(self):
            if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                return self._respond(*_error(415, "unsupported_media_type"))
            lengths = self.headers.get_all("Content-Length", [])
            if (
                self.headers.get("Transfer-Encoding") is not None
                or len(lengths) != 1
                or re.fullmatch(r"[0-9]+", lengths[0]) is None
            ):
                return self._respond(*_error(400, "invalid_content_length"))
            try:
                length = int(lengths[0])
            except ValueError:
                return self._respond(*_error(400, "invalid_content_length"))
            if length > _MAX_FORM_BYTES:
                return self._respond(*_error(413, "form_too_large"))

            raw_form = self.rfile.read(length)
            if len(raw_form) != length:
                return self._respond(*_error(400, "invalid_form"))
            try:
                fields = parse_qs(
                    raw_form.decode("utf-8"),
                    keep_blank_values=True,
                    strict_parsing=True,
                    errors="strict",
                    max_num_fields=16,
                )
            except ValueError:
                return self._respond(*_error(400, "invalid_form"))
            if any(len(values) != 1 for values in fields.values()):
                return self._respond(*_error(400, "invalid_form"))
            self._dispatch(form={name: values[0] for name, values in fields.items()})

        def _dispatch(self, *, form=None):
            cookies = SimpleCookie()
            try:
                cookies.load("; ".join(self.headers.get_all("Cookie", [])))
            except CookieError:
                return self._respond(*_error(403, "forbidden"))
            session = cookies.get("brain_session")
            token = session.value if session is not None else None
            self._respond(*web.dispatch(self.command, self.path, token=token, form=form))

        def _respond(self, status, content_type, body):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            if status == 303:
                self.send_header("Location", body)
            self.end_headers()
            self.wfile.write(payload)

    class ReviewServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET

    return ReviewServer((str(address), port), Handler)
