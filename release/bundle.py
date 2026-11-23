"""Build, verify, and install a Company Brain release archive."""

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import unicodedata
import zipfile
import zlib
from pathlib import Path


_ROOTS = ("brain", "deploy", "docs", "checks", "specs", "release")
_REVISION = re.compile(r"[0-9a-fA-F]{40}")
_PRIVATE_NAMES = {".git", ".envrc", ".netrc", ".npmrc", ".pypirc", ".ssh", ".aws",
                  "credentials", "data", "logs", "log", "secrets"}
_PRIVATE_SUFFIXES = {".db", ".key", ".log", ".p12", ".pem", ".pfx", ".sqlite", ".sqlite3"}


def _git(repo, *args, stdout=subprocess.PIPE):
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "--literal-pathspecs", "-C", str(repo), *args],
            check=True, stdout=stdout, stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"git {args[0]} failed") from exc


def _private(part):
    name = part.casefold()
    return (name in _PRIVATE_NAMES or name == ".env"
            or name.startswith((".env.", "credentials.", "secrets."))
            or Path(name).suffix in _PRIVATE_SUFFIXES or name.endswith(".log.gz"))


def _path_parts(name, directory):
    parts = tuple((name[:-1] if directory else name).split("/"))
    if (parts[0] not in _ROOTS or (len(parts) == 1 and not directory)
            or any(part in ("", ".", "..") or part.endswith((".", " "))
                   or _private(part) for part in parts)
            or any(char in "\\:" or ord(char) < 32 or ord(char) == 127 for char in name)):
        raise ValueError(f"invalid release path: {name!r}")
    return parts


def _sha256(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _file_hashes(archive):
    members = set()
    paths = {}
    hashes = {}
    for entry in archive.infolist():
        name = entry.orig_filename
        directory = name.endswith("/")
        parts = _path_parts(name, directory)
        kind = stat.S_IFMT(entry.external_attr >> 16)
        expected_kind = stat.S_IFDIR if directory else stat.S_IFREG
        if (kind not in (0, expected_kind) or (not directory and entry.external_attr & 0x10)
                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise ValueError(f"unsupported release entry: {name!r}")
        if parts in members:
            raise ValueError(f"duplicate release entry: {name!r}")
        members.add(parts)
        key = tuple(unicodedata.normalize("NFC", part.casefold()) for part in parts)
        for depth in range(1, len(parts) + 1):
            path = (parts[:depth], directory or depth < len(parts))
            if paths.setdefault(key[:depth], path) != path:
                raise ValueError(f"conflicting release path: {name!r}")
        with archive.open(entry) as stream:
            if directory:
                if entry.file_size or stream.read(1):
                    raise ValueError(f"release directory contains data: {name!r}")
            else:
                hashes[name] = _sha256(stream)
    return hashes


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate manifest key")
    return result


def _verify(stream, manifest):
    try:
        with Path(manifest).open(encoding="utf-8") as source:
            recorded = json.load(source, object_pairs_hook=_unique_object)
        if not isinstance(recorded, dict):
            raise ValueError("invalid release manifest")
        revision = recorded.get("revision")
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ValueError("invalid manifest revision")
        if _sha256(stream) != recorded.get("archive_sha256"):
            raise ValueError("archive SHA-256 mismatch")
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            if _file_hashes(archive) != recorded.get("files"):
                raise ValueError("release file names or SHA-256 checksums do not match")
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, struct.error, zlib.error) as exc:
        raise ValueError("cannot verify release bundle") from exc
    return recorded


def build_bundle(repo, revision, output_dir):
    """Export the release files from a full commit SHA and return both paths."""
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise ValueError("revision must be a full 40-character commit SHA")
    revision = revision.lower()
    if _git(repo, "cat-file", "-t", revision).strip() != b"commit":
        raise ValueError("revision does not name a commit")

    tree = _git(repo, "ls-tree", "-r", "-z", "--full-tree", revision, "--", *_ROOTS)
    names = []
    for record in tree.split(b"\0")[:-1]:
        metadata, name = record.split(b"\t", 1)
        name = name.decode("utf-8")
        if any(_private(part) for part in name.split("/")):
            continue
        _path_parts(name, directory=False)
        if metadata.split(b" ", 1)[0] not in (b"100644", b"100755"):
            raise ValueError(f"release path is not a regular Git file: {name!r}")
        names.append(name)
    if not names:
        raise ValueError("commit contains no release files")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f"company-brain-{revision}.zip"
    manifest_path = output / "manifest.json"
    with tempfile.TemporaryDirectory(prefix=".bundle-", dir=output) as temporary:
        staged_archive = Path(temporary) / archive_path.name
        staged_manifest = Path(temporary) / manifest_path.name
        with staged_archive.open("wb") as stream:
            _git(repo, "archive", "--format=zip", revision, "--", *names, stdout=stream)
        with staged_archive.open("rb") as stream:
            archive_hash = _sha256(stream)
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                files = _file_hashes(archive)
        if set(files) != set(names):
            raise ValueError("git archive omitted or added release files")
        recorded = {"revision": revision, "archive_sha256": archive_hash, "files": files}
        staged_manifest.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n",
                                   encoding="utf-8")
        staged_archive.replace(archive_path)
        staged_manifest.replace(manifest_path)
    return archive_path, manifest_path


def verify_bundle(archive, manifest):
    """Return the manifest after checking the archive and each file."""
    try:
        with Path(archive).open("rb") as stream:
            return _verify(stream, manifest)
    except OSError as exc:
        raise ValueError("cannot read release archive") from exc


def materialize(archive, manifest, target):
    """Verify a private copy and publish the release with a directory rename."""
    target = Path(target)
    with tempfile.TemporaryFile() as snapshot:
        try:
            with Path(archive).open("rb") as source:
                shutil.copyfileobj(source, snapshot)
        except OSError as exc:
            raise ValueError("cannot read release archive") from exc
        snapshot.seek(0)
        _verify(snapshot, manifest)

        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".release-", dir=target.parent) as temporary:
            stage = Path(temporary) / "root"
            stage.mkdir()
            with zipfile.ZipFile(snapshot) as verified:
                for entry in verified.infolist():
                    destination = stage / entry.filename
                    if entry.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with verified.open(entry) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    executable = (entry.external_attr >> 16) & 0o111
                    if executable:
                        destination.chmod(stat.S_IMODE(destination.stat().st_mode) | executable)
            try:
                os.rename(stage, target)
            except OSError as exc:
                if exc.errno in (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR, errno.EISDIR):
                    raise ValueError("release target must be absent or an empty directory") from exc
                raise
    return target
