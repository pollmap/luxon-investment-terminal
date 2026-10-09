"""Local backup encryption; never imports the app or connects to a database.

Format: magic(8) + big-endian header length(4) + authenticated JSON header
        + AES-256-GCM ciphertext + 16-byte authentication tag.
Keys are independent random 32-byte values, sealed using current-user DPAPI.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import struct
import sys
import uuid
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path

LIBRARY = Path(__file__).resolve().parents[2] / "work/tools/backup-crypto"
sys.path.insert(0, str(LIBRARY))
import cryptography  # noqa: E402
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

MAGIC = b"KSDGCM01"
KEY_MAGIC = b"KSDKEY01"
CONTEXT = b"korea-stock-dashboard:backup:aes256gcm:dpapi-current-user:v1"
CHUNK_SIZE = 4 * 1024 * 1024
MAX_SIZE = (2**39 - 256) // 8  # GCM's per-message plaintext limit.
MAX_HEADER = 8192


def utcnow():
    return datetime.now(UTC).isoformat()


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Current-user Windows DPAPI is required")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    entropy_buffer = (ctypes.c_ubyte * len(CONTEXT)).from_buffer_copy(CONTEXT)
    source = DataBlob(len(data), buffer)
    entropy = DataBlob(len(CONTEXT), entropy_buffer)
    target = DataBlob()
    if protect:
        operation = crypt.CryptProtectData
        operation.argtypes = [
            ctypes.POINTER(DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        description = "Korean stock backup AES-256 key v1"
    else:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        description = None
    operation.restype = wintypes.BOOL
    try:
        # UI forbidden; CRYPTPROTECT_LOCAL_MACHINE is deliberately NOT set.
        if not operation(
            ctypes.byref(source),
            description,
            ctypes.byref(entropy),
            None,
            None,
            1,
            ctypes.byref(target),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.memset(buffer, 0, len(buffer))
        if target.pbData:
            ctypes.memset(target.pbData, 0, target.cbData)
            kernel.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def seal_key(key):
    if len(key) != 32:
        raise ValueError("A 256-bit key is required")
    return KEY_MAGIC + dpapi(key, protect=True)


def unseal_key(path):
    path = Path(path)
    if not 32 < path.stat().st_size < 16384:
        raise ValueError("Invalid sealed-key size")
    value = path.read_bytes()
    if not value.startswith(KEY_MAGIC):
        raise ValueError("Invalid sealed-key format")
    key = dpapi(value[len(KEY_MAGIC) :], protect=False)
    if len(key) != 32:
        raise ValueError("Invalid recovered key size")
    return key


def chunks(stream, remaining=None):
    while remaining is None or remaining > 0:
        block = stream.read(CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining))
        if not block:
            if remaining:
                raise ValueError("Truncated file")
            break
        if remaining is not None:
            remaining -= len(block)
        yield block


def digest_file(path):
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as stream:
        for block in chunks(stream):
            digest.update(block)
            size += len(block)
    return {"sha256": digest.hexdigest(), "bytes": size}


def fresh_path(path):
    path = Path(path).resolve()
    if path.exists():
        raise FileExistsError("Destination already exists")
    if not path.parent.is_dir():
        raise FileNotFoundError("Destination directory must already exist")
    return path


def temporary_path(destination):
    return destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")


def publish(temporary, destination):
    # A same-volume hard link publishes a fully fsynced file atomically and
    # refuses to overwrite existing output on Windows and POSIX alike.
    os.link(temporary, destination)
    temporary.unlink()


def write_small(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def encrypt(source, destination, key_path, *, expected_sha256=None, expected_bytes=None):
    source = Path(source).resolve(strict=True)
    destination, key_path = fresh_path(destination), fresh_path(key_path)
    manifest_path = fresh_path(str(destination) + ".manifest.json")
    if len({source, destination, key_path, manifest_path}) != 4:
        raise ValueError("Source, ciphertext, key and manifest paths must differ")
    size = source.stat().st_size
    if size > MAX_SIZE or expected_bytes is not None and size != expected_bytes:
        raise ValueError("Unexpected source size")
    expected_sha256 = expected_sha256 or digest_file(source)["sha256"]
    if not re.fullmatch("[0-9a-f]{64}", expected_sha256):
        raise ValueError("Expected SHA-256 must be lowercase hexadecimal")
    key, nonce = os.urandom(32), os.urandom(12)
    header = {
        "version": 1,
        "algorithm": "AES-256-GCM",
        "tag_bytes": 16,
        "nonce": nonce.hex(),
        "plaintext_bytes": size,
        "plaintext_sha256": expected_sha256,
        "created_at": utcnow(),
        "key_protection": "Windows DPAPI current user",
    }
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    aad = MAGIC + struct.pack(">I", len(encoded)) + encoded
    encoder = Cipher(algorithms.AES256(key), modes.GCM(nonce)).encryptor()
    encoder.authenticate_additional_data(aad)
    temp_cipher, temp_key, temp_manifest = map(
        temporary_path, (destination, key_path, manifest_path)
    )
    owned = []
    try:
        write_small(temp_key, seal_key(key))
        plain_hash, cipher_hash, count = hashlib.sha256(), hashlib.sha256(), 0
        with source.open("rb") as input_file, temp_cipher.open("xb") as output_file:
            output_file.write(aad)
            cipher_hash.update(aad)
            for block in chunks(input_file):
                count += len(block)
                if count > size:
                    raise ValueError("Source changed during encryption")
                plain_hash.update(block)
                encrypted = encoder.update(block)
                output_file.write(encrypted)
                cipher_hash.update(encrypted)
            final = encoder.finalize() + encoder.tag
            output_file.write(final)
            cipher_hash.update(final)
            if count != size or plain_hash.hexdigest() != expected_sha256:
                raise ValueError("Source hash or size did not match")
            output_file.flush()
            os.fsync(output_file.fileno())
        report = {
            "format": "KSDGCM01",
            "algorithm": "AES-256-GCM",
            "created_at": header["created_at"],
            "source_file": source.name,
            "ciphertext_file": destination.name,
            "sealed_key_file": key_path.name,
            "plaintext_bytes": count,
            "plaintext_sha256": plain_hash.hexdigest(),
            "ciphertext_bytes": temp_cipher.stat().st_size,
            "ciphertext_sha256": cipher_hash.hexdigest(),
            "chunk_bytes": CHUNK_SIZE,
            "key_protection": "Windows DPAPI current user; not portable by default",
            "cryptography_version": cryptography.__version__,
        }
        write_small(temp_manifest, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        for temporary, final_path in (
            (temp_key, key_path),
            (temp_cipher, destination),
            (temp_manifest, manifest_path),
        ):
            publish(temporary, final_path)
            owned.append(final_path)
        return report
    except BaseException:
        for path in owned:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in (temp_cipher, temp_key, temp_manifest):
            path.unlink(missing_ok=True)


def read_header(stream):
    prefix = stream.read(12)
    if len(prefix) != 12 or prefix[:8] != MAGIC:
        raise ValueError("Invalid backup format")
    length = struct.unpack(">I", prefix[8:])[0]
    if not 0 < length <= MAX_HEADER:
        raise ValueError("Invalid backup header length")
    encoded = stream.read(length)
    if len(encoded) != length:
        raise ValueError("Truncated backup header")
    header = json.loads(encoded)
    if (
        header.get("version") != 1
        or header.get("algorithm") != "AES-256-GCM"
        or header.get("tag_bytes") != 16
        or not isinstance(header.get("plaintext_bytes"), int)
        or not 0 <= header["plaintext_bytes"] <= MAX_SIZE
        or not re.fullmatch("[0-9a-f]{64}", str(header.get("plaintext_sha256", "")))
        or not re.fullmatch("[0-9a-f]{24}", str(header.get("nonce", "")))
    ):
        raise ValueError("Invalid backup header")
    return header, prefix + encoded


def decrypt(source, key_path, destination=None):
    """Authenticate completely before publishing plaintext; None verifies to a hash sink."""
    source = Path(source).resolve(strict=True)
    if source.name.endswith(".part"):
        raise ValueError("Partial output is not a completed backup")
    destination = fresh_path(destination) if destination is not None else None
    temporary = temporary_path(destination) if destination is not None else None
    key = unseal_key(key_path)
    output_file = None
    try:
        with source.open("rb") as input_file:
            header, aad = read_header(input_file)
            if source.stat().st_size != len(aad) + header["plaintext_bytes"] + 16:
                raise ValueError("Truncated or extended ciphertext")
            input_file.seek(-16, os.SEEK_END)
            tag = input_file.read(16)
            input_file.seek(len(aad))
            decoder = Cipher(
                algorithms.AES256(key), modes.GCM(bytes.fromhex(header["nonce"]), tag)
            ).decryptor()
            decoder.authenticate_additional_data(aad)
            plain_hash, count = hashlib.sha256(), 0
            if temporary is not None:
                output_file = temporary.open("xb")
            for block in chunks(input_file, header["plaintext_bytes"]):
                plain = decoder.update(block)
                plain_hash.update(plain)
                count += len(plain)
                if output_file is not None:
                    output_file.write(plain)
            final = decoder.finalize()  # InvalidTag: no final plaintext is published.
            plain_hash.update(final)
            count += len(final)
            if output_file is not None:
                output_file.write(final)
            if (
                count != header["plaintext_bytes"]
                or plain_hash.hexdigest() != header["plaintext_sha256"]
            ):
                raise ValueError("Authenticated plaintext checksum mismatch")
            if output_file is not None:
                output_file.flush()
                os.fsync(output_file.fileno())
                output_file.close()
                output_file = None
                publish(temporary, destination)
            return {
                "verified_at": utcnow(),
                "authenticated": True,
                "plaintext_bytes": count,
                "plaintext_sha256": plain_hash.hexdigest(),
                "chunk_bytes": CHUNK_SIZE,
                "mode": "file" if destination is not None else "hash_sink",
                "output_file": str(destination) if destination is not None else None,
            }
    finally:
        if output_file is not None:
            output_file.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(
        description="Streaming encrypted backup; current-user Windows DPAPI"
    )
    actions = parser.add_subparsers(dest="action", required=True)
    enc = actions.add_parser("encrypt")
    enc.add_argument("source", type=Path)
    enc.add_argument("destination", type=Path)
    enc.add_argument("--key", required=True, type=Path)
    enc.add_argument("--expected-sha256")
    enc.add_argument("--expected-bytes", type=int)
    for operation in ("decrypt", "verify"):
        sub = actions.add_parser(operation)
        sub.add_argument("source", type=Path)
        sub.add_argument("--key", required=True, type=Path)
        if operation == "decrypt":
            sub.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "encrypt":
            result = encrypt(
                args.source,
                args.destination,
                args.key,
                expected_sha256=args.expected_sha256,
                expected_bytes=args.expected_bytes,
            )
        else:
            result = decrypt(args.source, args.key, getattr(args, "destination", None))
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "published": False}), file=sys.stderr)
        return 130
    except Exception as exc:
        # No key bytes, decrypted contents, or full exception payload enter logs.
        print(json.dumps({"status": "failed", "error": type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
