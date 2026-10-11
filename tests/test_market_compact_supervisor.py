"""fixture_non_production: Python children only; no pg_restore, PG or network."""

import hashlib
import io
import json
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from ops.market.compact_restore import restore_supervisor as m


def child(code, **limits):
    return m._Supervisor(
        [sys.executable, "-I", "-u", "-c", code], m.Limits(max_seconds=2, **limits),
    )


def stopped(instance):
    assert instance.process.poll() is not None
    assert instance.safe_status()["threads_stopped"]
    assert instance.safe_status()["restore_verified"] is False


def test_binary_stdout_and_actual_exit_are_separate():
    raw = b"fixture_non_production\x00\xff\r\nsecond\t\\N\n"
    with child("import os;os.write(1," + repr(raw) + ")") as instance:
        assert isinstance(instance.stream, io.RawIOBase)
        assert instance.stream.readline(100) == raw[:26]
        assert instance.stream.readline(100) == raw[26:]
        assert instance.stream.readline(100) == b""
        assert instance.safe_status()["exit_waited"] is False
        assert instance.wait_exit() == 0
        assert instance.safe_status()["state"] == "child_exited"
    stopped(instance)


def test_readinto_preserves_bytes_without_text_conversion():
    with child("import os;os.write(1,b'abc\\x00def')") as instance:
        target = bytearray(4)
        assert instance.stream.readinto(target) == 4
        assert target == b"abc\x00"
        assert instance.stream.read(4) == b"def"
        assert instance.wait_exit() == 0
    stopped(instance)


def test_nonzero_child_exit_is_not_eof_success():
    with child("import os;os.write(1,b'fixture_non_production\\n');os._exit(7)") as instance:
        assert instance.stream.readline(100)
        assert instance.stream.readline(100) == b""
        assert instance.wait_exit() == 7
        assert instance.safe_status()["state"] == "child_failed"
    stopped(instance)


@pytest.mark.parametrize("close_output", [False, True], ids=["blocked_read", "eof_alive_child"])
def test_deadline_covers_blocked_stdout_and_exit_wait(close_output):
    code = ("import os,time;" + ("os.close(1);os.close(2);" if close_output else "")
            + "time.sleep(10)")
    started = time.monotonic()
    instance = m._Supervisor([sys.executable, "-I", "-c", code], m.Limits(max_seconds=0.3))
    with pytest.raises(m.SupervisorError, match="deadline_exceeded"):
        with instance:
            assert instance.stream.readline(64) == b""
            instance.wait_exit()
    assert time.monotonic() - started < 3
    stopped(instance)


def test_deadline_runs_when_consumer_is_not_reading_and_queue_is_full():
    instance = m._Supervisor(
        [sys.executable, "-I", "-u", "-c",
         "import os;block=b'x'*65536\nwhile True: os.write(1,block)"],
        m.Limits(max_seconds=0.3, chunk_bytes=4096, queue_chunks=1),
    )
    try:
        instance.process.wait(timeout=2)
        assert instance.safe_status()["state"] == "deadline_exceeded"
        assert instance.queue.qsize() <= 1
        assert instance.stdout_bytes <= 3 * 4096
    finally:
        instance.close()
    stopped(instance)


def test_stderr_flood_is_drained_capped_and_never_exposed():
    secret = "fixture_sensitive_body_not_for_diagnostics"
    instance = child(
        "import os,time;os.write(2," + repr(secret.encode()) + "*10000);time.sleep(10)",
        chunk_bytes=1024, max_stderr_bytes=2048,
    )
    with pytest.raises(m.SupervisorError, match="stderr_limit_exceeded") as error:
        with instance:
            instance.stream.readline(100)
    assert 2048 < instance.stderr_bytes <= 3072
    assert secret not in str(error.value)
    assert secret not in json.dumps(instance.safe_status())
    stopped(instance)


def test_stdout_total_budget_is_enforced_by_producer():
    instance = child("import os;os.write(1,b'x'*10000)", chunk_bytes=1024, max_stdout_bytes=2048)
    with pytest.raises(m.SupervisorError, match="stdout_limit_exceeded"):
        with instance:
            while instance.stream.readline(256):
                pass
    assert instance.stdout_bytes <= 3072
    stopped(instance)


def test_long_line_never_accumulates_beyond_read_and_queue_limits():
    instance = child(
        "import os,time;os.write(1,b'x'*20000);time.sleep(10)",
        max_read_bytes=1025, chunk_bytes=256, queue_chunks=2,
    )
    with pytest.raises(m.SupervisorError, match="consumer_stopped_early"):
        with instance:
            assert instance.stream.readline(1025) == b"x" * 1025
            assert len(instance.stream.pending) <= 256
            assert instance.queue.qsize() <= 2
    stopped(instance)


