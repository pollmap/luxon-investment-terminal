"""Raw writer boundary checks; all bytes are fixture_non_production."""

import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.market_dashboard.raw_archive import read_original_verified
from services.market_dashboard.store import Store

SOURCES = (
    "dart",
    "dart-corporations",
    "dart-events",
    "dart-xbrl",
    "dart-reviewed-eps",
    "dart-reviewed-eps-note",
    "dart-bootstrap-eps",
    "dart-share-window",
    "issuer-dividends",
    "issuer-cashflow-review",
    "issuer-share-review",
    "issuer-share-history",
    "kind",
    "kind-dividends",
    "krx-openapi",
    "krx_short_selling",
    "toss",
    "toss-stream",
    "naver",
    "naver-news",
    "fnguide",
    "test",
    "fixture-unit-review",
    "fixture-review-source",
)
CONTENT = b"PK\x03\x04fixture_non_production\xff"


@pytest.fixture
def writer(tmp_path):
    # Exercise only the real filesystem writer; never initialize any database.
    store = object.__new__(Store)
    store.root = tmp_path / "storage"
    store.root.mkdir()
    return store


@pytest.mark.parametrize("source", SOURCES)
def test_current_source_names_preserve_exact_original_and_receipt(writer, source):
    digest = writer.raw(source, "https://example.invalid/original", CONTENT)
    assert digest == hashlib.sha256(CONTENT).hexdigest()
    directory = writer.root / "raw" / source
    assert directory.resolve().parent == (writer.root / "raw").resolve()
    assert read_original_verified(directory, digest) == CONTENT
    metadata_path = directory / f"{digest}.json"
    receipt = json.loads(metadata_path.read_bytes())
    assert receipt["url"] == "https://example.invalid/original"
    assert writer.raw(source, "https://example.invalid/repeated", CONTENT) == digest
    assert json.loads(metadata_path.read_bytes()) == receipt


@pytest.mark.parametrize(
    "source",
    (
        "",
        ".",
        "..",
        "../outside",
        "..\\outside",
        "naver/child",
        "naver\\child",
        "/outside",
        "\\outside",
        "C:\\outside",
        "C:/outside",
        "C:outside",
        "\\\\server\\share",
        "//server/share",
        "\\\\?\\C:\\outside",
        "\\\\.\\C:\\outside",
        "naver:stream",
        "naver.",
        "naver ",
        "naver\x00",
        "CON",
        "NUL",
        "com1",
        "lpt9",
        None,
        Path("outside"),
    ),
)
def test_invalid_namespace_is_rejected_before_creating_raw_directory(writer, source):
    with pytest.raises(ValueError, match="Invalid raw source"):
        writer.raw(source, "https://example.invalid", CONTENT)
    assert list(writer.root.iterdir()) == []


def test_absolute_existing_outside_directory_remains_unchanged(writer, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "original.txt"
    sentinel.write_bytes(b"fixture_non_production_preserve")
    with pytest.raises(ValueError, match="Invalid raw source"):
        writer.raw(str(outside), "https://example.invalid", CONTENT)
    assert list(outside.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"fixture_non_production_preserve"
    assert not (writer.root / "raw").exists()


@pytest.mark.parametrize("linked_part", ("raw", "source"))
def test_existing_symlink_cannot_redirect_writes(writer, tmp_path, linked_part):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = writer.root / "raw"
    if linked_part == "source":
        link.mkdir()
        link = link / "naver"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"Filesystem cannot create test symlink: {type(error).__name__}")
    with pytest.raises(ValueError, match="links are not supported"):
        writer.raw("naver", "https://example.invalid", CONTENT)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("linked_part", ("raw", "source"))
def test_windows_reparse_attribute_is_rejected_before_file_writes(
    writer, monkeypatch, linked_part
):
    directory = writer.root / "raw" / "naver"
    directory.mkdir(parents=True)
    target = directory.parent if linked_part == "raw" else directory
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == target:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="links are not supported"):
        writer.raw("naver", "https://example.invalid", CONTENT)
    assert list(directory.iterdir()) == []


def test_resolved_source_must_be_direct_child_of_raw_root(writer, monkeypatch, tmp_path):
    directory = writer.root / "raw" / "naver"
    outside = tmp_path / "outside"
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == directory:
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="escapes raw root"):
        writer.raw("naver", "https://example.invalid", CONTENT)
    assert not outside.exists()
    assert not (writer.root / "raw").exists()


def test_source_replaced_during_mkdir_is_rechecked_before_file_writes(writer, monkeypatch):
    directory = writer.root / "raw" / "naver"
    original_mkdir = Path.mkdir
    original_lstat = Path.lstat
    replaced = False

    def mkdir(path, *args, **kwargs):
        nonlocal replaced
        original_mkdir(path, *args, **kwargs)
        if path == directory:
            replaced = True

    def lstat(path, *args, **kwargs):
        if path == directory and replaced:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="links are not supported"):
        writer.raw("naver", "https://example.invalid", CONTENT)
    assert list(directory.iterdir()) == []
