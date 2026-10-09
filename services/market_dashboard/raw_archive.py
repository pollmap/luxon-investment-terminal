"""Lossless compression of original bytes, with immutable legacy envelopes preserved."""

import gzip
import hashlib
import json
import os
import tempfile
import zlib
from pathlib import Path

# Default writers stay unchanged until the deployment owner enables DART.
COMPRESSED_SOURCES = frozenset(("toss", "naver", "fnguide"))
OPT_IN_COMPRESSED_SOURCES = frozenset(("dart",))


def _validate_hash(digest, content=None):
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("Invalid source hash")
    if content is not None and hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Source archive hash mismatch")


def _safe_file(path):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Source archive symlinks are not supported")


def _gunzip(path):
    _safe_file(path)
    try:
        return gzip.decompress(path.read_bytes())
    except (OSError, EOFError, zlib.error) as error:
        raise ValueError("Compressed source archive is invalid") from error


def _validate_metadata(metadata, digest, content):
    if not isinstance(metadata, dict):
        raise ValueError("Source metadata must be an object")
    if "sha256" in metadata and metadata["sha256"] != digest:
        raise ValueError("Metadata identity mismatch")
    if "bytes" in metadata and (
        type(metadata["bytes"]) is not int or metadata["bytes"] != len(content)
    ):
        raise ValueError("Metadata byte count mismatch")
    if metadata.get("binary_file") is not None and metadata["binary_file"] not in (
        digest + ".bin",
        digest + ".bin.gz",
    ):
        raise ValueError("Metadata binary identity mismatch")
    payload = metadata.get("payload")
    if payload is not None and (not isinstance(payload, str) or payload.encode("utf-8") != content):
        raise ValueError("Duplicated payload differs from original")


def _validate_legacy(directory, digest, content, metadata):
    preserved = directory / (digest + ".json.legacy.gz")
    _safe_file(preserved)
    if not preserved.exists():
        return
    old = json.loads(_gunzip(preserved))
    _validate_metadata(old, digest, content)
    # Only physical-storage fields may change. URLs, receipt times, correction
    # attributes and unknown metadata must survive every restart unchanged.
    physical = {"payload", "binary_file", "archive_version"}
    if metadata is not None and (
        {k: v for k, v in old.items() if k not in physical}
        != {k: v for k, v in metadata.items() if k not in physical}
    ):
        raise ValueError("Legacy envelope metadata mismatch")
    return old


def _validate_copies(directory, digest, content):
    for suffix in (".bin", ".bin.gz"):
        path = directory / (digest + suffix)
        _safe_file(path)
        if path.exists():
            saved = _gunzip(path) if suffix.endswith(".gz") else path.read_bytes()
            _validate_hash(digest, saved)
            if saved != content:
                raise ValueError("Duplicated originals differ")


def atomic_bytes(path, content):
    pending = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".archive-", delete=False
        ) as stream:
            pending = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def read_original(directory, digest):
    """Existing fast reader: one preferred original, no metadata duplicate reads."""
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid source hash")
    binary = directory / (digest + ".bin")
    compressed = directory / (digest + ".bin.gz")
    if binary.is_file():
        content = binary.read_bytes()
    elif compressed.is_file():
        content = gzip.decompress(compressed.read_bytes())
    else:
        metadata = json.loads((directory / (digest + ".json")).read_bytes())
        if not isinstance(metadata.get("payload"), str):
            raise ValueError("Original payload missing")
        content = metadata["payload"].encode("utf-8")
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Source archive hash mismatch")
    return content


def read_original_verified(directory, digest):
    """Explicit maintenance/replay reader: verify all copies and their metadata."""
    directory = Path(directory)
    _validate_hash(digest)
    binary = directory / (digest + ".bin")
    compressed = directory / (digest + ".bin.gz")
    envelope = directory / (digest + ".json")
    for path in (binary, compressed, envelope):
        _safe_file(path)
    metadata = json.loads(envelope.read_bytes()) if envelope.exists() else None
    if envelope.exists() and not isinstance(metadata, dict):
        raise ValueError("Source metadata must be an object")
    if binary.is_file():
        content = binary.read_bytes()
    elif compressed.is_file():
        content = _gunzip(compressed)
    else:
        # Early releases stored UTF-8 responses only in the JSON envelope.
        if not envelope.exists():
            # Archive-only collectors distinguish absent evidence from corrupt
            # evidence. Preserve their existing FileNotFoundError contract.
            raise FileNotFoundError("Original source archive is absent")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("payload"), str):
            raise ValueError("Original payload missing")
        content = metadata["payload"].encode("utf-8")
    _validate_hash(digest, content)
    _validate_copies(directory, digest, content)
    if metadata is not None:
        _validate_metadata(metadata, digest, content)
    _validate_legacy(directory, digest, content, metadata)
    return content


