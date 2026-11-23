"""Supervise a Seatbelt child and broker its bounded JSON protocol."""

from __future__ import annotations

import ctypes
import os
import queue
import selectors
import signal
import subprocess
from contextlib import ExitStack
from threading import Thread
from time import monotonic

from brain.sandbox._child import FRAME_BYTES, FrameTooLarge, decode_frame, encode_frame
from brain.sandbox._seatbelt import sandbox_command


_POLL_SECONDS = 0.01
_PROTOCOL_BYTES = 16 * FRAME_BYTES
_PROC_PIDTASKINFO = 4


class _TaskInfo(ctypes.Structure):
    # struct proc_taskinfo in Darwin's <sys/proc_info.h>.
    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("cpu_times", ctypes.c_uint64 * 4),
        ("counters", ctypes.c_int32 * 12),
    ]


class _Stop(Exception):
    """Stop the child and return the given run status."""


def _rss_reader():
    proc_pidinfo = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidinfo
    proc_pidinfo.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int
    ]
    proc_pidinfo.restype = ctypes.c_int
    info = _TaskInfo()
    size = ctypes.sizeof(info)

    def resident_bytes(pid):
        if proc_pidinfo(pid, _PROC_PIDTASKINFO, 0, ctypes.byref(info), size) == size:
            return info.resident_size
        return None

    return resident_bytes


def _reap(process, options):
    pid, status, usage = os.wait4(process.pid, options)
    if pid:
        process.returncode = os.waitstatus_to_exitcode(status)
        # Darwin reports ru_maxrss in bytes.
        return usage.ru_maxrss
    return None


