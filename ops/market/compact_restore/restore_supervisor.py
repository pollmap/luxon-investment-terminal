"""Bounded binary pg_restore stdout; no SQL execution or database connection.

Caller verifies the immutable PGDMP archive hash and supplies its before/after
stat identity. The tiny, hashed TOC must select exactly the two TABLE DATA entries
in trace-first order. Use child.stream with restore_prefix and pass
child_exit=child.wait_exit. EOF alone is never a successful process exit.

The watchdog kills only its own Popen handle at the deadline, even while the
consumer is busy. Reaping is bounded separately by cleanup_seconds. This does
not interrupt a caller's database transaction/COMMIT or supervise grandchildren.
OS process creation itself is not interruptible by this Python watchdog. No
successful restore claim is made here, including when pg_restore exits zero.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import queue
import re
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

TABLES = ("market_source_traces", "market_time_series")
FIXED_OPTIONS = (
    "--data-only", "--file=-", "--no-owner", "--no-privileges", "--exit-on-error",
)


class SupervisorError(RuntimeError):
    """Only a fixed reason is exposed, never command/stdout/stderr contents."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__("Restore supervisor: " + reason)


@dataclass(frozen=True)
class Limits:
    max_seconds: float = 300
    cleanup_seconds: float = 2
    chunk_bytes: int = 65536
    queue_chunks: int = 4
    max_read_bytes: int = 16 * 1024**2 + 1
    max_stdout_bytes: int = 256 * 1024**3
    max_stderr_bytes: int = 1024**2

    def __post_init__(self):
        for value, low, high in (
            (self.max_seconds, 0.05, 21600), (self.cleanup_seconds, 0.1, 5),
        ):
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not low <= value <= high):
                raise SupervisorError("invalid_limits")
        for value, high in (
            (self.chunk_bytes, 256 * 1024), (self.queue_chunks, 16),
            (self.max_read_bytes, 16 * 1024**2 + 1),
            (self.max_stdout_bytes, 256 * 1024**3), (self.max_stderr_bytes, 16 * 1024**2),
        ):
            if type(value) is not int or not 0 < value <= high:
                raise SupervisorError("invalid_limits")


def _plain(path):
    path = Path(path)
    if not path.is_absolute():
        raise SupervisorError("absolute_file_required")
    for item in (path, *path.parents):
        meta = item.lstat()
        if stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & 0x400:
            raise SupervisorError("linked_input")
    if not path.is_file():
        raise SupervisorError("regular_file_required")
    return path


def _signature(meta):
    return meta.st_size, meta.st_mtime_ns, meta.st_dev, meta.st_ino


@dataclass(frozen=True)
class VerifiedArchive:
    path: Path
    sha256: str
    signature: tuple[int, int, int, int]

    def __post_init__(self):
        if (not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
                or not isinstance(self.signature, tuple) or len(self.signature) != 4
                or any(type(v) is not int or v < 0 for v in self.signature)
                or self.signature[0] <= 5):
            raise SupervisorError("archive_verification_mismatch")

    @classmethod
    def from_verified(cls, path, sha256, byte_count, *, before, after):
        """Use only AFTER caller's independent full hash check, with its stats."""
        path = _plain(path)
        if (not re.fullmatch(r"[0-9a-f]{64}", sha256)
                or type(byte_count) is not int or byte_count <= 5
                or _signature(before) != _signature(after)
                or _signature(after) != _signature(path.stat()) or after.st_size != byte_count):
            raise SupervisorError("archive_verification_mismatch")
        return cls(path, sha256, _signature(after))

    def check(self):
        path = _plain(self.path)
        if _signature(path.stat()) != self.signature:
            raise SupervisorError("archive_changed")
        with path.open("rb") as source:
            if source.read(5) != b"PGDMP":
                raise SupervisorError("archive_magic_mismatch")
        if _signature(path.stat()) != self.signature:
            raise SupervisorError("archive_changed")


