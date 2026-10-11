"""Small synthetic fixtures only; production dump is never modified by tests."""

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ops/market/backup_crypto.py"
spec = importlib.util.spec_from_file_location("isolated_backup_crypto", SCRIPT)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
from cryptography.exceptions import InvalidTag  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Actual current-user Windows DPAPI is required"
)


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "CHUNK_SIZE", 4096)
    data = (b"fixture_non_production\x00\xff" + bytes(range(256))) * 180
    source = tmp_path / "fixture.dump"
    source.write_bytes(data)
    ciphertext = tmp_path / "fixture.dump.aesgcm"
    key = tmp_path / "fixture.key.dpapi"
    result = backup.encrypt(
        source,
        ciphertext,
        key,
        expected_sha256=hashlib.sha256(data).hexdigest(),
        expected_bytes=len(data),
    )
    return source, ciphertext, key, result


def test_actual_dpapi_streaming_roundtrip_and_hash_sink(sample):
    source, ciphertext, key, report = sample
    restored = source.with_name("restored.dump")
    actual = backup.decrypt(ciphertext, key, restored)
    assert restored.read_bytes() == source.read_bytes()
    assert actual["authenticated"] is True
    assert actual["plaintext_bytes"] == source.stat().st_size
    assert actual["plaintext_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert backup.decrypt(ciphertext, key)["mode"] == "hash_sink"
    assert report["ciphertext_sha256"] == hashlib.sha256(ciphertext.read_bytes()).hexdigest()
    assert report["ciphertext_bytes"] == ciphertext.stat().st_size
    assert backup.unseal_key(key) not in key.read_bytes()
    assert not list(source.parent.glob("*.part"))


def test_each_backup_has_independent_random_key_and_nonce(sample):
    source, ciphertext, key, _ = sample
    other = ciphertext.with_name("other.aesgcm")
    other_key = key.with_name("other.key.dpapi")
    backup.encrypt(source, other, other_key)
    assert backup.unseal_key(key) != backup.unseal_key(other_key)
    with ciphertext.open("rb") as stream, other.open("rb") as other_stream:
        assert (
            backup.read_header(stream)[0]["nonce"] != backup.read_header(other_stream)[0]["nonce"]
        )
    assert ciphertext.read_bytes() != other.read_bytes()


def test_wrong_key_never_publishes_plaintext(sample):
    source, ciphertext, _, _ = sample
    wrong = source.with_name("wrong.key.dpapi")
    wrong.write_bytes(backup.seal_key(os.urandom(32)))
    output = source.with_name("wrong-key.dump")
    with pytest.raises(InvalidTag):
        backup.decrypt(ciphertext, wrong, output)
    assert not output.exists() and not list(source.parent.glob("*.part"))


@pytest.mark.parametrize("area", ["ciphertext", "tag", "header"])
def test_tamper_is_authenticated_before_publish(sample, area):
    source, ciphertext, key, _ = sample
    data = bytearray(ciphertext.read_bytes())
    length = struct.unpack(">I", data[8:12])[0]
    if area == "ciphertext":
        data[12 + length + 200] ^= 1
    elif area == "tag":
        data[-1] ^= 1
    else:
        header = json.loads(data[12 : 12 + length])
        digest = header["plaintext_sha256"]
        header["plaintext_sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]
        encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        assert len(encoded) == length
        data[12 : 12 + length] = encoded
    bad = ciphertext.with_name("tampered.aesgcm")
    bad.write_bytes(data)
    output = source.with_name("tampered.dump")
    with pytest.raises(InvalidTag):
        backup.decrypt(bad, key, output)
    assert not output.exists() and not list(source.parent.glob("*.part"))


@pytest.mark.parametrize("change", ["truncated", "extended", "bad_header_length"])
def test_structural_corruption_is_rejected(sample, change):
    source, ciphertext, key, _ = sample
    data = ciphertext.read_bytes()
    if change == "truncated":
        data = data[:-17]
    elif change == "extended":
        data += b"extra"
    else:
        data = data[:8] + struct.pack(">I", 10_000_000) + data[12:]
    bad = ciphertext.with_name("incomplete.aesgcm")
    bad.write_bytes(data)
    output = source.with_name("incomplete.dump")
    with pytest.raises(ValueError):
        backup.decrypt(bad, key, output)
    assert not output.exists() and not list(source.parent.glob("*.part"))


def interrupt_after_chunk(monkeypatch):
    real = backup.chunks

    def interrupted(stream, remaining=None):
        for block in real(stream, remaining):
            yield block
            raise KeyboardInterrupt()

    monkeypatch.setattr(backup, "chunks", interrupted)


def test_encryption_interrupt_preserves_original_without_final_outputs(sample, monkeypatch):
    source, _, _, report = sample
    output = source.with_name("interrupted.aesgcm")
    key = source.with_name("interrupted.key.dpapi")
    original = source.read_bytes()
    interrupt_after_chunk(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        backup.encrypt(source, output, key, expected_sha256=report["plaintext_sha256"])
    assert source.read_bytes() == original
    assert not output.exists() and not key.exists()
    assert not Path(str(output) + ".manifest.json").exists()
    assert not list(source.parent.glob("*.part"))


def test_decryption_interrupt_never_publishes_partial_plaintext(sample, monkeypatch):
    source, ciphertext, key, _ = sample
    output = source.with_name("interrupted.dump")
    interrupt_after_chunk(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        backup.decrypt(ciphertext, key, output)
    assert not output.exists() and not list(source.parent.glob("*.part"))


def test_force_stopped_part_is_not_accepted_as_complete(sample):
    source, ciphertext, key, _ = sample
    partial = source.with_name("force-stopped.part")
    partial.write_bytes(ciphertext.read_bytes())
    with pytest.raises(ValueError, match="Partial"):
        backup.decrypt(partial, key)
    assert partial.exists()  # Do not silently delete another process's output.


def test_existing_files_are_never_overwritten(sample):
    source, ciphertext, key, _ = sample
    before = [p.read_bytes() for p in (source, ciphertext, key)]
    with pytest.raises(FileExistsError):
        backup.encrypt(source, ciphertext, key)
    with pytest.raises(FileExistsError):
        backup.decrypt(ciphertext, key, source)
    assert [p.read_bytes() for p in (source, ciphertext, key)] == before


def test_expected_hash_mismatch_publishes_nothing(sample):
    source, _, _, _ = sample
    output = source.with_name("incorrect-source.aesgcm")
    key = source.with_name("incorrect-source.key.dpapi")
    with pytest.raises(ValueError, match="hash or size"):
        backup.encrypt(source, output, key, expected_sha256="0" * 64)
    assert not output.exists() and not key.exists() and not list(source.parent.glob("*.part"))


def test_dpapi_blob_tampering_never_yields_plaintext(sample):
    source, ciphertext, key, _ = sample
    bad = source.with_name("tampered.key.dpapi")
    data = bytearray(key.read_bytes())
    data[-10] ^= 1
    bad.write_bytes(data)
    with pytest.raises(OSError):
        backup.decrypt(ciphertext, bad, source.with_name("bad-key.dump"))
    assert not list(source.parent.glob("*.part"))


def test_decrypt_cli_is_usable_and_does_not_print_key(sample):
    source, ciphertext, key, _ = sample
    output = source.with_name("cli-restored.dump")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "decrypt", str(ciphertext), str(output), "--key", str(key)],
        capture_output=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["authenticated"] is True and report["mode"] == "file"
    assert output.read_bytes() == source.read_bytes()
    assert backup.unseal_key(key).hex().encode() not in result.stdout + result.stderr
