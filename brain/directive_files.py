"""Read the configured scope declarations beneath a trusted deployment root."""

import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path

LEVELS = ("root", "organization", "project", "workflow")
_MAX_BYTES = 64 * 1024
_SCOPE = re.compile(r"^[ \t]*(?:[-*][ \t]+)?scope[ \t]*:[ \t]*(\S+)[ \t]*$", re.I | re.M)


class DirectiveFiles:
    def __init__(self, root, paths):
        self.root = Path(root)
        self.paths = {}
        if not isinstance(paths, dict):
            raise ValueError("directive_files must map levels to Markdown paths")
        for level, value in paths.items():
            if level not in LEVELS or not isinstance(value, str) or not value or "\0" in value:
                raise ValueError("Invalid directive file configuration")
            path = Path(value)
            relative = path.relative_to(self.root) if path.is_absolute() else path
            if relative.suffix.lower() != ".md" or ".." in relative.parts:
                raise ValueError("Directive file must be Markdown within the configured root")
            self.paths[level] = relative.parts

    def scope(self, level):
        if level not in self.paths:
            return None
        with ExitStack() as stack:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(self.root.anchor, directory_flags)
            stack.callback(os.close, directory)
            for part in (*self.root.parts[1:], *self.paths[level][:-1]):
                directory = os.open(part, directory_flags, dir_fd=directory)
                stack.callback(os.close, directory)

            def open_relative(name, flags):
                return os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)

            with open(self.paths[level][-1], "rb", opener=open_relative) as source:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_BYTES:
                    raise ValueError("Directive file must be a bounded regular file")
                data = source.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise ValueError("Directive file is too large")
        scopes = _SCOPE.findall(data.decode("utf-8"))
        if len(scopes) != 1:
            raise ValueError("Directive file must declare exactly one Scope")
        return scopes[0]
