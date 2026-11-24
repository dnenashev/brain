"""Bind CLI profiles to an authenticated local deployment."""

import http.client
import ipaddress
import json
import os
import stat
from contextlib import closing
from urllib.parse import urlsplit
from uuid import uuid4

from .adapters.mcp import CodeMode
from .adapters.swarm_mcp import SwarmReadOnly
from .live_workflow import parse_reference
from .sdk.api import VersionedSDK
from .server import valid_trace_id


def _credential():
    def owner_only(path, flags):
        return os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK)

    with open(os.environ["BRAIN_CREDENTIAL_FILE"], encoding="utf-8", opener=owner_only) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("Credential file must be an owner-only regular file")
        credential = source.read(4097).rstrip("\r\n")
    if not credential or len(credential) > 4096:
        raise ValueError("Invalid credential length")
    return credential


class LocalKnowledgeClient:
    def __init__(self):
        address = urlsplit(os.environ["BRAIN_KNOWLEDGE_URL"])
        if (address.scheme != "http" or address.username is not None or address.password is not None
                or not address.hostname or "%" in address.hostname
                or not ipaddress.ip_address(address.hostname).is_loopback
                or address.port is None or address.path not in ("", "/") or address.query or address.fragment):
            raise ValueError("Knowledge URL must use numeric loopback and an explicit port")
        self.host, self.port = address.hostname, address.port
        self.trace = os.environ.get("BRAIN_TRACE_ID", uuid4().hex)
        if not valid_trace_id(self.trace):
            raise ValueError("Invalid trace ID")
        self.project = os.environ["BRAIN_PROJECT_ID"]
        self.token = None
        self.token = self.request("/session", credential=_credential())["token"]
        identity = self.call("/identity")
        if identity["project_id"] != self.project:
            raise PermissionError("Credential belongs to another project")
        self.role = identity["role"]

    def request(self, path, **payload):
        headers = {"Content-Type": "application/json", "X-Trace-Id": self.trace}
        if self.token is not None:
            headers["Authorization"] = "Bearer " + self.token
        with closing(http.client.HTTPConnection(self.host, self.port, timeout=2)) as connection:
            connection.request("POST", path, json.dumps(payload, allow_nan=False), headers)
            response = connection.getresponse()
            if response.status in (401, 403):
                raise PermissionError("Knowledge operation denied")
            if response.status >= 400:
                raise ValueError("Knowledge operation failed")
            if response.getheader("X-Trace-Id") != self.trace:
                raise ValueError("Knowledge trace did not match")
            return json.load(response)

    def call(self, path, **payload):
        return self.request(path, project_id=self.project, **payload)


def build_code_mode():
    client = LocalKnowledgeClient()
    sdk = VersionedSDK()
    sdk.register("knowledge-http-v1", {
        "read": lambda doc_id, version=None: client.call("/read", doc_id=doc_id, version=version),
        "search": lambda query: client.call("/search", query=query)["results"],
        "check": lambda deliveries: client.call("/check", deliveries=deliveries),
        "draft": lambda draft_id, change: client.call("/drafts", draft_id=draft_id, change=change),
        "task": lambda task_id, candidate_sha, draft_id, revision, handoff: client.call(
            "/reviews", task_id=task_id, candidate_sha=candidate_sha,
            draft_id=draft_id, revision=revision, handoff=handoff,
        ),
    })
    sdk.open_session(client.token, client.role)

    def resolve_reference(session_id, reference):
        parsed = parse_reference(reference)
        if parsed is None:
            return False
        return bool(sdk.call(session_id, "read", doc_id=parsed[0], version=parsed[1]))

    return CodeMode(sdk, session_id=client.token, resolve_reference=resolve_reference)


def build_swarm_readonly():
    client = LocalKnowledgeClient()

    def search(query, section):
        if section not in (None, client.project):
            raise PermissionError("Section belongs to another project")
        return client.call("/search", query=query)["results"]

    return SwarmReadOnly(search)
