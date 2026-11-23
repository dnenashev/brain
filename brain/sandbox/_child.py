"""Sandbox child. Read source on stdin and exchange JSON on inherited pipes."""

from __future__ import annotations

import json
import os
import sys
from threading import Lock


FRAME_BYTES = 65536


class FrameTooLarge(ValueError):
    pass


def encode_frame(message):
    frame = json.dumps(message, allow_nan=False, separators=(",", ":")).encode("ascii")
    if len(frame) >= FRAME_BYTES:
        raise FrameTooLarge
    return frame + b"\n"


def _reject_constant(value):
    raise ValueError("invalid JSON constant")


def decode_frame(frame):
    if len(frame) > FRAME_BYTES or not frame.endswith(b"\n"):
        raise ValueError("invalid frame")
    return json.loads(frame.decode("utf-8"), parse_constant=_reject_constant)


def _run(replies, messages):
    exchange = Lock()

    def send(message):
        messages.write(encode_frame(message))
        messages.flush()

    def sdk_call(operation, **parameters):
        with exchange:
            try:
                send({"operation": operation, "parameters": parameters})
            except FrameTooLarge:
                send({"status": "output_limit"})
                raise PermissionError("output_limit") from None
            except (TypeError, ValueError, RecursionError):
                raise PermissionError("invalid_request") from None
            except OSError:
                raise PermissionError("sdk_error") from None

            try:
                reply = decode_frame(replies.readline(FRAME_BYTES + 1))
            except (OSError, ValueError, RecursionError):
                raise PermissionError("sdk_error") from None
            if type(reply) is dict:
                if reply.get("ok") is True and reply.keys() == {"ok", "result"}:
                    return reply["result"]
                if reply.get("ok") is False and reply.get("error") in (
                    "invalid_request", "permission_denied", "sdk_error"
                ):
                    raise PermissionError(reply["error"])
            raise PermissionError("sdk_error")

    send({"status": "ready"})
    namespace = {"__name__": "__main__", "sdk_call": sdk_call}
    try:
        source = sys.stdin.buffer.read().decode("utf-8")
        exec(compile(source, "<sandbox>", "exec"), namespace)
        completion = {"status": "ok", "result": namespace.get("result")}
    except BaseException:
        completion = {"status": "error"}

    with exchange:
        try:
            send(completion)
        except FrameTooLarge:
            send({"status": "output_limit"})
        except (TypeError, ValueError, RecursionError):
            send({"status": "error"})


if __name__ == "__main__":
    # argv carries the read end for replies and the write end for child messages.
    with os.fdopen(int(sys.argv[1]), "rb") as replies:
        with os.fdopen(int(sys.argv[2]), "wb") as messages:
            _run(replies, messages)
