"""fixture_non_production: Python child streams only, no PG/SSH/network."""

import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from ops.market.compact_restore import archive_receiver as m

RAW = b"PGDMPfixture_non_production\x00\xff\r\n\\N\t" * 5


def command(code):
    return [sys.executable, "-I", "-u", "-c", code]


def writer(raw=RAW, *, exit_code=0):
    return command("import os;os.write(1," + repr(raw) + ");os._exit(" + str(exit_code) + ")")


@pytest.fixture(autouse=True)
def ample_space(monkeypatch):
    monkeypatch.setattr(m, "_disk_free", lambda path: 100 * 1024**3)


def receive(tmp_path, argv=None, **options):
    settings = {"max_bytes": 1024**2, "max_seconds": 3, "chunk_bytes": 32}
    settings.update(options)
    return m.receive_archive(argv or writer(), tmp_path / "fixture.dump", **settings)


def test_exact_binary_hash_exit_and_publication(tmp_path):
    report = receive(tmp_path)
    assert (tmp_path / "fixture.dump").read_bytes() == RAW
    assert not (tmp_path / "fixture.dump.partial").exists()
    assert report["received"] and report["published"]
    assert report["bytes"] == len(RAW)
    assert report["sha256"] == hashlib.sha256(RAW).hexdigest()
    assert report["exit_waited"] and report["exit_code"] == 0
    assert report["child_stopped"] and report["threads_stopped"]
    assert report["restore_verified"] is False and report["archive_contents_verified"] is False
    assert report["directory_synced"] is (os.name != "nt")


@pytest.mark.parametrize("name", ["fixture.dump", "fixture.dump.partial"])
def test_existing_output_is_never_reused_or_child_started(tmp_path, monkeypatch, name):
    path = tmp_path / name
    path.write_bytes(b"preserve fixture_non_production")
    monkeypatch.setattr(m, "_Supervisor", lambda *args: pytest.fail("unexpected child"))
    with pytest.raises(m.ReceiverError, match="output_exists"):
        receive(tmp_path)
    assert path.read_bytes() == b"preserve fixture_non_production"


def test_nonzero_after_complete_stdout_preserves_partial(tmp_path):
    with pytest.raises(m.ReceiverError, match="child_failed") as failure:
        receive(tmp_path, writer(exit_code=7))
    assert (tmp_path / "fixture.dump.partial").read_bytes() == RAW
    assert not (tmp_path / "fixture.dump").exists()
    assert failure.value.report["exit_code"] == 7
    assert failure.value.report["exit_waited"] and failure.value.report["child_stopped"]


@pytest.mark.parametrize("close_output", [False, True])
def test_deadline_stops_owned_child_and_preserves_partial(tmp_path, close_output):
    code = "import os,time;os.write(1,b'PGDMPfixture_non_production');"
    code += "os.close(1);os.close(2);" if close_output else ""
    code += "time.sleep(10)"
    started = time.monotonic()
    with pytest.raises(m.ReceiverError, match="deadline_exceeded") as failure:
        receive(tmp_path, command(code), max_seconds=0.4)
    assert time.monotonic() - started < 3
    assert (tmp_path / "fixture.dump.partial").exists()
    assert not (tmp_path / "fixture.dump").exists()
    assert failure.value.report["child_stopped"] and failure.value.report["threads_stopped"]


def test_disk_reserve_checked_before_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_disk_free", lambda path: m.MIN_FREE_BYTES)
    monkeypatch.setattr(m, "_Supervisor", lambda *args: pytest.fail("unexpected child"))
    with pytest.raises(m.ReceiverError, match="disk_reserve"):
        receive(tmp_path)
    assert not list(tmp_path.iterdir())


def test_disk_reserve_rechecked_each_chunk_and_stops_without_delete(tmp_path, monkeypatch):
    writes = []
    original = m._write_chunk

    def write(stream, chunk):
        original(stream, chunk)
        writes.append(len(chunk))

    monkeypatch.setattr(m, "_write_chunk", write)
    monkeypatch.setattr(m, "_disk_free", lambda path: m.MIN_FREE_BYTES - 1
                        if len(writes) >= 2 else 100 * 1024**3)
    with pytest.raises(m.ReceiverError, match="disk_reserve"):
        receive(tmp_path)
    assert writes == [32, 32]
    assert (tmp_path / "fixture.dump.partial").read_bytes() == RAW[:64]


def test_stdout_limit_rejects_oversized_child(tmp_path):
    with pytest.raises(m.ReceiverError, match="stdout_limit_exceeded") as failure:
        receive(tmp_path, max_bytes=64)
    assert (tmp_path / "fixture.dump.partial").stat().st_size <= 64
    assert not failure.value.report["received"] and failure.value.report["child_stopped"]


def test_stderr_flood_is_bounded_and_not_exposed(tmp_path):
    code = "import os;os.write(1,b'PGDMPfixture_non_production');" \
           "os.write(2,b'fixture_secret_not_for_output'*10000)"
    with pytest.raises(m.ReceiverError, match="stderr_limit_exceeded") as failure:
        receive(tmp_path, command(code), max_stderr_bytes=64)
    assert "fixture_secret" not in str(failure.value) + json.dumps(failure.value.report)
    assert failure.value.report["stderr_bytes"] <= 64 + 32
    assert failure.value.report["child_stopped"]


@pytest.mark.parametrize("raw", [b"", b"PGDMP", b"not_a_dump"])
def test_empty_truncated_or_wrong_magic_not_published(tmp_path, raw):
    with pytest.raises(m.ReceiverError):
        receive(tmp_path, writer(raw))
    assert (tmp_path / "fixture.dump.partial").exists()
    assert not (tmp_path / "fixture.dump").exists()