def test_consumer_exception_cancels_child_without_hiding_parser_failure():
    instance = child("import os,time;os.write(1,b'fixture_non_production\\n');time.sleep(10)")
    with pytest.raises(ValueError, match="fixture parser refusal"):
        with instance:
            assert instance.stream.readline(100)
            raise ValueError("fixture parser refusal")
    assert instance.safe_status()["state"] == "consumer_stopped_early"
    stopped(instance)


def test_wait_exit_before_eof_is_refused_and_child_reaped():
    instance = child("import os,time;os.write(1,b'fixture_non_production\\n');time.sleep(10)")
    with pytest.raises(m.SupervisorError, match="consumer_stopped_early"):
        with instance:
            assert instance.stream.readline(100)
            instance.wait_exit()
    stopped(instance)


def test_external_cancel_unblocks_reader():
    instance = child("import time;time.sleep(10)")
    timer = threading.Timer(0.15, instance.cancel)
    timer.start()
    try:
        with pytest.raises(m.SupervisorError, match="cancelled"):
            with instance:
                instance.stream.readline(64)
    finally:
        timer.cancel()
        timer.join(timeout=1)
    stopped(instance)


def test_unbounded_read_is_refused():
    instance = child("import time;time.sleep(10)")
    with pytest.raises(m.SupervisorError, match="unbounded_read_refused"):
        with instance:
            instance.stream.read()
    stopped(instance)


def test_only_owned_child_is_terminated():
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    sibling = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time;time.sleep(10)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options,
    )
    try:
        instance = m._Supervisor(
            [sys.executable, "-I", "-c", "import time;time.sleep(10)"],
            m.Limits(max_seconds=0.2),
        )
        with pytest.raises(m.SupervisorError, match="deadline_exceeded"):
            with instance:
                instance.stream.readline(1)
        assert sibling.poll() is None
        stopped(instance)
    finally:
        sibling.kill()
        sibling.wait(timeout=2)


def command_fixture(tmp_path, body=b"PGDMPfixture_non_production"):
    executable = tmp_path / "pg_restore.exe"
    executable.write_bytes(b"fixture_non_production not executable")
    archive = tmp_path / "fixture ; no shell.dump"
    archive.write_bytes(body)
    before = archive.stat()
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    proof = m.VerifiedArchive.from_verified(
        archive, checksum, len(body), before=before, after=archive.stat(),
    )
    toc = tmp_path / "ordered data.list"
    toc.write_bytes(
        b"10; 0 101 TABLE DATA public market_source_traces fixture_admin\n"
        b"11; 0 102 TABLE DATA public market_time_series fixture_admin\n"
    )
    return executable, proof, toc, hashlib.sha256(toc.read_bytes()).hexdigest()


def test_fixed_command_has_no_db_shell_or_arbitrary_flags(tmp_path):
    executable, proof, toc, digest = command_fixture(tmp_path)
    command = m.pg_restore_command(executable, proof, toc, digest)
    assert command == [str(executable), *m.FIXED_OPTIONS, "--use-list=" + str(toc), str(proof.path)]
    assert "--file=-" in command and "--exit-on-error" in command


@pytest.mark.parametrize("change", ["order", "duplicate", "extra_sql", "hash"])
def test_toc_has_exact_two_verified_data_entries(tmp_path, change):
    executable, proof, toc, digest = command_fixture(tmp_path)
    rows = toc.read_bytes().splitlines(keepends=True)
    if change == "order":
        body = b"".join(reversed(rows))
    elif change == "duplicate":
        body = rows[0] + rows[0]
    elif change == "extra_sql":
        body = b"".join(rows) + b"DROP TABLE public.fixture_non_production;\n"
    else:
        body = b"".join(rows) + b"; fixture changed\n"
    toc.write_bytes(body)
    if change != "hash":
        digest = hashlib.sha256(body).hexdigest()
    with pytest.raises(m.SupervisorError):
        m.pg_restore_command(executable, proof, toc, digest)


def test_changed_archive_is_refused_before_process_creation(tmp_path):
    executable, proof, toc, digest = command_fixture(tmp_path)
    proof.path.write_bytes(b"PGDMPfixture_non_production changed")
    with pytest.raises(m.SupervisorError, match="archive_changed"):
        m.pg_restore_command(executable, proof, toc, digest)


