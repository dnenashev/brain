import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from uuid import uuid4


_AREAS = ("active", "archive", "shared", "inbox", "workspaces")


def _validate_area(area: str) -> None:
    if not isinstance(area, str) or area not in _AREAS:
        raise ValueError(f"unknown storage area: {area!r}")


def _validate_filename(filename: str) -> None:
    if (
        not isinstance(filename, str)
        or "\x00" in filename
        or PureWindowsPath(filename).name != filename
        or Path(filename).suffix != ".md"
    ):
        raise ValueError("expected a single filename with the .md extension")


@contextmanager
def _directory(
    path: str | Path, *, parent_fd: int | None = None, create: bool = False
) -> Iterator[int]:
    if create:
        if parent_fd is None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(path, dir_fd=parent_fd)
        except FileExistsError:
            pass

    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(f"storage directory must be a real directory: {path}") from error
        raise
    try:
        yield fd
    finally:
        os.close(fd)


def _read_bytes(directory_fd: int, filename: str) -> bytes:
    try:
        fd = os.open(
            filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
        )
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError(f"Markdown file must be a regular file: {filename}") from error
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"Markdown file must be a regular file: {filename}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(fd)


def _publish_bytes(directory_fd: int, filename: str, data: bytes) -> bool:
    temporary = f".import-{uuid4().hex}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        # Publish the complete file atomically without overwriting a concurrent import.
        try:
            os.link(
                temporary,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(temporary, dir_fd=directory_fd)


class KnowledgeStore:
    """A directory-backed store for UTF-8 Markdown knowledge files."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def ensure_layout(self) -> dict[str, Path]:
        areas = {}
        with _directory(self.root, create=True) as root_fd:
            for area in _AREAS:
                with _directory(area, parent_fd=root_fd, create=True):
                    areas[area] = self.root / area
        return areas

    def import_markdown(self, source: str | Path, area: str) -> Path:
        _validate_area(area)
        source_path = Path(source)
        filename = source_path.name
        _validate_filename(filename)
        data = source_path.read_bytes()
        data.decode("utf-8")

        destination = self.root / area / filename
        with self._area_directory(area, create=True) as directory_fd:
            try:
                existing = _read_bytes(directory_fd, filename)
            except FileNotFoundError:
                if _publish_bytes(directory_fd, filename, data):
                    return destination
                existing = _read_bytes(directory_fd, filename)
            if existing != data:
                raise ValueError(f"Markdown content conflicts with {destination}")
        return destination

    def read_markdown(self, area: str, filename: str) -> str:
        _validate_area(area)
        _validate_filename(filename)
        with self._area_directory(area) as directory_fd:
            return _read_bytes(directory_fd, filename).decode("utf-8")

    @contextmanager
    def _area_directory(self, area: str, *, create: bool = False) -> Iterator[int]:
        with _directory(self.root, create=create) as root_fd:
            with _directory(area, parent_fd=root_fd, create=create) as directory_fd:
                yield directory_fd
