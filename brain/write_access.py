"""Authorization at the changeset transaction boundary."""

from contextlib import contextmanager
from secrets import token_urlsafe
from threading import Lock
from time import monotonic

from .policy import request_digest


class ApprovalRegistry:
    """Bind explicit operator confirmation to a session and an exact request."""

    def __init__(self, *, clock=monotonic, ttl=300, capacity=1024):
        self._entries = {}
        self._lock = Lock()
        self._clock = clock
        self._ttl = ttl
        self._capacity = capacity

    def issue(self, session, action, request):
        digest = request_digest(request)
        with self._lock:
            now = self._clock()
            self._entries = {key: entry for key, entry in self._entries.items() if entry[3] > now}
            if len(self._entries) >= self._capacity:
                del self._entries[next(iter(self._entries))]
            token = token_urlsafe(32)
            self._entries[token] = session, action, digest, now + self._ttl
            return token

    def _valid(self, session, action, body):
        token = body.get("approval")
        entry = self._entries.get(token) if isinstance(token, str) else None
        if entry is None or entry[3] <= self._clock():
            return False
        request = {key: value for key, value in body.items() if key != "approval"}
        return entry[:3] == (session, action, request_digest(request))

    def valid(self, session, action, body):
        with self._lock:
            return self._valid(session, action, body)

    def consume(self, session, action, body):
        with self._lock:
            if not self._valid(session, action, body):
                return False
            del self._entries[body["approval"]]
            return True

    @contextmanager
    def commit(self, session, action, body, *, required=False):
        if not required and "approval" not in body:
            yield False
            return
        # Keep this token exclusively reserved until the store commits. Exceptions
        # leave it available for an exact retry until the original expiration.
        with self._lock:
            if not self._valid(session, action, body):
                raise PermissionError("Unused exact approval is required")
            yield True
            del self._entries[body["approval"]]


def check_write_access(actor, change, previous, *, approved):
    subject = actor["actor_id"]
    readers = set(change["readers"])
    current = set(previous["readers"]) if previous is not None else {subject}
    if subject not in current or subject not in readers:
        raise PermissionError("Document access is required")
    if readers != current and (actor["role"] != "operator" or not approved):
        raise PermissionError("Changing document access requires operator confirmation")
