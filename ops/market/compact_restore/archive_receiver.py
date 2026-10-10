"""Receive an operator-owned argv's binary dump without reusing any output.

No CLI, SSH policy, SQL, or connection is supplied here. The caller owns the
command policy. The binary supervisor bounds child I/O; disk writes, fsync and
OS process creation remain synchronous OS operations and are checked before and
after, not interruptible by Python. Cleanup has a separate bounded grace period.
PGDMP magic and successful receipt are NOT archive/restore verification.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import stat
import sys
import time
from dataclasses import replace
from pathlib import Path

from .restore_supervisor import Limits, SupervisorError, _Supervisor

MIN_FREE_BYTES = 6 * 1024**3


class ReceiverError(RuntimeError):
    """Fixed reason and safe counters only; never argv or child stderr/body."""

    def __init__(self, reason, report):
        self.reason = reason
        self.report = report
        super().__init__("Archive receiver: " + reason)


def _plain_parent(destination):
    if not destination.is_absolute() or not destination.parent.is_dir():
        raise ValueError("invalid_destination")
    for item in (destination.parent, *destination.parent.parents):
        meta = item.lstat()
        if stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked_parent")


def _disk_free(directory):
    return shutil.disk_usage(directory).free


def _sync_directory(directory):
    # Windows does not expose POSIX directory fsync through os.open/fsync.
    if os.name == "nt":
        return False
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def _rename_exclusive(source, destination):
    """One atomic rename, never POSIX os.rename's overwrite-existing behavior."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows refuses an existing destination.
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError("exclusive_rename_unavailable")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                           ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
            raise OSError(ctypes.get_errno(), "exclusive_rename_failed")
    else:
        raise OSError("exclusive_rename_unavailable")


def _write_chunk(stream, chunk):
    remaining = memoryview(chunk)
    while remaining:
        size = stream.write(remaining)
        if type(size) is not int or size <= 0 or size > len(remaining):
            raise OSError("incomplete_file_write")
        remaining = remaining[size:]


def receive_archive(argv, destination, *, max_bytes, max_seconds,
                    chunk_bytes=65536, max_stderr_bytes=1024**2):
    """Exclusive .partial -> hash/EOF/real exit/fsync -> no-replace rename.

    Failed transfers leave their partial untouched. A failure after rename leaves
    the published file intact and returns no success: inspect `published` before
    retrying with a NEW filename. Neither existing nor failed files are reused.
    KeyboardInterrupt is propagated after stopping the owned child. No child
    output is retained in the report. The caller must independently inspect and
    restore the archive before using it as backup proof.
    """
    report = {"received": False, "published": False, "bytes": 0, "sha256": None,
              "exit_code": None, "exit_waited": False, "stderr_bytes": 0,
              "partial_created": False, "directory_synced": False,
              "archive_contents_verified": False, "restore_verified": False}
    child = None
    phase = "invalid_input"
    started = time.monotonic()
    try:
        if (not isinstance(argv, (list, tuple)) or not argv
                or any(not isinstance(value, str) or not value or "\x00" in value for value in argv)
                or type(max_bytes) is not int or max_bytes <= 5):
            raise ValueError("invalid_input")
        limits = Limits(max_seconds=max_seconds, chunk_bytes=chunk_bytes,
                        max_read_bytes=chunk_bytes, max_stdout_bytes=max_bytes,
                        max_stderr_bytes=max_stderr_bytes)
        destination = Path(destination)
        _plain_parent(destination)
        if destination.name.endswith(".partial"):
            raise ValueError("invalid_destination")
        partial = destination.with_name(destination.name + ".partial")
        if any(os.path.lexists(path) for path in (destination, partial)):
            phase = "output_exists"
            raise ValueError(phase)

        def check(incoming=0):
            if time.monotonic() - started >= limits.max_seconds:
                raise ReceiverError("deadline_exceeded", dict(report))
            if _disk_free(destination.parent) < MIN_FREE_BYTES + incoming:
                raise ReceiverError("disk_reserve", dict(report))

        check(chunk_bytes)
        phase = "partial_create_failed"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(partial, flags, 0o600)
        report["partial_created"] = True
        checksum = hashlib.sha256()
        prefix = b""
        with os.fdopen(fd, "wb", buffering=0) as output:
            identity = os.fstat(output.fileno())
            phase = "partial_sync_failed"
            _sync_directory(destination.parent)
            check()
            phase = "child_start_failed"
            remaining = limits.max_seconds - (time.monotonic() - started)
            if remaining < 0.05:
                raise ReceiverError("deadline_exceeded", dict(report))
            child = _Supervisor(list(argv), replace(limits, max_seconds=remaining))
            with child:
                phase = "transfer_failed"
                while True:
                    check()
                    chunk = child.stream.read(chunk_bytes)
                    check(len(chunk))
                    if not chunk:
                        break
                    if report["bytes"] + len(chunk) > max_bytes:
                        raise ReceiverError("stdout_limit_exceeded", dict(report))
                    prefix = (prefix + chunk[:5])[:5] if len(prefix) < 5 else prefix
                    if len(prefix) == 5 and prefix != b"PGDMP":
                        raise ReceiverError("archive_magic_mismatch", dict(report))
                    _write_chunk(output, chunk)
                    checksum.update(chunk)
                    report["bytes"] += len(chunk)
                if prefix != b"PGDMP" or report["bytes"] <= 5:
                    raise ReceiverError("empty_or_truncated_archive", dict(report))
                report["exit_code"] = child.wait_exit()
                report["exit_waited"] = True
                if report["exit_code"] != 0:
                    raise ReceiverError("child_failed", dict(report))
            check()
            status = child.safe_status()
            if not status["closed"] or not status["threads_stopped"]:
                raise ReceiverError("child_cleanup_incomplete", dict(report))
            phase = "file_sync_failed"
            output.flush()
            os.fsync(output.fileno())
            check()
            written = os.fstat(output.fileno())
            if (written.st_size != report["bytes"] or written.st_nlink != 1
                    or (written.st_dev, written.st_ino) != (identity.st_dev, identity.st_ino)):
                raise ReceiverError("partial_identity_changed", dict(report))
        _plain_parent(destination)
        current = partial.lstat()
        if (stat.S_ISLNK(current.st_mode) or getattr(current, "st_file_attributes", 0) & 0x400
                or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                != (written.st_dev, written.st_ino, written.st_size, written.st_mtime_ns)):
            raise ReceiverError("partial_identity_changed", dict(report))
        check()
        phase = "publish_failed"
        _rename_exclusive(partial, destination)
        report["published"] = True
        phase = "publish_sync_failed"
        report["directory_synced"] = _sync_directory(destination.parent)
        check()
        report.update(received=True, sha256=checksum.hexdigest())
        return report
    except KeyboardInterrupt:
        raise
    except ReceiverError as exc:
        report["reason"] = exc.reason
        raise ReceiverError(exc.reason, report) from None
    except SupervisorError as exc:
        report["reason"] = exc.reason
        raise ReceiverError(exc.reason, report) from None
    except (OSError, ValueError, TypeError):
        report["reason"] = phase
        raise ReceiverError(phase, report) from None
    finally:
        if child is not None:
            # The context already stops failed transfers; this covers failures
            # before its entry as well. Only this exact process handle is used.
            child.close()
            status = child.safe_status()
            report.update(exit_code=status["exit_code"], exit_waited=status["exit_waited"],
                          stderr_bytes=status["stderr_bytes"],
                          child_stopped=child.process.poll() is not None,
                          threads_stopped=status["threads_stopped"])
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
