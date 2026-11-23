import hashlib
import html
import io
import json
import os
import posixpath
import re
import string
import subprocess
from urllib.parse import unquote


_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)')
_INLINE_CODE = re.compile(r'(?<![\\`])(`+)(?!`).*?(?<!`)\1(?!`)', re.DOTALL)
_LINK_LABEL = r'\[(?:\\.|[^\[\]\\\r\n]|\[[^\[\]\r\n]*\])*\]'
_DESTINATION = (
    r'(?:<(?P<angle>[^<>\r\n]*)>'
    r'|(?P<bare>(?:\\.|[^\s\\()<>]|\((?:\\.|[^\s\\()<>])*\))*))'
)
_INLINE_LINK = re.compile(
    r'(?<!\\)' + _LINK_LABEL + r'\(\s*' + _DESTINATION
    + r'''(?:\s+(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\((?:\\.|[^)\\])*\)))?\s*\)'''
)
_REFERENCE_DEFINITION = re.compile(
    r'^ {0,3}\[(?P<label>(?:\\.|[^\]\\\r\n])+)\]:[ \t]*'
    + _DESTINATION + r'(?=[ \t]|$)[^\r\n]*',
    re.MULTILINE,
)
_REFERENCE_LINK = re.compile(
    r'(?<!\\)(?P<label>' + _LINK_LABEL + r')(?P<reference>' + _LINK_LABEL + r')?'
)
_MARKDOWN_ESCAPE = re.compile(r'\\([' + re.escape(string.punctuation) + r'])')
_URI_SCHEME = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')


def read_git_snapshot(repo, ref, *, git_executable="git"):
    """Read regular files from a resolved commit in a local Git repository."""
    command = [git_executable, "-C", os.fspath(repo)]
    environment = dict(
        os.environ,
        GIT_ALLOW_PROTOCOL="",
        GIT_NO_LAZY_FETCH="1",
        GIT_NO_REPLACE_OBJECTS="1",
    )

    def git(*args, input_data=None):
        return subprocess.run(
            [*command, *args],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=True,
        ).stdout

    commit = git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    commit = commit.strip().decode("ascii")
    paths_by_blob = {}
    for entry in git("ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            mode, kind, blob = metadata.split()
            if kind == b"blob" and mode in (b"100644", b"100755"):
                paths_by_blob.setdefault(blob, []).append(os.fsdecode(path))

    files = {}
    if paths_by_blob:
        objects = io.BytesIO(
            git("cat-file", "--batch", input_data=b"\n".join(paths_by_blob) + b"\n")
        )
        for blob, paths in paths_by_blob.items():
            header = objects.readline().split()
            if len(header) != 3 or header[:2] != [blob, b"blob"]:
                raise ValueError(f"Unexpected Git response for blob {blob.decode('ascii')}")
            size = int(header[2])
            data = objects.read(size)
            if len(data) != size or objects.read(1) != b"\n":
                raise ValueError(f"Incomplete Git blob {blob.decode('ascii')}")
            files.update(dict.fromkeys(paths, data))
    return commit, files


def _markdown_destinations(text):
    lines = []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = _FENCE.match(line)
        if fence is None and marker:
            fence = marker[1]
            lines.append("\n")
        elif fence is None:
            lines.append(line)
        elif marker and marker[1].startswith(fence) and not marker[2].strip():
            fence = None

    source = re.sub(r"<!--.*?(?:-->|\Z)", "", "".join(lines), flags=re.DOTALL)
    source = _INLINE_CODE.sub("", source)
    definitions = {}
    for match in _REFERENCE_DEFINITION.finditer(source):
        label = " ".join(match["label"].split()).casefold()
        definitions.setdefault(label, match["angle"] or match["bare"] or "")
    source = _REFERENCE_DEFINITION.sub("", source)

    for match in _INLINE_LINK.finditer(source):
        yield match["angle"] or match["bare"] or ""
    source = _INLINE_LINK.sub("", source)
    for match in _REFERENCE_LINK.finditer(source):
        reference = match["reference"]
        label = reference if reference and reference != "[]" else match["label"]
        label = " ".join(label[1:-1].split()).casefold()
        if label in definitions:
            yield definitions[label]


def _local_target(doc_id, destination):
    destination = html.unescape(_MARKDOWN_ESCAPE.sub(r"\1", destination))
    if destination.startswith("//") or _URI_SCHEME.match(destination):
        return None
    path = re.split(r"[?#]", destination, maxsplit=1)[0]
    if not path:
        return None
    path = unquote(path, errors="surrogateescape")
    if path.startswith("/"):
        return posixpath.normpath(path.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(doc_id), path))


def plan_git_import(repo, ref, *, project_id, readers_by_path, current, read_snapshot=None):
    """Plan Markdown revisions using the snapshot and the operator's current state."""
    if read_snapshot is None:
        read_snapshot = read_git_snapshot
    commit, files = read_snapshot(repo, ref)

    available_paths = set(files) | {"."}
    for path in files:
        directory = posixpath.dirname(path)
        while directory and directory not in available_paths:
            available_paths.add(directory)
            directory = posixpath.dirname(directory)

    inventory = {}
    changes = []
    missing_links = []
    for doc_id in sorted(files):
        if not doc_id.startswith(("active/", "archive/")):
            continue
        if posixpath.splitext(doc_id)[1].lower() not in (".md", ".markdown"):
            continue

        data = files[doc_id]
        text = data.decode("utf-8")
        readers = list(readers_by_path[doc_id])
        inventory[doc_id] = {"sha256": hashlib.sha256(data).hexdigest(), "readers": readers}
        for destination in sorted(set(_markdown_destinations(text))):
            target = _local_target(doc_id, destination)
            if target is not None and target not in available_paths:
                missing_links.append({"doc_id": doc_id, "target": destination})

        previous = current.get(doc_id)
        if previous is not None and previous["text"] == text and set(previous["readers"]) == set(readers):
            continue
        changes.append({
            "doc_id": doc_id,
            "expected_version": 0 if previous is None else previous["version"],
            "text": text,
            "readers": readers,
        })

    plan = {
        "project_id": project_id,
        "commit": commit,
        "inventory": inventory,
        "changes": changes,
        "missing_links": missing_links,
    }
    payload = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    plan["change_id"] = hashlib.sha256(payload).hexdigest()
    return plan


def apply_git_import(plan, changesets, *, actor, dry_run=False):
    """Apply the planned revisions as one changeset, or return a preview count."""
    changes = plan["changes"]
    if dry_run:
        return {"outcome": "dry_run", "planned_changes": len(changes)}
    if plan["missing_links"]:
        raise ValueError(f"Git import has missing links: {plan['missing_links']!r}")
    if not changes:
        return {"outcome": "unchanged"}
    return changesets.apply(plan["change_id"], plan["project_id"], actor, changes)