def test_write_failure_preserves_partial_and_hides_os_error(tmp_path, monkeypatch):
    def broken(stream, chunk):
        stream.write(chunk[:8])
        raise OSError("fixture_secret_from_os")

    monkeypatch.setattr(m, "_write_chunk", broken)
    with pytest.raises(m.ReceiverError, match="transfer_failed") as failure:
        receive(tmp_path)
    assert (tmp_path / "fixture.dump.partial").read_bytes() == RAW[:8]
    assert "fixture_secret" not in str(failure.value)
    assert failure.value.report["child_stopped"]


def test_file_fsync_failure_keeps_full_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_sync_directory", lambda path: os.name != "nt")
    monkeypatch.setattr(m.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fixture error")))
    with pytest.raises(m.ReceiverError, match="file_sync_failed"):
        receive(tmp_path)
    assert (tmp_path / "fixture.dump.partial").read_bytes() == RAW
    assert not (tmp_path / "fixture.dump").exists()


def test_destination_created_during_transfer_is_not_overwritten(tmp_path, monkeypatch):
    rename = m._rename_exclusive

    def competitor(source, destination):
        destination.write_bytes(b"other fixture_non_production")
        rename(source, destination)

    monkeypatch.setattr(m, "_rename_exclusive", competitor)
    with pytest.raises(m.ReceiverError, match="publish_failed"):
        receive(tmp_path)
    assert (tmp_path / "fixture.dump").read_bytes() == b"other fixture_non_production"
    assert (tmp_path / "fixture.dump.partial").read_bytes() == RAW


def test_post_rename_sync_failure_is_not_success_and_keeps_published_file(tmp_path, monkeypatch):
    calls = []

    def sync(path):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("fixture error")
        return True

    monkeypatch.setattr(m, "_sync_directory", sync)
    with pytest.raises(m.ReceiverError, match="publish_sync_failed") as failure:
        receive(tmp_path)
    assert failure.value.report["published"] and not failure.value.report["received"]
    assert (tmp_path / "fixture.dump").read_bytes() == RAW


def test_cancellation_propagates_after_owned_child_cleanup(tmp_path, monkeypatch):
    children = []
    original = m._Supervisor

    def track(*args):
        child = original(*args)
        children.append(child)
        return child

    monkeypatch.setattr(m, "_Supervisor", track)
    monkeypatch.setattr(m, "_write_chunk", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        receive(tmp_path)
    assert children[0].process.poll() is not None
    assert children[0].safe_status()["threads_stopped"]
    assert (tmp_path / "fixture.dump.partial").exists()


def test_failed_spawn_preserves_empty_partial_without_argv_in_error(tmp_path):
    with pytest.raises(m.ReceiverError, match="child_start_failed") as failure:
        receive(tmp_path, [str(tmp_path / "missing_fixture_secret_program")])
    assert (tmp_path / "fixture.dump.partial").stat().st_size == 0
    assert "fixture_secret" not in str(failure.value)


def test_unrelated_process_survives_receiver_timeout(tmp_path):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    sibling = subprocess.Popen(command("import time;time.sleep(10)"),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, **options)
    try:
        with pytest.raises(m.ReceiverError, match="deadline_exceeded"):
            receive(tmp_path, command("import time;time.sleep(10)"), max_seconds=0.2)
        assert sibling.poll() is None
    finally:
        sibling.kill()
        sibling.wait(timeout=2)


def test_short_writes_preserve_complete_chunk():
    class Sink:
        def __init__(self):
            self.data = bytearray()

        def write(self, view):
            self.data.extend(view[:3])
            return min(3, len(view))

    sink = Sink()
    m._write_chunk(sink, RAW)
    assert bytes(sink.data) == RAW


def test_partial_creation_uses_same_deadline_before_starting_child(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(m, "_sync_directory", lambda path: clock.__setitem__(0, 3.0))
    monkeypatch.setattr(m, "_Supervisor", lambda *args: pytest.fail("unexpected child"))
    with pytest.raises(m.ReceiverError, match="deadline_exceeded"):
        receive(tmp_path)
    assert (tmp_path / "fixture.dump.partial").stat().st_size == 0


def test_finalization_deadline_does_not_publish_late_file(tmp_path, monkeypatch):
    original = m.os.fsync

    def late(fd):
        original(fd)
        time.sleep(0.6)

    monkeypatch.setattr(m, "_sync_directory", lambda path: False)
    monkeypatch.setattr(m.os, "fsync", late)
    with pytest.raises(m.ReceiverError, match="deadline_exceeded"):
        receive(tmp_path, max_seconds=0.5)
    assert (tmp_path / "fixture.dump.partial").read_bytes() == RAW
    assert not (tmp_path / "fixture.dump").exists()


def test_invalid_limits_do_not_start_child_or_create_files(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_Supervisor", lambda *args: pytest.fail("unexpected child"))
    for kwargs in ({"max_bytes": 5}, {"max_seconds": 0}, {"chunk_bytes": 0}):
        with pytest.raises(m.ReceiverError):
            receive(tmp_path, **kwargs)
    assert not list(tmp_path.iterdir())


def test_symlink_parent_is_refused_before_start(tmp_path, monkeypatch):
    parent = tmp_path / "linked"
    real = tmp_path / "real"
    real.mkdir()
    try:
        parent.symlink_to(real, target_is_directory=True)
    except OSError:
        # Model a Windows reparse directory without requiring link privileges.
        original = m.Path.lstat

        def lstat(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == parent:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
            return value

        parent.mkdir()
        monkeypatch.setattr(m.Path, "lstat", lstat)
    monkeypatch.setattr(m, "_Supervisor", lambda *args: pytest.fail("unexpected child"))
    with pytest.raises(m.ReceiverError, match="invalid_input"):
        receive(parent)
    assert not list(real.iterdir())
