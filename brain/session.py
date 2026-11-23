from copy import deepcopy
from secrets import token_urlsafe
from threading import Lock


class SessionRegistry:
    def __init__(self, authenticate):
        self._authenticate = authenticate
        self._sessions = {}
        self._lock = Lock()

    def open(self, credential):
        actor = self._authenticate(credential)
        if actor is None:
            return None

        actor = deepcopy(actor)
        with self._lock:
            token = token_urlsafe(32)
            while token in self._sessions:
                token = token_urlsafe(32)
            self._sessions[token] = actor
        return token

    def resolve(self, token):
        if not isinstance(token, str):
            return None
        with self._lock:
            return deepcopy(self._sessions.get(token))

    def revoke(self, token):
        if isinstance(token, str):
            with self._lock:
                self._sessions.pop(token, None)
