"""Back up and restore the persistent state of an isolated Company Brain profile."""

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

_DATABASES = ("knowledge.db", "audit.db")
_ROOT_FILES = (*_DATABASES, "review.json")
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
_MANIFEST = "manifest.json"


def _file_type(path):
    try:
        return stat.S_IFMT(path.lstat().st_mode)
    except FileNotFoundError:
        return 0


def _selected_files(source):
    files = {
        name: source / name for name in _ROOT_FILES
        if _file_type(source / name) == stat.S_IFREG
    }
    attachments = source / "attachments"
    pending = [attachments] if _file_type(attachments) == stat.S_IFDIR else []
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files[path.relative_to(source).as_posix()] = path
    return files


def _allowed_name(name):
    if name in _ROOT_FILES:
        return True
    parts = name.split("/")
    return (
        len(parts) > 1
        and parts[0] == "attachments"
        and not any(char in name for char in "\\:\x00")
        and all(part not in ("", ".", "..") for part in parts)
        and (
            os.name != "nt"
            or all(
                not part.endswith((".", " ")) and not PureWindowsPath(part).is_reserved()
                for part in parts
            )
        )
    )


def _hash_stream(source, destination=None):
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return digest.hexdigest()


def _snapshot_database(source, destination):
    for suffix in _SQLITE_SIDECARS:
        sidecar = source.with_name(source.name + suffix)
        if _file_type(sidecar) not in (0, stat.S_IFREG):
            raise ValueError(f"Invalid SQLite sidecar {sidecar.name}")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro")) as database:
        with closing(sqlite3.connect(destination)) as snapshot:
            database.backup(snapshot)


def backup(source_dir, archive_path):
    """Publish a ZIP snapshot. The operator must pause writes across the profile."""
    source = Path(source_dir).resolve()
    if not source.is_dir():
        raise NotADirectoryError(source)
    destination = Path(archive_path).absolute()
    destination = destination.parent.resolve() / destination.name
    selected = _selected_files(source)
    sidecars = {source / (name + suffix) for name in _DATABASES for suffix in _SQLITE_SIDECARS}
    if destination in selected.values() or destination in sidecars:
        raise ValueError("Archive path overlaps profile state")

    destination.parent.mkdir(parents=True, exist_ok=True)
    files = {}
    with TemporaryDirectory(prefix=".backup-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        staged_archive = stage / "backup.zip"
        staged_archive.touch(mode=0o600, exist_ok=False)
        with ZipFile(staged_archive, "w", compression=ZIP_DEFLATED) as archive:
            for name, path in sorted(selected.items()):
                if not _allowed_name(name):
                    raise ValueError(f"Invalid archive path {name!r}")
                if name in _DATABASES:
                    snapshot = stage / name
                    _snapshot_database(path, snapshot)
                    path = snapshot
                with path.open("rb") as original, archive.open(name, "w", force_zip64=True) as member:
                    files[name] = _hash_stream(original, member)
            archive.writestr(_MANIFEST, json.dumps({"files": files}, sort_keys=True))
        with staged_archive.open("rb") as stream:
            archive_sha256 = _hash_stream(stream)
        os.replace(staged_archive, destination)
    return {"archive_sha256": archive_sha256, "files": files}


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate manifest key {key!r}")
        result[key] = value
    return result


def _read_manifest(archive):
    members = {}
    for member in archive.infolist():
        name = member.orig_filename
        mode = stat.S_IFMT(member.external_attr >> 16)
        if (
            name in members
            or (name != _MANIFEST and not _allowed_name(name))
            or mode not in (0, stat.S_IFREG)
            or member.external_attr & 0x10
        ):
            raise ValueError(f"Invalid ZIP member {name!r}")
        members[name] = member

    manifest_member = members.pop(_MANIFEST, None)
    if manifest_member is None:
        raise ValueError("ZIP has no manifest.json")
    with archive.open(manifest_member) as stream:
        manifest = json.load(stream, object_pairs_hook=_unique_fields)
    if not isinstance(manifest, dict) or manifest.keys() != {"files"}:
        raise ValueError("Invalid manifest structure")
    files = manifest["files"]
    if not isinstance(files, dict) or files.keys() != members.keys():
        raise ValueError("ZIP members do not match the manifest")
    if any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
           for digest in files.values()):
        raise ValueError("Invalid SHA-256 digest in manifest")
    return files, members


def _check_database(path):
    uri = path.as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri)) as database:
        if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError(f"SQLite integrity check failed for {path.name}")


def restore(archive_path, target_dir):
    """Validate a snapshot and publish it to a separate, empty or absent target."""
    started = time.monotonic()
    target = Path(target_dir).absolute()
    target = target.parent.resolve() / target.name
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise ValueError("Restore target must be an empty directory or absent")

    # Publish missing ancestors in the same rename when the target is nested.
    destination = target
    while not destination.parent.exists():
        destination = destination.parent
    with TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "profile"
        stage.mkdir(mode=0o700)
        payload = stage / target.relative_to(destination)
        payload.mkdir(parents=True, exist_ok=True)
        with ZipFile(archive_path) as archive:
            files, members = _read_manifest(archive)
            for name, expected in files.items():
                path = payload / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(members[name]) as member, path.open("xb") as output:
                    actual = _hash_stream(member, output)
                if actual != expected:
                    raise ValueError(f"SHA-256 mismatch for {name!r}")
        for name in _DATABASES:
            if name in files:
                _check_database(payload / name)
        os.replace(stage, destination)
    return {"files": files, "rto_seconds": time.monotonic() - started}
