from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from brain.sandbox.runner import run_code


MAX_MESSAGE_BYTES = 64 * 1024
_PROTOCOL_VERSION = "2024-11-05"


class CodeMode:
    """Expose the SDK session supplied by the trusted host."""

    def __init__(
        self,
        sdk: Any,
        *,
        session_id: str,
        resolve_reference: Callable[[str, str], Any],
        executor: Callable[..., dict[str, Any]] = run_code,
    ) -> None:
        self._sdk = sdk
        self._session_id = session_id
        self._resolve_reference = resolve_reference
        self._executor = executor

    def start(self) -> dict[str, Any]:
        description = self._sdk.describe(self._session_id)
        return {
            "profile": "code",
            "revision": description["revision"],
            "operations": description["operations"],
        }

    def run(self, source: str) -> dict[str, Any]:
        revision = self._sdk.describe(self._session_id)["revision"]
        if type(source) is not str:
            return {"status": "invalid_arguments", "revision": revision, "result": None}

        try:
            execution = self._executor(source, sdk=self._sdk, session_id=self._session_id)
            status = execution["status"]
            if type(status) is not str:
                raise ValueError("invalid executor status")
        except Exception:
            return {"status": "error", "revision": revision, "result": None}

        result = None
        if status == "ok":
            result = execution.get("result")
            if not self._validate_result(result):
                return {"status": "invalid_result", "revision": revision, "result": None}

        response = {"status": status, "revision": revision, "result": result}
        for stream in ("stdout", "stderr"):
            output = execution.get(stream)
            if type(output) is str:
                response[stream] = output
        return response

    def _validate_result(self, result: Any) -> bool:
        if type(result) is not dict or result.keys() != {"value", "grounding_refs"}:
            return False
        references = result["grounding_refs"]
        if type(references) is not list or any(
            type(reference) is not str or not reference.strip() for reference in references
        ):
            return False
        if len(set(references)) != len(references):
            return False
        try:
            return _is_json_value(result["value"]) and all(
                self._resolve_reference(self._session_id, reference) for reference in references
            )
        except Exception:
            return False


def _is_json_value(value: Any) -> bool:
    kind = type(value)
    if value is None or kind in (str, int, bool):
        return True
    if kind is float:
        return math.isfinite(value)
    if kind is list:
        return all(_is_json_value(item) for item in value)
    if kind is dict:
        return all(type(key) is str and _is_json_value(item) for key, item in value.items())
    return False


def _tool_specs(profile: Any) -> list[dict[str, Any]]:
    if isinstance(profile, CodeMode):
        required = "source"
        properties = {"source": {"type": "string"}}
        description = "Run Python using the bound SDK session."
    else:
        from brain.adapters.swarm_mcp import SwarmReadOnly

        if not isinstance(profile, SwarmReadOnly):
            raise TypeError("unsupported profile")
        required = "query"
        properties = {"query": {"type": "string"}, "section": {"type": ["string", "null"]}}
        description = "Search memory within the configured read-only scope."

    return [
        {
            "name": "start",
            "description": "Return the capabilities of the active profile.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "run",
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": [required],
                "additionalProperties": False,
            },
        },
    ]


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _call_tool(profile: Any, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    if params.keys() - {"name", "arguments", "_meta"} or type(arguments) is not dict:
        return _error(request_id, -32602, "Invalid tool parameters")

    if name == "start" and not arguments:
        result = profile.start()
    elif name == "run" and isinstance(profile, CodeMode):
        if arguments.keys() != {"source"} or type(arguments["source"]) is not str:
            return _error(request_id, -32602, "Invalid tool arguments")
        result = profile.run(arguments["source"])
    elif name == "run":
        query = arguments.get("query")
        section = arguments.get("section")
        if (
            arguments.keys() - {"query", "section"}
            or type(query) is not str
            or (section is not None and type(section) is not str)
        ):
            return _error(request_id, -32602, "Invalid tool arguments")
        result = profile.run(query, section)
    else:
        return _error(request_id, -32602, "Invalid tool call")

    structured = result if type(result) is dict else {"result": result}
    return _success(request_id, {"content": [], "structuredContent": structured})


def _handle_request(
    profile: Any, tools: list[dict[str, Any]], request: Any
) -> dict[str, Any] | None:
    if type(request) is not dict:
        return _error(None, -32600, "Invalid Request")
    request_id = request.get("id")
    if request_id is not None and (
        type(request_id) not in (str, int, float)
        or (type(request_id) is float and not math.isfinite(request_id))
    ):
        return _error(None, -32600, "Invalid Request")
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or type(method) is not str:
        return _error(None, -32600, "Invalid Request")
    if "id" not in request:
        return None
    params = request.get("params", {})
    if type(params) is not dict:
        return _error(request_id, -32602, "Invalid params")
    if method == "initialize":
        return _success(request_id, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "company-brain", "version": "1"},
        })
    if method == "tools/list":
        return _success(request_id, {"tools": tools})
    if method == "tools/call":
        try:
            return _call_tool(profile, request_id, params)
        except Exception:
            return _error(request_id, -32603, "Tool execution failed")
    return _error(request_id, -32601, "Method not found")


def _reject_constant(value: str) -> None:
    raise ValueError("invalid JSON constant")


def _encode_line(value: Any) -> bytes:
    return (json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")


def _send_response(writer: Any, response: dict[str, Any]) -> None:
    request_id = response["id"]
    try:
        line = _encode_line(response)
    except (TypeError, ValueError, RecursionError):
        line = _encode_line(_error(request_id, -32603, "Invalid tool result"))
    if len(line) > MAX_MESSAGE_BYTES:
        line = _encode_line(_error(request_id, -32000, "Response too large"))
        if len(line) > MAX_MESSAGE_BYTES:
            line = _encode_line(_error(None, -32000, "Response too large"))
    try:
        writer.write(line.decode("ascii"))
    except TypeError:
        writer.write(line)
    writer.flush()


def serve_stdio(profile: Any, reader: Any, writer: Any) -> None:
    """Serve newline-delimited MCP requests on text or binary streams."""
    tools = _tool_specs(profile)
    read_size = MAX_MESSAGE_BYTES + 1
    while line := reader.readline(read_size):
        if isinstance(line, str):
            line = line.encode("utf-8", errors="surrogatepass")
        if len(line) > MAX_MESSAGE_BYTES:
            while line and not line.endswith(b"\n"):
                line = reader.readline(read_size)
                if isinstance(line, str):
                    line = line.encode("utf-8", errors="surrogatepass")
            _send_response(writer, _error(None, -32000, "Request too large"))
            continue
        try:
            request = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            _send_response(writer, _error(None, -32700, "Parse error"))
            continue
        response = _handle_request(profile, tools, request)
        if response is not None:
            _send_response(writer, response)