def test_non_pg_dump_magic_is_refused(tmp_path):
    executable, proof, toc, digest = command_fixture(tmp_path, b"fixture_non_production")
    with pytest.raises(m.SupervisorError, match="archive_magic_mismatch"):
        m.pg_restore_command(executable, proof, toc, digest)


def test_pg_environment_is_not_forwarded(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "fixture_sensitive_password")
    with child("import os;print('PGPASSWORD' in os.environ)") as instance:
        assert instance.stream.readline(100) in (b"False\r\n", b"False\n")
        assert instance.stream.readline(100) == b""
        assert instance.wait_exit() == 0
    stopped(instance)


def test_spawn_error_does_not_expose_paths_or_credentials(tmp_path):
    path = tmp_path / "fixture_sensitive_argument.exe"
    with pytest.raises(m.SupervisorError, match="child_start_failed") as error:
        m._Supervisor([str(path)], m.Limits())
    assert path.name not in str(error.value)


def test_thread_start_failure_does_not_leave_owned_child_running(monkeypatch):
    processes = []
    original_popen = m.subprocess.Popen

    def capture(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    def failure(self):
        raise RuntimeError("fixture thread start failure")

    monkeypatch.setattr(m.subprocess, "Popen", capture)
    monkeypatch.setattr(m.threading.Thread, "start", failure)
    with pytest.raises(RuntimeError, match="fixture thread start failure"):
        child("import time;time.sleep(10)")
    assert len(processes) == 1
    assert processes[0].poll() is not None


@pytest.mark.parametrize("values", [
    {"max_seconds": 0}, {"max_seconds": float("nan")}, {"queue_chunks": 0},
    {"chunk_bytes": 10**9}, {"max_stderr_bytes": -1}, {"max_read_bytes": True},
])
def test_invalid_memory_time_limits(values):
    with pytest.raises(m.SupervisorError, match="invalid_limits"):
        m.Limits(**values)


def test_last_chunk_arriving_at_queue_timeout_is_not_lost_as_eof():
    class LastChunk(queue.Queue):
        def get(self, block=True, timeout=None):
            if block:
                self.put(b"fixture_non_production last chunk")
                raise queue.Empty
            return super().get(block=False)

    done = threading.Event()
    done.set()
    owner = SimpleNamespace(_check=lambda: None, queue=LastChunk(), stdout_done=done)
    assert m._Supervisor._next_chunk(owner) == b"fixture_non_production last chunk"


@pytest.mark.parametrize("exit_code", [0, 7])
def test_actual_pipe_connects_to_existing_restore_coordinator(tmp_path, exit_code):
    # Only the sink is a pure fixture. The existing parser/coordinator consume
    # real binary child stdout and wait for that real Python process's status.
    from ops.market.compact_restore import checkpoint as coordinator
    raw = (
        b"-- PostgreSQL database dump\n"
        b"COPY public.market_source_traces (digest, byte_count, payload) FROM stdin;\n"
        b"\\.\n"
        b"COPY public.market_time_series (series, time, digest, payload) FROM stdin;\n"
        b"\\.\n-- PostgreSQL database dump complete\n"
    )
    archive = tmp_path / "fixture_non_production.dump"
    archive.write_bytes(b"PGDMPfixture_non_production")
    manifest = coordinator.Manifest(
        hashlib.sha256(archive.read_bytes()).hexdigest(), archive.stat().st_size,
        "a" * 64, coordinator.codec_identity(), 0, 0,
    )
    events = []
    sink = SimpleNamespace(
        open=lambda: coordinator.Checkpoint(),
        consume=lambda event, current, following, replay: events.append(event),
        close=lambda: None, check_counts=lambda current: None,
    )
    budget = coordinator.Budget(
        tmp_path, max_growth_bytes=1024, max_seconds=10, free_for_data=lambda: 100 * 1024**3,
    )
    code = "import os;os.write(1," + repr(raw) + ");os._exit(" + str(exit_code) + ")"
    with child(code) as instance:
        report = coordinator.restore_prefix(
            instance.stream, sink, manifest, archive=archive, budget=budget,
            child_exit=instance.wait_exit, evidence_path=tmp_path / "fixture-evidence.json",
        )
    assert report["restore_verified"] is False
    assert len(events) == 2
    assert report["state"] == ("interrupted" if exit_code else "awaiting_ordered_verification")
    assert report["stream_complete"] is (exit_code == 0)
    assert instance.safe_status()["exit_code"] == exit_code
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest.archive_sha256
    stopped(instance)
