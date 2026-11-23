from __future__ import annotations

import sys
from functools import partial
from typing import Any


_OPERATIONS = frozenset({"read", "search", "check", "draft", "task"})


def dispatch_sdk_request(sdk: Any, session_id: str, request: Any) -> dict[str, Any]:
    """Dispatch one sandbox request using the parent's existing SDK session."""
    if type(request) is not dict or request.keys() != {"operation", "parameters"}:
        return {"ok": False, "error": "invalid_request"}

    operation = request["operation"]
    parameters = request["parameters"]
    if type(operation) is not str or type(parameters) is not dict:
        return {"ok": False, "error": "invalid_request"}
    if any(type(name) is not str or name in {"session_id", "operation"} for name in parameters):
        return {"ok": False, "error": "invalid_request"}
    if operation not in _OPERATIONS:
        return {"ok": False, "error": "permission_denied"}

    try:
        result = sdk.call(session_id, operation, **parameters)
    except PermissionError:
        return {"ok": False, "error": "permission_denied"}
    except Exception:
        return {"ok": False, "error": "sdk_error"}
    return {"ok": True, "result": result}


def run_code(
    source: str,
    *,
    sdk: Any,
    session_id: str,
    timeout_seconds: float = 3,
    memory_bytes: int = 67108864,
    output_bytes: int = 16384,
) -> dict[str, Any]:
    """Execute Python in a supervised macOS sandbox with an SDK broker."""
    if (
        type(source) is not str
        or type(timeout_seconds) not in (int, float)
        or not 0 < timeout_seconds < float("inf")
        or type(memory_bytes) is not int
        or memory_bytes <= 0
        or type(output_bytes) is not int
        or output_bytes < 0
    ):
        return {"status": "error", "result": None, "stdout": "", "stderr": ""}
    if sys.platform != "darwin":
        return {"status": "unavailable", "result": None, "stdout": "", "stderr": ""}

    from brain.sandbox._native import run

    return run(
        source,
        dispatch=partial(dispatch_sdk_request, sdk, session_id),
        timeout_seconds=timeout_seconds,
        memory_bytes=memory_bytes,
        output_bytes=output_bytes,
    )
