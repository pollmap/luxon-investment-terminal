import errno
import gzip
import hashlib
import json

import pytest

from services.market_dashboard import raw_archive
from services.market_dashboard.raw_archive import (
    compact_one,
    read_original,
    read_original_verified,
    save_compressed,
)
from services.market_dashboard.store import Store


def test_lossless_compaction_restart_and_new_writes(tmp_path):
    directory = tmp_path / "raw" / "naver"
    directory.mkdir(parents=True)
    content = ("원본 유지\r\n" * 20000).encode()
    digest = hashlib.sha256(content).hexdigest()
    binary = directory / (digest + ".bin")
    envelope = directory / (digest + ".json")
    binary.write_bytes(content)
    old = json.dumps(
        {
            "sha256": digest,
            "payload": content.decode(),
            "url": "https://example.test",
            "received_at": "2020-01-01",
            "binary_file": binary.name,
        }
    ).encode()
    envelope.write_bytes(old)
    assert compact_one(envelope) > len(content)
    assert not binary.exists()
    assert read_original(directory, digest) == content
    assert gzip.decompress((directory / (digest + ".json.legacy.gz")).read_bytes()) == old
    assert json.loads(envelope.read_bytes())["received_at"] == "2020-01-01"
    assert compact_one(envelope) == 0
    store = Store("sqlite://", tmp_path)
    assert store.raw("naver", "https://example.test", content) == digest
    assert not binary.exists()
    assert read_original(directory, digest) == content


def test_corrupt_original_is_never_deleted(tmp_path):
    directory = tmp_path / "naver"
    directory.mkdir()
    digest = "0" * 64
    binary = directory / (digest + ".bin")
    binary.write_bytes(b"corrupt")
    envelope = directory / (digest + ".json")
    envelope.write_text(json.dumps({"sha256": digest}))
    with pytest.raises(ValueError):
        compact_one(envelope)
    assert binary.read_bytes() == b"corrupt"


def test_json_only_early_archive_is_recoverable(tmp_path):
    directory = tmp_path / "naver"
    directory.mkdir()
    content = ("옛날 원문" * 10000).encode()
    digest = hashlib.sha256(content).hexdigest()
    envelope = directory / (digest + ".json")
    envelope.write_text(
        json.dumps({"sha256": digest, "payload": content.decode()}), encoding="utf-8"
    )
    assert compact_one(envelope) > 0
    assert read_original(directory, digest) == content


def test_new_compressed_archives_keep_exact_binary_and_legacy_dart_format(tmp_path):
    store = Store("sqlite://", tmp_path)
    content = b"PK\x03\x04fixture_non_production\xff"
    digest = store.raw("toss", "https://example.test", content)
    assert read_original(tmp_path / "raw" / "toss", digest) == content
    assert not (tmp_path / "raw" / "toss" / (digest + ".bin")).exists()
    store.raw("dart", "https://example.test", content)
    assert (tmp_path / "raw" / "dart" / (digest + ".bin")).read_bytes() == content


def test_new_real_response_repairs_truncated_metadata_without_discarding_it(tmp_path):
    store = Store("sqlite://", tmp_path)
    content = b'{"fixture_non_production": true}'
    digest = hashlib.sha256(content).hexdigest()
    directory = tmp_path / "raw/naver"
    directory.mkdir(parents=True)
    old = b'{"url":'
    envelope = directory / (digest + ".json")
    envelope.write_bytes(old)
    store.raw("naver", "https://example.test/actual-new-response", content)
    assert read_original(directory, digest) == content
    assert json.loads(envelope.read_bytes())["url"].endswith("/actual-new-response")
    assert next(directory.glob("*.invalid-*")).read_bytes() == old


def legacy_dart(tmp_path, content=None):
    directory = tmp_path / "raw" / "dart"
    directory.mkdir(parents=True)
    if content is None:
        content = ('{"fixture_non_production":"보존할 정정 원문\\r\\n"}\r\n' * 500).encode()
    digest = hashlib.sha256(content).hexdigest()
    metadata = {
        "sha256": digest,
        "payload": content.decode(),
        "bytes": len(content),
        "url": "https://example.invalid/fixture?report=correction",
        "received_at": "2021-01-02T03:04:05Z",
        "binary_file": digest + ".bin",
        "correction": {"previous_receipt": "fixture-old", "current_receipt": "fixture-new"},
    }
    old = json.dumps(metadata, ensure_ascii=False, indent=2).encode()
    envelope = directory / (digest + ".json")
    envelope.write_bytes(old)
    (directory / (digest + ".bin")).write_bytes(content)
    return directory, digest, content, envelope, old


