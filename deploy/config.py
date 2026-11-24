import hashlib
import hmac
import ipaddress
import json
import os
import re
import stat
from pathlib import Path

from brain.live_workflow import DEFAULT_LIMITS


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"bind", "http_port", "review_port", "data_dir", "credential_file"}
    if not isinstance(config, dict) or not required <= config.keys():
        raise ValueError("missing deployment config fields")
    bind = config["bind"]
    if not isinstance(bind, str) or "%" in bind or not ipaddress.ip_address(bind).is_loopback:
        raise ValueError("bind must be a numeric loopback address")
    for key in ("http_port", "review_port"):
        if type(config[key]) is not int or not 0 <= config[key] <= 65535:
            raise ValueError(f"{key} must be an integer from 0 to 65535")
    for key in ("data_dir", "credential_file", "review_seed"):
        value = config.get(key)
        if key == "review_seed" and value is None:
            config[key] = None
            continue
        if not isinstance(value, str) or not value or "\0" in value:
            raise ValueError(f"{key} must be a nonempty path")
        target = path.parent / value
        config[key] = target.parent.resolve() / target.name if key == "credential_file" else target.resolve()
    limits = config.get("limits", {})
    if not isinstance(limits, dict) or any(
        key not in DEFAULT_LIMITS or type(value) is not int or not 0 < value <= DEFAULT_LIMITS[key]
        for key, value in limits.items()
    ):
        raise ValueError("invalid knowledge quotas")
    directives = config.get("directives", {})
    if not isinstance(directives, dict) or any(
        not isinstance(levels, dict) or any(
            level not in {"organization", "project", "workflow"}
            or not isinstance(scope, str) or not scope
            for level, scope in levels.items()
        )
        for levels in directives.values()
    ):
        raise ValueError("invalid deployment directive scope")
    load_credentials(config["credential_file"])
    return config


def _credential_opener(path, flags):
    return os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK)


def load_credentials(path):
    try:
        with open(path, encoding="utf-8", opener=_credential_opener) as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError
            entries = json.load(source)
    except (OSError, ValueError):
        raise ValueError("credential_file must be an owner-only regular JSON file with mode 0600") from None
    if not isinstance(entries, list):
        raise ValueError("credential_file must contain an array")
    records = []
    fields = ("actor_id", "project_id", "role")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid credential record")
        digest = entry.get("credential_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("invalid credential_sha256")
        if any(not isinstance(entry.get(key), str) or not entry[key].strip() for key in fields):
            raise ValueError("invalid credential identity")
        records.append((bytes.fromhex(digest), {key: entry[key] for key in fields}))
    return records


def authenticate(credential, records):
    if not isinstance(credential, str):
        return None
    try:
        digest = hashlib.sha256(credential.encode("utf-8")).digest()
    except UnicodeEncodeError:
        return None
    actor = None
    for expected, identity in records:
        if hmac.compare_digest(digest, expected) and actor is None:
            actor = {**identity, "verified": True}
    return actor
