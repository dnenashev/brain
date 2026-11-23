from __future__ import annotations

import argparse
import importlib
import json
import sys
from typing import NoReturn

from brain.adapters.mcp import CodeMode, serve_stdio
from brain.adapters.swarm_mcp import SwarmReadOnly


class _InvalidArguments(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _InvalidArguments


def _error(status: str, exit_code: int, *, stdio: bool = False) -> int:
    stream = sys.stderr if stdio else sys.stdout
    print(json.dumps({"status": status, "result": None}), file=stream)
    return exit_code


def main(
    argv: list[str] | None = None,
    *,
    profile: CodeMode | SwarmReadOnly | None = None,
) -> int:
    parser = _ArgumentParser(prog="brain", allow_abbrev=False)
    parser.add_argument("--factory", metavar="MODULE:FUNCTION")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start", allow_abbrev=False)
    commands.add_parser("stdio", allow_abbrev=False)
    run = commands.add_parser("run", allow_abbrev=False)
    run.add_argument("input", metavar="SOURCE_OR_QUERY")
    run.add_argument("--section")

    stdio = False
    try:
        args = parser.parse_args(argv)
        stdio = args.command == "stdio"
        if args.factory is not None:
            module, separator, function = args.factory.partition(":")
            if profile is not None or not separator or not module or not function.isidentifier():
                raise _InvalidArguments
            profile = getattr(importlib.import_module(module), function)()

        if profile is None:
            return _error("missing_profile", 2, stdio=stdio)
        if not isinstance(profile, (CodeMode, SwarmReadOnly)):
            return _error("invalid_profile", 2, stdio=stdio)

        if stdio:
            serve_stdio(profile, sys.stdin, sys.stdout)
            return 0
        if args.command == "start":
            result = profile.start()
        elif isinstance(profile, CodeMode):
            if args.section is not None:
                raise _InvalidArguments
            result = profile.run(args.input)
        else:
            result = profile.run(args.input, args.section)
        response = json.dumps(result, allow_nan=False)
    except _InvalidArguments:
        return _error("invalid_arguments", 2, stdio=stdio)
    except Exception:
        return _error("error", 1, stdio=stdio)

    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