def contents(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


def test_dart_compaction_requires_opt_in_and_preserves_all_metadata(tmp_path):
    directory, digest, content, envelope, old = legacy_dart(tmp_path)
    with pytest.raises(ValueError, match="outside compaction scope"):
        compact_one(envelope)
    assert "dart" not in raw_archive.COMPRESSED_SOURCES
    assert raw_archive.OPT_IN_COMPRESSED_SOURCES == {"dart"}
    assert compact_one(envelope, opt_in_sources=("dart",)) > 0
    assert read_original(directory, digest) == content
    assert gzip.decompress((directory / (digest + ".json.legacy.gz")).read_bytes()) == old
    physical = {"payload", "binary_file", "archive_version"}
    assert {k: v for k, v in json.loads(old).items() if k not in physical} == {
        k: v for k, v in json.loads(envelope.read_bytes()).items() if k not in physical
    }
    assert compact_one(envelope, opt_in_sources=("dart",)) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 64),
        ("bytes", 12),
        ("bytes", True),
        ("payload", "different"),
        ("binary_file", "other.bin"),
        ("archive_version", 99),
    ],
)
def test_metadata_conflict_preserves_every_file(tmp_path, field, value):
    directory, _, _, envelope, _ = legacy_dart(tmp_path)
    metadata = json.loads(envelope.read_bytes())
    metadata[field] = value
    envelope.write_text(json.dumps(metadata), encoding="utf8")
    before = contents(directory)
    with pytest.raises(ValueError):
        compact_one(envelope, opt_in_sources=("dart",))
    assert contents(directory) == before


@pytest.mark.parametrize("compressed", [b"not gzip", gzip.compress(b"other document")])
def test_disagreeing_compressed_duplicate_never_overwrites_valid_original(tmp_path, compressed):
    directory, digest, content, envelope, _ = legacy_dart(tmp_path)
    (directory / (digest + ".bin.gz")).write_bytes(compressed)
    before = contents(directory)
    with pytest.raises(ValueError):
        read_original_verified(directory, digest)
    with pytest.raises(ValueError):
        compact_one(envelope, opt_in_sources=("dart",))
    assert contents(directory) == before


@pytest.mark.parametrize("phase", [".bin.gz", ".json.legacy.gz", ".json"])
def test_interrupted_publication_is_resumable_without_metadata_loss(tmp_path, monkeypatch, phase):
    directory, digest, content, envelope, old = legacy_dart(tmp_path)
    original = raw_archive.atomic_bytes

    def fail_after_publish(path, body):
        original(path, body)
        if path.name == digest + phase:
            raise OSError("fixture_non_production interrupted")

    monkeypatch.setattr(raw_archive, "atomic_bytes", fail_after_publish)
    with pytest.raises(OSError):
        compact_one(envelope, opt_in_sources=("dart",))
    assert (directory / (digest + ".bin")).read_bytes() == content
    assert read_original(directory, digest) == content
    monkeypatch.setattr(raw_archive, "atomic_bytes", original)
    compact_one(envelope, opt_in_sources=("dart",))
    assert read_original(directory, digest) == content
    assert gzip.decompress((directory / (digest + ".json.legacy.gz")).read_bytes()) == old


def test_empty_original_is_lossless_not_missing(tmp_path):
    directory, digest, content, envelope, old = legacy_dart(tmp_path, b"")
    compact_one(envelope, opt_in_sources=("dart",))
    assert read_original(directory, digest) == content == b""
    assert gzip.decompress((directory / (digest + ".json.legacy.gz")).read_bytes()) == old


def test_new_dart_compressed_writer_is_opt_in_at_call_site_only(tmp_path):
    directory = tmp_path / "dart"
    directory.mkdir()
    content = b"fixture_non_production\xff\x00"
    digest = hashlib.sha256(content).hexdigest()
    save_compressed(directory, digest, content, "https://example.invalid", "2020-01-01")
    assert read_original(directory, digest) == content
    assert not (directory / (digest + ".bin")).exists()
    assert "dart" not in raw_archive.COMPRESSED_SOURCES


def test_damaged_current_envelope_is_preserved_without_rewriting_first_metadata(tmp_path):
    directory, digest, _, envelope, old = legacy_dart(tmp_path)
    compact_one(envelope, opt_in_sources=("dart",))
    damaged = b'{"url":'
    envelope.write_bytes(damaged)
    before = contents(directory)
    with pytest.raises(ValueError):
        compact_one(envelope, opt_in_sources=("dart",))
    assert contents(directory) == before
    assert gzip.decompress((directory / (digest + ".json.legacy.gz")).read_bytes()) == old


def test_existing_v2_with_damaged_legacy_must_not_be_reported_complete(tmp_path):
    directory, digest, _, envelope, _ = legacy_dart(tmp_path)
    compact_one(envelope, opt_in_sources=("dart",))
    legacy = directory / (digest + ".json.legacy.gz")
    legacy.write_bytes(b"broken compressed envelope")
    before = contents(directory)
    with pytest.raises(ValueError):
        compact_one(envelope, opt_in_sources=("dart",))
    assert contents(directory) == before


def test_absent_original_remains_distinct_from_a_corrupt_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_original(tmp_path, "0" * 64)


def test_default_binary_reader_does_not_inspect_other_copies(tmp_path, monkeypatch):
    directory, digest, content, envelope, _ = legacy_dart(tmp_path)
    envelope.write_bytes(b"broken old metadata")
    (directory / (digest + ".bin.gz")).write_bytes(b"broken duplicate")
    original = raw_archive.Path.read_bytes
    reads = []

    def record(path):
        reads.append(path.name)
        return original(path)

    monkeypatch.setattr(raw_archive.Path, "read_bytes", record)
    assert read_original(directory, digest) == content
    assert reads == [digest + ".bin"]
    with pytest.raises(ValueError):
        read_original_verified(directory, digest)


