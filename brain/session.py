from copy import deepcopy
from secrets import token_urlsafe
from threading import Lock
from time import monotonic


class SessionRegistry:
    def __init__(self, authenticate, *, ttl=3600, clock=monotonic, capacity=2048):
        if ttl <= 0 or capacity <= 0:
            raise ValueError("Session TTL and capacity must be positive")
        self._authenticate = authenticate
        self._ttl = ttl
        self._clock = clock
        self._capacity = capacity
        self._sessions = {}
        self._lock = Lock()

    def open(self, credential):
        actor = self._authenticate(credential)
        if actor is None:
            return None

        actor = deepcopy(actor)
        with self._lock:
            now = self._clock()
            self._sessions = {key: entry for key, entry in self._sessions.items() if entry[2] > now}
            if len(self._sessions) >= self._capacity:
                del self._sessions[next(iter(self._sessions))]
            token = token_urlsafe(32)
            while token in self._sessions:
                token = token_urlsafe(32)
            self._sessions[token] = actor, token_urlsafe(32), now + self._ttl
            return token

    def _active(self, token):
        if not isinstance(token, str):
            return None
        entry = self._sessions.get(token)
        if entry is not None and entry[2] <= self._clock():
            self._sessions.pop(token)
            return None
        return entry

    def resolve(self, token):
        with self._lock:
            entry = self._active(token)
            return deepcopy(entry[0]) if entry is not None else None

    def csrf(self, token):
        with self._lock:
            entry = self._active(token)
            return entry[1] if entry is not None else None

    def revoke(self, token):
        if isinstance(token, str):
            with self._lock:
                self._sessions.pop(token, None)
