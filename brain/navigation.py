import os
import re
from collections.abc import Iterator
from contextlib import contextmanager

from .git_store import (
    KnowledgeStore,
    _directory,
    _read_bytes,
    _validate_area,
    _validate_filename,
)


_DOCUMENT_REF = re.compile(r"doc:(.{1,128})@([1-9][0-9]{0,17})")


def parse_document_reference(ref):
    """Parse a versioned document link. The caller supplies its authorized project."""
    match = _DOCUMENT_REF.fullmatch(ref) if isinstance(ref, str) else None
    return (match[1], int(match[2])) if match is not None else None


def document_reference(doc_id, version):
    return f"doc:{doc_id}@{version}"


@contextmanager
def _area_directory(store: KnowledgeStore, area: str) -> Iterator[int]:
    _validate_area(area)
    with _directory(store.root) as root_fd:
        with _directory(area, parent_fd=root_fd) as area_fd:
            yield area_fd


def _markdown_names(area_fd: int) -> list[str]:
    names = []
    with os.scandir(area_fd) as entries:
        for entry in entries:
            try:
                _validate_filename(entry.name)
            except ValueError:
                continue
            if entry.is_file(follow_symlinks=False):
                names.append(entry.name)
    return sorted(names)


def tree(store: KnowledgeStore, *, area: str = "active") -> list[str]:
    """List Markdown filenames relative to the selected storage area."""
    try:
        with _area_directory(store, area) as area_fd:
            return _markdown_names(area_fd)
    except FileNotFoundError:
        return []


def search(
    store: KnowledgeStore, text: str, *, area: str = "active"
) -> list[dict[str, str | int]]:
    """Find matching lines in filename and line number order."""
    query = text.casefold()
    matches = []
    try:
        with _area_directory(store, area) as area_fd:
            for path in _markdown_names(area_fd):
                try:
                    content = _read_bytes(area_fd, path).decode("utf-8")
                except FileNotFoundError:
                    continue
                for number, line in enumerate(content.splitlines(), start=1):
                    if query in line.casefold():
                        matches.append({"path": path, "line": number, "text": line})
    except FileNotFoundError:
        return []
    return matches


def read_lines(
    store: KnowledgeStore, path: str, start: int, end: int, *, area: str = "active"
) -> str:
    """Read an inclusive range of one-based line numbers."""
    _validate_filename(path)
    if type(start) is not int or type(end) is not int or not 1 <= start <= end:
        raise ValueError("expected an ordered range of positive line numbers")

    with _area_directory(store, area) as area_fd:
        lines = _read_bytes(area_fd, path).decode("utf-8").splitlines()
    if end > len(lines):
        raise ValueError("line range exceeds the file length")
    return "\n".join(lines[start - 1:end])
