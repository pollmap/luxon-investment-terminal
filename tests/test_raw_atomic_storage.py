"""fixture_non_production: raw receipt failures must not publish partial files."""

import errno
import hashlib
import json
import stat
from types import SimpleNamespace

import pytest

from services.market_dashboard import raw_archive
from services.market_dashboard.store import Store

CONTENT = b'{"fixture_non_production":true,"restatement":"fixture-original"}'
URL = "https://example.invalid/fixture-original"


@pytest.fixture
def writer(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_COMPRESS_DART_RAW", "0")
    # This exercises only raw files; no database or provider is initialized.
    instance = object.__new__(Store)
    instance.root = tmp_path
    return instance


def paths(writer, content=CONTENT):
    digest = hashlib.sha256(content).hexdigest()
    directory = writer.root / "raw" / "dart"
    return directory, directory / (digest + ".bin"), directory / (digest + ".json")


@pytest.mark.parametrize("stage", [1, 2], ids=["original", "metadata"])
@pytest.mark.parametrize("failure", ["write", "fsync", "publish"])
def test_disk_full_never_publishes_partial_final_files(writer, monkeypatch, stage, failure):
    directory, binary, envelope = paths(writer)
    calls = 0

    with monkeypatch.context() as patch:
        if failure == "write":
            original = raw_archive.tempfile.NamedTemporaryFile

            class PartialWrite:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    self.stream.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.stream.__exit__(*args)

                def __getattr__(self, name):
                    return getattr(self.stream, name)

                def write(self, body):
                    nonlocal calls
                    calls += 1
                    if calls == stage:
                        self.stream.write(body[:4])
                        raise OSError(errno.ENOSPC, "fixture disk full")
                    return self.stream.write(body)

            patch.setattr(
                raw_archive.tempfile, "NamedTemporaryFile",
                lambda *args, **kwargs: PartialWrite(original(*args, **kwargs)),
            )
        elif failure == "fsync":
            original = raw_archive.os.fsync

            def full(descriptor):
                nonlocal calls
                if stat.S_ISREG(raw_archive.os.fstat(descriptor).st_mode):
                    calls += 1
                    if calls == stage:
                        raise OSError(errno.ENOSPC, "fixture disk full")
                return original(descriptor)

            patch.setattr(raw_archive.os, "fsync", full)
        else:
            original = raw_archive.os.link

            def full(source, destination):
                nonlocal calls
                calls += 1
                if calls == stage:
                    raise OSError(errno.ENOSPC, "fixture disk full")
                return original(source, destination)

            patch.setattr(raw_archive.os, "link", full)

        with pytest.raises(OSError) as error:
            writer.raw("dart", URL, CONTENT)
        assert error.value.errno == errno.ENOSPC

    assert not envelope.exists()
    if stage == 1:
        assert not binary.exists()
    else:
        assert binary.read_bytes() == CONTENT
    assert not list(directory.glob(".archive-*"))

    digest = writer.raw("dart", URL, CONTENT)
    assert raw_archive.read_original_verified(directory, digest) == CONTENT
    assert json.loads(envelope.read_bytes())["payload"].encode() == CONTENT
    assert not list(directory.glob(".archive-*"))


@pytest.mark.parametrize("suffix", [".bin", ".json"])
def test_interruption_after_publish_preserves_complete_files(writer, monkeypatch, suffix):
    directory, binary, envelope = paths(writer)
    original = raw_archive.os.link

    def interrupt(source, destination):
        original(source, destination)
        if destination.suffix == suffix:
            raise KeyboardInterrupt("fixture interruption after publish")

    with monkeypatch.context() as patch:
        patch.setattr(raw_archive.os, "link", interrupt)
        with pytest.raises(KeyboardInterrupt):
            writer.raw("dart", URL, CONTENT)
    assert binary.read_bytes() == CONTENT
    old_metadata = envelope.read_bytes() if envelope.exists() else None
    digest = writer.raw("dart", "https://example.invalid/repeated", CONTENT)
    assert raw_archive.read_original_verified(directory, digest) == CONTENT
    if old_metadata is not None:
        assert envelope.read_bytes() == old_metadata
    assert not list(directory.glob(".archive-*"))


def test_existing_receipt_and_correction_metadata_are_not_overwritten(writer, monkeypatch):
    writer.raw("dart", URL, CONTENT)
    _, binary, envelope = paths(writer)
    metadata = json.loads(envelope.read_bytes())
    metadata.update({"received_at": "2020-01-01T00:00:00Z", "correction_of": "fixture-old"})
    envelope.write_bytes(json.dumps(metadata).encode())
    before = (binary.read_bytes(), envelope.read_bytes())
    monkeypatch.setattr(
        raw_archive.tempfile, "NamedTemporaryFile",
        lambda **kwargs: pytest.fail("Duplicate receipt must not start a new file write"),
    )
    monkeypatch.setattr(
        json, "dumps", lambda *args, **kwargs: pytest.fail("Do not reserialize a saved receipt"),
    )
    writer.raw("dart", "https://example.invalid/repeated", CONTENT)
    assert (binary.read_bytes(), envelope.read_bytes()) == before


def test_serialization_failure_does_not_create_empty_metadata(writer, monkeypatch):
    _, binary, envelope = paths(writer)
    with monkeypatch.context() as patch:
        patch.setattr(
            json, "dumps", lambda *args, **kwargs: (_ for _ in ()).throw(
                MemoryError("fixture serialization failure"),
            ),
        )
        with pytest.raises(MemoryError):
            writer.raw("dart", URL, CONTENT)
    assert binary.read_bytes() == CONTENT
    assert not envelope.exists()
    writer.raw("dart", URL, CONTENT)
    assert json.loads(envelope.read_bytes())["sha256"] == hashlib.sha256(CONTENT).hexdigest()


def test_existing_zero_byte_metadata_is_preserved_without_inferred_repair(writer):
    _, binary, envelope = paths(writer)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(CONTENT)
    envelope.write_bytes(b"")
    writer.raw("dart", URL, CONTENT)
    assert envelope.read_bytes() == b""
    assert binary.read_bytes() == CONTENT
    with pytest.raises(json.JSONDecodeError):
        raw_archive.read_original_verified(binary.parent, hashlib.sha256(CONTENT).hexdigest())


def test_concurrent_publication_preserves_first_complete_receipt(tmp_path, monkeypatch):
    path = tmp_path / "fixture.json"
    original = raw_archive.os.link

    def competitor(source, destination):
        destination.write_bytes(b'{"fixture_non_production":"first receipt"}')
        original(source, destination)

    monkeypatch.setattr(raw_archive.os, "link", competitor)
    assert raw_archive.atomic_create(path, b'{"fixture_non_production":"later receipt"}') is False
    assert path.read_bytes() == b'{"fixture_non_production":"first receipt"}'
    assert not list(tmp_path.glob(".archive-*"))


def test_directory_sync_failure_preserves_published_bytes(tmp_path, monkeypatch):
    path = tmp_path / "fixture.json"
    real_os = raw_archive.os
    directory_descriptor = -123
    closed = []

    def sync(descriptor):
        if descriptor == directory_descriptor:
            raise OSError(errno.EIO, "fixture directory sync failure")
        return real_os.fsync(descriptor)

    # Exercise the POSIX durability branch without changing the host OS or
    # touching a real directory descriptor on Windows.
    monkeypatch.setattr(raw_archive, "os", SimpleNamespace(
        name="posix", O_RDONLY=0, O_DIRECTORY=0, link=real_os.link,
        open=lambda directory, flags: directory_descriptor,
        fsync=sync, close=closed.append,
    ))
    with pytest.raises(OSError) as error:
        raw_archive.atomic_create(path, CONTENT)
    assert error.value.errno == errno.EIO
    assert path.read_bytes() == CONTENT
    assert closed == [directory_descriptor]
    assert not list(tmp_path.glob(".archive-*"))


def test_windows_directory_sync_is_explicitly_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_archive, "os", SimpleNamespace(name="nt"))
    raw_archive._sync_directory(tmp_path)


@pytest.mark.parametrize("source", ["toss", "naver", "fnguide", "dart"])
def test_compressed_writers_keep_existing_atomic_bytes_path(writer, monkeypatch, source):
    monkeypatch.setenv("MARKET_COMPRESS_DART_RAW", "1")
    monkeypatch.setattr(
        raw_archive, "atomic_create",
        lambda *args: pytest.fail("Compressed writers must keep their existing implementation"),
    )
    digest = writer.raw(source, URL, CONTENT)
    directory = writer.root / "raw" / source
    assert raw_archive.read_original_verified(directory, digest) == CONTENT
    assert not (directory / (digest + ".bin")).exists()


@pytest.mark.parametrize("content", [b"", b"PK\x03\x04fixture_non_production\xff"])
def test_empty_and_binary_originals_keep_exact_hash_and_metadata(writer, content):
    directory, _, envelope = paths(writer, content)
    digest = writer.raw("dart", URL, content)
    assert raw_archive.read_original_verified(directory, digest) == content
    metadata = json.loads(envelope.read_bytes())
    assert metadata["bytes"] == len(content)
    assert metadata["payload"] == ("" if not content else None)