def pg_restore_command(executable, archive, ordered_toc, toc_sha256):
    """Pure file validation + fixed argv; no arbitrary options or SQL accepted."""
    executable = _plain(executable)
    if executable.name not in ("pg_restore", "pg_restore.exe"):
        raise SupervisorError("pg_restore_executable_required")
    if not isinstance(archive, VerifiedArchive):
        raise SupervisorError("verified_archive_required")
    archive.check()
    toc = _plain(ordered_toc)
    before = toc.stat()
    if before.st_size > 65536 or not re.fullmatch(r"[0-9a-f]{64}", toc_sha256):
        raise SupervisorError("invalid_toc")
    with toc.open("rb") as source:
        body = source.read(65537)
    if (len(body) > 65536 or _signature(before) != _signature(toc.stat())
            or hashlib.sha256(body).hexdigest() != toc_sha256):
        raise SupervisorError("toc_changed")
    entries = []
    for line in body.splitlines():
        if not line or line.startswith(b";"):
            continue
        match = re.fullmatch(
            rb"([0-9]+); [0-9]+ [0-9]+ TABLE DATA public "
            rb"(market_source_traces|market_time_series) [A-Za-z_][A-Za-z0-9_$]*", line,
        )
        if not match:
            raise SupervisorError("unexpected_toc_entry")
        entries.append((match[1], match[2].decode("ascii")))
    if (tuple(row[1] for row in entries) != TABLES
            or any(int(row[0]) <= 0 for row in entries)
            or len({int(row[0]) for row in entries}) != 2):
        raise SupervisorError("toc_order_or_identity")
    return [str(executable), *FIXED_OPTIONS, "--use-list=" + str(toc), str(archive.path)]


class _BinaryReader(io.RawIOBase):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.pending = b""
        self.eof_seen = False

    def readable(self):
        return True

    def _read(self, size, line):
        if self.closed:
            raise SupervisorError("stream_closed")
        if type(size) is not int or size < 0 or size > self.owner.limits.max_read_bytes:
            raise SupervisorError("unbounded_read_refused")
        result = bytearray()
        while len(result) < size:
            self.owner._check()
            if not self.pending:
                self.pending = self.owner._next_chunk()
                if not self.pending:
                    self.eof_seen = True
                    break
            take = min(size - len(result), len(self.pending))
            if line:
                found = self.pending.find(b"\n", 0, take)
                if found >= 0:
                    take = found + 1
            part, self.pending = self.pending[:take], self.pending[take:]
            result.extend(part)
            if line and part.endswith(b"\n"):
                break
        return bytes(result)

    def read(self, size=-1):
        return self._read(size, False)

    def readline(self, size=-1):
        return self._read(self.owner.limits.max_read_bytes if size == -1 else size, True)

    def readinto(self, target):
        body = self.read(len(target))
        target[:len(body)] = body
        return len(body)