def test_default_compressed_reader_decompresses_only_one_original(tmp_path, monkeypatch):
    directory = tmp_path / "toss"
    directory.mkdir()
    content = b"fixture_non_production"
    digest = hashlib.sha256(content).hexdigest()
    (directory / (digest + ".bin.gz")).write_bytes(gzip.compress(content))
    (directory / (digest + ".json")).write_bytes(b"old incomplete metadata")
    (directory / (digest + ".json.legacy.gz")).write_bytes(b"unrelated broken duplicate")
    original = gzip.decompress
    calls = []

    def decompress(body):
        calls.append(len(body))
        return original(body)

    monkeypatch.setattr(gzip, "decompress", decompress)
    assert read_original(directory, digest) == content
    assert len(calls) == 1


def test_default_writer_does_not_redecode_existing_gzip_or_legacy(tmp_path, monkeypatch):
    directory = tmp_path / "naver"
    directory.mkdir()
    content = b"fixture_non_production"
    digest = hashlib.sha256(content).hexdigest()
    save_compressed(directory, digest, content, "https://example.invalid/first", "2020-01-01")
    (directory / (digest + ".json.legacy.gz")).write_bytes(b"legacy not audited on the hot path")
    original = raw_archive.Path.read_bytes
    reads = []

    def record(path):
        reads.append(path.name)
        return original(path)

    monkeypatch.setattr(raw_archive.Path, "read_bytes", record)
    monkeypatch.setattr(gzip, "decompress", lambda _: pytest.fail("No repeat decompression"))
    save_compressed(directory, digest, content, "https://example.invalid/new", "2099-01-01")
    assert reads == [digest + ".json"]


def test_default_gzip_corruption_exception_contract_is_unchanged(tmp_path):
    digest = hashlib.sha256(b"fixture_non_production").hexdigest()
    (tmp_path / (digest + ".bin.gz")).write_bytes(b"not gzip")
    with pytest.raises(gzip.BadGzipFile):
        read_original(tmp_path, digest)


@pytest.mark.parametrize("failed_sync", [1, 2, 3], ids=["original", "legacy", "metadata"])
def test_compaction_directory_sync_failure_keeps_original_before_removal(
    tmp_path, monkeypatch, failed_sync,
):
    directory, digest, content, envelope, original_metadata = legacy_dart(tmp_path)
    binary = directory / (digest + ".bin")
    calls = 0

    def failure(path):
        nonlocal calls
        assert path == directory
        calls += 1
        if calls == failed_sync:
            raise OSError(errno.EIO, "fixture directory sync failure")

    with monkeypatch.context() as patch:
        patch.setattr(raw_archive, "_sync_directory", failure)
        with pytest.raises(OSError) as error:
            compact_one(envelope, opt_in_sources={"dart"})
        assert error.value.errno == errno.EIO
    assert binary.read_bytes() == content
    assert read_original_verified(directory, digest) == content
    compact_one(envelope, opt_in_sources={"dart"})
    assert not binary.exists()
    assert read_original_verified(directory, digest) == content
    preserved = directory / (digest + ".json.legacy.gz")
    assert gzip.decompress(preserved.read_bytes()) == original_metadata


def test_compaction_syncs_publications_before_unlink_and_unlink_afterward(tmp_path, monkeypatch):
    directory, digest, content, envelope, _ = legacy_dart(tmp_path)
    binary = directory / (digest + ".bin")
    events = []
    original_unlink = raw_archive.Path.unlink

    def sync(path):
        assert path == directory
        events.append("directory_fsync")

    def unlink(path, *args, **kwargs):
        if path == binary:
            events.append("original_unlink")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(raw_archive, "_sync_directory", sync)
    monkeypatch.setattr(raw_archive.Path, "unlink", unlink)
    compact_one(envelope, opt_in_sources={"dart"})
    assert events == ["directory_fsync"] * 3 + ["original_unlink", "directory_fsync"]
    assert read_original_verified(directory, digest) == content


def test_post_unlink_sync_failure_keeps_verified_replacement(tmp_path, monkeypatch):
    directory, digest, content, envelope, original_metadata = legacy_dart(tmp_path)
    calls = 0

    def failure(path):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError(errno.EIO, "fixture unlink directory sync failure")

    with monkeypatch.context() as patch:
        patch.setattr(raw_archive, "_sync_directory", failure)
        with pytest.raises(OSError):
            compact_one(envelope, opt_in_sources={"dart"})
    assert calls == 4
    assert not (directory / (digest + ".bin")).exists()
    assert read_original_verified(directory, digest) == content
    preserved = directory / (digest + ".json.legacy.gz")
    assert gzip.decompress(preserved.read_bytes()) == original_metadata
    assert compact_one(envelope, opt_in_sources={"dart"}) == 0