def save_compressed(directory, digest, content, url, received_at):
    """Keep the existing ingestion path; full-copy audits are explicit operations."""
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Incoming source hash mismatch")
    binary = directory / (digest + ".bin.gz")
    legacy = directory / (digest + ".bin")
    if not binary.exists() and not legacy.exists():
        compressed = gzip.compress(content, compresslevel=3, mtime=0)
        if gzip.decompress(compressed) != content:
            raise ValueError("Source compression mismatch")
        atomic_bytes(binary, compressed)
    envelope = directory / (digest + ".json")
    valid = False
    if envelope.exists():
        original_envelope = envelope.read_bytes()
        try:
            saved = json.loads(original_envelope)
            valid = isinstance(saved, dict) and saved.get("sha256") == digest
        except (ValueError, UnicodeError):
            pass
        if not valid:
            # Earlier disk-full writes left empty/truncated envelopes. Preserve
            # those exact bytes, then record this newly received real response.
            damaged_hash = hashlib.sha256(original_envelope).hexdigest()
            preserved = directory / (digest + ".json.invalid-" + damaged_hash)
            if not preserved.exists():
                atomic_bytes(preserved, original_envelope)
    if not valid:
        updated = {
            "url": url,
            "received_at": received_at,
            "sha256": digest,
            "payload": None,
            "binary_file": legacy.name if legacy.exists() else binary.name,
            "bytes": len(content),
            "archive_version": 2,
        }
        atomic_bytes(envelope, json.dumps(updated, ensure_ascii=False).encode())


def compact_one(envelope, *, opt_in_sources=()):
    """Verify originals, preserve metadata, atomically publish, then remove duplicates."""
    envelope = Path(envelope)
    optional = frozenset(opt_in_sources)
    if not optional.issubset(OPT_IN_COMPRESSED_SOURCES):
        raise ValueError("Unsupported optional archive source")
    directory, digest = envelope.parent, envelope.stem
    if directory.name not in COMPRESSED_SOURCES | optional or envelope.is_symlink():
        raise ValueError("Archive outside compaction scope")
    old = envelope.read_bytes()
    metadata = json.loads(old)
    if not isinstance(metadata, dict) or metadata.get("sha256") != digest:
        raise ValueError("Metadata identity mismatch")
    if metadata.get("archive_version") not in (None, 1, 2):
        raise ValueError("Unsupported source archive version")
    content = read_original_verified(directory, digest)
    binary = directory / (digest + ".bin")
    compressed = directory / (digest + ".bin.gz")
    preserved = directory / (digest + ".json.legacy.gz")
    before = sum(p.stat().st_size for p in (envelope, binary, compressed, preserved) if p.exists())
    if (
        metadata.get("archive_version") == 2
        and metadata.get("payload") is None
        and metadata.get("binary_file") == compressed.name
        and not binary.exists()
    ):
        return 0
    if not compressed.exists():
        atomic_bytes(compressed, gzip.compress(content, compresslevel=3, mtime=0))
    if _gunzip(compressed) != content:
        raise ValueError("Stored compressed source mismatch")
    if not preserved.exists():
        atomic_bytes(preserved, gzip.compress(old, compresslevel=3, mtime=0))
    # Preserve the first envelope for recovery; an interrupted second pass may
    # see an already upgraded envelope and must not replace that original.
    if metadata.get("archive_version") != 2 and _gunzip(preserved) != old:
        raise ValueError("Legacy envelope mismatch")
    updated = {**metadata, "payload": None, "binary_file": compressed.name, "archive_version": 2}
    if envelope.read_bytes() != old:
        raise ValueError("Archive envelope changed during compaction")
    atomic_bytes(envelope, json.dumps(updated, ensure_ascii=False).encode())
    # Verify the committed envelope and every surviving duplicate before removal.
    read_original_verified(directory, digest)
    binary.unlink(missing_ok=True)
    after = sum(p.stat().st_size for p in (envelope, compressed, preserved) if p.exists())
    return before - after