class _Supervisor:
    """Private generic runner; public entry point admits only fixed pg_restore."""

    def __init__(self, command, limits):
        self.limits = limits
        self.started = time.monotonic()
        self.deadline = self.started + limits.max_seconds
        self.queue = queue.Queue(maxsize=limits.queue_chunks)
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.stdout_done = threading.Event()
        self.stderr_done = threading.Event()
        self.lock = threading.Lock()
        self.reason = None
        self.stdout_bytes = self.stderr_bytes = self.queue_peak = 0
        self.waited = self.closed = False
        environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, text=False, bufsize=0, close_fds=True, env=environment, **options,
            )
        except OSError:
            raise SupervisorError("child_start_failed") from None
        self.stream = _BinaryReader(self)
        self.threads = [
            threading.Thread(target=self._stdout, daemon=True, name="restore-stdout"),
            threading.Thread(target=self._stderr, daemon=True, name="restore-stderr"),
            threading.Thread(target=self._watch, daemon=True, name="restore-deadline"),
        ]
        try:
            for thread in self.threads:
                thread.start()
        except BaseException:
            self._fail("supervisor_start_failed")
            self.close()
            raise

    def _fail(self, reason):
        with self.lock:
            if self.waited:
                return
            if self.reason is None:
                self.reason = reason
        self.stop.set()
        if self.process.poll() is None:
            try:
                self.process.kill()  # exact owned handle; no process-name/group termination
            except OSError:
                pass

    def _check(self):
        if time.monotonic() >= self.deadline and not self.waited:
            self._fail("deadline_exceeded")
        if self.reason:
            raise SupervisorError(self.reason)

    def _watch(self):
        while not self.finished.wait(min(0.02, max(0, self.deadline - time.monotonic()))):
            if time.monotonic() >= self.deadline:
                self._fail("deadline_exceeded")
                return

    def _stdout(self):
        try:
            while not self.stop.is_set():
                part = self.process.stdout.read(self.limits.chunk_bytes)
                if not part:
                    break
                self.stdout_bytes += len(part)
                if self.stdout_bytes > self.limits.max_stdout_bytes:
                    self._fail("stdout_limit_exceeded")
                    break
                while not self.stop.is_set():
                    try:
                        self.queue.put(part, timeout=0.02)
                        self.queue_peak = max(self.queue_peak, self.queue.qsize())
                        break
                    except queue.Full:
                        continue
        except Exception:
            if not self.stop.is_set():
                self._fail("stdout_read_failed")
        finally:
            self.stdout_done.set()

    def _stderr(self):
        try:
            while not self.stop.is_set():
                part = self.process.stderr.read(self.limits.chunk_bytes)
                if not part:
                    break
                self.stderr_bytes += len(part)  # discard immediately; never retain or print
                if self.stderr_bytes > self.limits.max_stderr_bytes:
                    self._fail("stderr_limit_exceeded")
                    break
        except Exception:
            if not self.stop.is_set():
                self._fail("stderr_read_failed")
        finally:
            self.stderr_done.set()

    def _next_chunk(self):
        while True:
            self._check()
            try:
                return self.queue.get(timeout=0.02)
            except queue.Empty:
                if self.stdout_done.is_set():
                    self._check()
                    # The producer can enqueue its last chunk immediately after
                    # get(timeout) expires and then set done. Drain before EOF.
                    try:
                        return self.queue.get_nowait()
                    except queue.Empty:
                        return b""

    def wait_exit(self):
        """Wait for real status only after caller consumed stdout through EOF."""
        self._check()
        if not self.stream.eof_seen or self.stream.pending or not self.queue.empty():
            self._fail("consumer_stopped_early")
            raise SupervisorError(self.reason)
        while self.process.poll() is None or not self.stderr_done.is_set():
            self._check()
            time.sleep(0.01)
        self._check()
        code = self.process.wait(timeout=max(0.001, self.deadline - time.monotonic()))
        with self.lock:
            failure = self.reason
            if time.monotonic() >= self.deadline:
                failure = failure or "deadline_exceeded"
            if failure is None:
                self.waited = True
                self.finished.set()
        if failure:
            self._fail(failure)
            raise SupervisorError(failure)
        return code

    def cancel(self):
        self._fail("cancelled")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if not self.waited and self.reason is None:
            self._fail("consumer_stopped_early")
        self.stop.set()
        self.finished.set()
        if self.process.poll() is None:
            self._fail(self.reason or "consumer_stopped_early")
        until = time.monotonic() + self.limits.cleanup_seconds
        try:
            self.process.wait(timeout=max(0.001, until - time.monotonic()))
        except subprocess.TimeoutExpired:
            self._fail("child_reap_timeout")
        for thread in self.threads:
            if thread.ident is not None:
                thread.join(timeout=max(0, until - time.monotonic()))
        for pipe in (self.process.stdout, self.process.stderr):
            pipe.close()
        self.stream.close()

    def safe_status(self):
        code = self.process.poll()
        return {
            "state": self.reason or ("child_failed" if self.waited and code else
                                     "child_exited" if self.waited else "reading"),
            "exit_code": code, "exit_waited": self.waited,
            "stdout_eof_consumed": self.stream.eof_seen,
            "stdout_bytes": self.stdout_bytes, "stderr_bytes": self.stderr_bytes,
            "queue_peak_chunks": self.queue_peak,
            "closed": self.closed, "threads_stopped": all(not t.is_alive() for t in self.threads),
            "restore_verified": False,
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        early = exc_type is None and not self.waited
        self.close()
        if early:
            raise SupervisorError(self.reason or "consumer_stopped_early")
        return False


DEFAULT_LIMITS = Limits()


def open_pg_restore(executable, archive, *, ordered_toc, toc_sha256, limits=DEFAULT_LIMITS):
    """No DB DSN, shell, password, arbitrary flags, or archive SQL execution."""
    return _Supervisor(pg_restore_command(executable, archive, ordered_toc, toc_sha256), limits)