def _stop_process(process):
    if process.returncode is not None:
        return None
    try:
        os.kill(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return _reap(process, 0)


def _pipe(stack):
    read_fd, write_fd = os.pipe()
    reader = stack.enter_context(os.fdopen(read_fd, "rb", buffering=0))
    writer = stack.enter_context(os.fdopen(write_fd, "wb", buffering=0))
    return reader, writer


def _dispatch_reply(dispatch, request, replies):
    try:
        frame = encode_frame(dispatch(request))
    except FrameTooLarge:
        frame = None
    except BaseException:
        frame = encode_frame({"ok": False, "error": "sdk_error"})
    replies.put(frame)


class _Protocol:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.buffer = bytearray()
        self.used_bytes = 0
        self.ready = False
        self.completion = None
        self.pending = None

    def _charge(self, size):
        self.used_bytes += size
        if self.used_bytes > _PROTOCOL_BYTES:
            raise _Stop("output_limit")

    def receive(self, data):
        self._charge(len(data))
        self.buffer.extend(data)
        while True:
            newline = self.buffer.find(b"\n", 0, FRAME_BYTES)
            if newline < 0:
                if len(self.buffer) >= FRAME_BYTES:
                    raise _Stop("output_limit")
                return
            try:
                message = decode_frame(bytes(self.buffer[:newline + 1]))
            except (ValueError, RecursionError):
                raise _Stop("error" if self.ready else "unavailable") from None
            del self.buffer[:newline + 1]
            self._receive_message(message)

    def _receive_message(self, message):
        if not self.ready:
            if message != {"status": "ready"}:
                raise _Stop("unavailable")
            self.ready = True
            return
        if self.completion is not None or self.pending is not None:
            raise _Stop("error")
        if message == {"status": "output_limit"}:
            raise _Stop("output_limit")
        if message == {"status": "error"}:
            self.completion = ("error", None)
        elif (
            type(message) is dict
            and message.keys() == {"status", "result"}
            and message["status"] == "ok"
        ):
            self.completion = ("ok", message["result"])
        else:
            self.pending = queue.SimpleQueue()
            # An SDK handler can block. Keep it off the process supervisor.
            Thread(
                target=_dispatch_reply,
                args=(self.dispatch, message, self.pending),
                daemon=True,
            ).start()

    def take_reply(self):
        if self.pending is None:
            return None
        try:
            frame = self.pending.get_nowait()
        except queue.Empty:
            return None
        if frame is None:
            raise _Stop("output_limit")
        self._charge(len(frame))
        return memoryview(frame)


def _supervise(process, source, replies, messages, protocol, captured,
               deadline, resident_bytes, memory_bytes, output_bytes):
    readers = {process.stdout: "stdout", process.stderr: "stderr", messages: "messages"}
    writes = {}
    remaining_output = output_bytes
    next_sample = 0.0
    missed_samples = 0

    with selectors.DefaultSelector() as selector:
        for stream, channel in readers.items():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, channel)
        if source:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            writes[process.stdin] = memoryview(source)
        else:
            process.stdin.close()
        os.set_blocking(replies.fileno(), False)

        while True:
            now = monotonic()
            if process.returncode is None:
                peak = _reap(process, os.WNOHANG)
                if peak is not None and peak > memory_bytes:
                    raise _Stop("memory_limit")
            if now >= deadline:
                raise _Stop("timeout")

            if process.returncode is None:
                if now >= next_sample:
                    rss = resident_bytes(process.pid)
                    if rss is None:
                        missed_samples += 1
                        if missed_samples == 3:
                            if _reap(process, os.WNOHANG) is None:
                                raise _Stop("unavailable")
                            continue
                    else:
                        missed_samples = 0
                        if rss > memory_bytes:
                            raise _Stop("memory_limit")
                    next_sample = now + _POLL_SECONDS
                if replies not in writes:
                    reply = protocol.take_reply()
                    if reply is not None:
                        selector.register(replies, selectors.EVENT_WRITE)
                        writes[replies] = reply
                delay = max(0.0, min(deadline, next_sample) - monotonic())
            else:
                for stream in writes:
                    selector.unregister(stream)
                writes.clear()
                if not readers:
                    break
                delay = 0

            for key, event in selector.select(delay):
                stream = key.fileobj
                if event & selectors.EVENT_WRITE:
                    payload = writes[stream]
                    try:
                        payload = payload[os.write(stream.fileno(), payload[:FRAME_BYTES]):]
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        payload = memoryview(b"")
                    if payload:
                        writes[stream] = payload
                    else:
                        selector.unregister(stream)
                        del writes[stream]
                        if stream is process.stdin:
                            stream.close()
                        else:
                            protocol.pending = None
                    continue

                try:
                    chunk = os.read(stream.fileno(), FRAME_BYTES)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    del readers[stream]
                elif key.data == "messages":
                    protocol.receive(chunk)
                else:
                    captured[key.data].extend(chunk[:remaining_output])
                    remaining_output -= len(chunk)
                    if remaining_output < 0:
                        raise _Stop("output_limit")

    if not protocol.ready:
        return "unavailable", None
    if process.returncode != 0 or protocol.buffer or protocol.completion is None:
        return "error", None
    return protocol.completion


def _result(status, result=None, stdout=b"", stderr=b""):
    return {
        "status": status,
        "result": result if status == "ok" else None,
        "stdout": stdout.decode("utf-8", errors="ignore"),
        "stderr": stderr.decode("utf-8", errors="ignore"),
    }


def run(source, *, dispatch, timeout_seconds, memory_bytes, output_bytes):
    try:
        source = source.encode("utf-8")
    except UnicodeError:
        return _result("error")

    captured = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = monotonic() + timeout_seconds
    try:
        resident_bytes = _rss_reader()
        with ExitStack() as stack:
            reply_reader, reply_writer = _pipe(stack)
            message_reader, message_writer = _pipe(stack)
            child_fds = (reply_reader.fileno(), message_writer.fileno())
            process = stack.enter_context(subprocess.Popen(
                sandbox_command(*child_fds),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=child_fds,
                cwd="/",
                env={},
            ))
            try:
                reply_reader.close()
                message_writer.close()
                status, result = _supervise(
                    process, source, reply_writer, message_reader, _Protocol(dispatch),
                    captured, deadline, resident_bytes, memory_bytes, output_bytes,
                )
            except _Stop as stop:
                status, result = stop.args[0], None
            finally:
                peak = _stop_process(process)
            if peak is not None and peak > memory_bytes:
                status, result = "memory_limit", None
    except (AttributeError, OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        status, result = "unavailable", None
    return _result(status, result, **captured)
