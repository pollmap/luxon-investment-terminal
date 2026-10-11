"""Stream immutable inactive releases off the full VPS; verify tar payloads before removal."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work/operations-20261008"
SSH = [
    "ssh",
    "-i",
    str(Path.home() / ".ssh/openclaw_vmi3138243_ed25519"),
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "root@62.171.141.206",
]
REMOTE = "/srv/services/korea-stocks/backups/inactive-releases-20261008.tar.gz"
HELPER = "/srv/services/korea-stocks/current/ops/market/retain_releases.py"
CHUNK_BYTES = 1024**2
MIN_FREE_BYTES = 1024**3


class InsufficientLocalSpace(RuntimeError):
    """Stop before spending the reserved space; keep all existing artifacts."""


def require_local_space(directory, minimum=MIN_FREE_BYTES, incoming=0):
    free = shutil.disk_usage(directory).free
    if free < minimum + incoming:
        raise InsufficientLocalSpace(
            f"Local free space {free} bytes is below reserve {minimum} "
            f"plus next write {incoming}; partial files were preserved"
        )


def guarded_copy(source, output, directory, minimum=MIN_FREE_BYTES):
    """Bound memory and check the destination volume before every write."""
    checksum = hashlib.sha256()
    for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
        require_local_space(directory, minimum, len(chunk))
        output.write(chunk)
        output.flush()
        checksum.update(chunk)
    return checksum.digest()


def stop_download(child):
    """Terminate only this invocation's child, never other SSH/archive jobs."""
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def download_archive(pending, command, minimum=MIN_FREE_BYTES):
    if pending.exists():
        raise FileExistsError("Archive partial already exists; use --resume to verify it")
    require_local_space(pending.parent, minimum, CHUNK_BYTES)
    with pending.open("xb") as output:
        child = subprocess.Popen(command, stdout=subprocess.PIPE)
        try:
            guarded_copy(child.stdout, output, pending.parent, minimum)
            result = child.wait()
            if result:
                raise subprocess.CalledProcessError(result, command)
        except BaseException:
            stop_download(child)
            raise
        finally:
            child.stdout.close()


def file_digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            checksum.update(chunk)
    return checksum.digest()


def archive_paths(date, batch=None):
    if not re.fullmatch(r"20[0-9]{6}", date):
        raise ValueError("Expected YYYYMMDD archive date")
    if batch is not None and (type(batch) is not int or batch < 1):
        raise ValueError("Expected a positive archive batch number")
    suffix = date + (f"-{batch}" if batch is not None else "")
    return (
        ROOT / ("work/operations-" + suffix),
        "/srv/services/korea-stocks/backups/inactive-releases-" + suffix + ".tar.gz",
    )


def payload_inventory(archive, candidates):
    """Read every decompressed byte and bind each release tree to source hashes."""
    expected = {row["name"]: row for row in candidates}
    rows = {name: [] for name in expected}
    originals, seen = {}, set()
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or len(path.parts) < 2
                or ".." in path.parts
                or "\\" in member.name
                or str(path) != member.name
                or path.parts[0] not in expected
                or member.name in seen
                or not (member.isfile() or member.islnk())
            ):
                raise ValueError("Unexpected archive member")
            seen.add(member.name)
            if member.islnk():
                if member.linkname not in originals:
                    raise ValueError("Archive link target was not verified")
                size, checksum = originals[member.linkname]
            else:
                checksum = hashlib.sha256()
                size = 0
                with tar.extractfile(member) as source:
                    for chunk in iter(lambda: source.read(1024**2), b""):
                        size += len(chunk)
                        checksum.update(chunk)
                if size != member.size:
                    raise ValueError("Truncated archive member")
                checksum = checksum.hexdigest()
                originals[member.name] = (size, checksum)
            rows[path.parts[0]].append((PurePosixPath(*path.parts[1:]).as_posix(), size, checksum))
    for name, files in rows.items():
        signature = hashlib.sha256()
        for row in sorted(files, key=lambda r: PurePosixPath(r[0]).parts):
            signature.update(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            )
        if (
            len(files) != expected[name].get("files")
            or sum(row[1] for row in files) != expected[name]["bytes"]
            or signature.hexdigest() != expected[name].get("content_sha256")
        ):
            raise ValueError("Archive release content differs from source inventory")
    return sum(map(len, rows.values()))


def filesystem_path(path):
    """Use the same Windows long-path namespace for writes and verification."""
    absolute = str(path.absolute())
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def restored_tree_inventory(root):
    root = filesystem_path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Unexpected restored release directory")
    signature = hashlib.sha256()
    count, total = 0, 0
    files = sorted(
        root.rglob("*"), key=lambda p: PurePosixPath(p.relative_to(root).as_posix()).parts
    )
    for file in files:
        if file.is_symlink():
            raise ValueError("Unexpected restored symlink")
        if file.is_file():
            size = file.stat().st_size
            checksum = hashlib.sha256()
            with file.open("rb") as source:
                for chunk in iter(lambda: source.read(1024**2), b""):
                    checksum.update(chunk)
            signature.update(
                json.dumps(
                    [file.relative_to(root).as_posix(), size, checksum.hexdigest()],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            count += 1
            total += size
    return {"files": count, "bytes": total, "content_sha256": signature.hexdigest()}


def restored_tree_hash(root):
    return restored_tree_inventory(root)["content_sha256"]


def verify_restored_release(root, candidate):
    measured = restored_tree_inventory(root)
    if measured != {k: candidate[k] for k in ("files", "bytes", "content_sha256")}:
        raise ValueError("Restored release content mismatch")
    return measured


def main():
    global OUT, REMOTE, HELPER
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="20261008")
    parser.add_argument("--helper", choices=("current", "incoming"), default="current",
                        help="Use a reviewed uploaded helper without changing the active release")
    parser.add_argument(
        "--batch", type=int, help="Positive suffix for another archive on the same day"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Remove unchanged inactive releases after offsite verification",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Verify an already downloaded partial against its remote manifest",
    )
    parser.add_argument(
        "--min-free-gib", type=int, default=1,
        help="Keep at least this much local free space during download and restore (minimum 1)",
    )
    args = parser.parse_args()
    if args.helper == "incoming":
        HELPER = "/srv/services/korea-stocks/incoming/retain_releases.py"
    if args.min_free_gib < 1:
        parser.error("--min-free-gib must be at least 1")
    minimum = args.min_free_gib * 1024**3
    OUT, REMOTE = archive_paths(args.date, args.batch)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / PurePosixPath(REMOTE).name
    if target.exists():
        raise ValueError("Completed archive already exists; do not overwrite it")
    pending = target.with_suffix(".partial")
    if not args.resume:
        download_archive(
            pending, [*SSH, f"python3 {HELPER} archive --stream --archive {REMOTE}"], minimum
        )
    h = hashlib.sha256()
    with pending.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            h.update(chunk)
    manifest = json.loads(subprocess.check_output([*SSH, f"cat {REMOTE[:-3]}.manifest.json"]))
    if h.hexdigest() != manifest["archive_sha256"]:
        raise ValueError("Offsite checksum mismatch")
    expected = {r["name"]: r["bytes"] for r in manifest["candidates"]}
    if not expected or manifest.get("inventory_version") != 2:
        raise ValueError("Content-hashed inactive release inventory required")
    count = payload_inventory(pending, manifest["candidates"])
    # Restore one complete release, including hard-linked archive members, into
    # a new local directory. No production path is involved in this check.
    restored = OUT / ("release-restore-verified" if args.resume else "release-restore-verification")
    reuse_restore = restored.exists()
    if reuse_restore and not args.resume:
        raise ValueError("Restore verification directory already exists")
    if restored.is_symlink() or (reuse_restore and not restored.is_dir()):
        raise ValueError("Unexpected restore verification path")
    selected = next(iter(expected), None)
    if not reuse_restore:
        # Hard links are restored as independent files, as counted by the
        # verified manifest. Budget the entire selected release before starting.
        require_local_space(OUT, minimum, expected[selected])
        restored.mkdir()
        with tarfile.open(pending, "r:gz") as tar:
            for member in tar:
                if member.name.split("/")[0] != selected:
                    continue
                if not (member.isfile() or member.islnk()):
                    raise ValueError("Unexpected restore member")
                destination = (restored / member.name).resolve()
                if not destination.is_relative_to(restored.resolve()):
                    raise ValueError("Restore path escapes verification directory")
                destination = filesystem_path(destination)
                require_local_space(OUT, minimum, member.size)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, destination.open("xb") as output:
                    expected_hash = guarded_copy(source, output, OUT, minimum)
                if file_digest(destination) != expected_hash:
                    raise ValueError("Restored file checksum mismatch")
    # On resume a pre-existing directory is read-only: never overwrite or fill
    # partial contents. Every file/byte/hash must match the verified archive.
    candidate = next(r for r in manifest["candidates"] if r["name"] == selected)
    measured = verify_restored_release(restored / selected, candidate)
    restored_bytes, restored_files = measured["bytes"], measured["files"]
    tree_hash = measured["content_sha256"]
    report = {
        **manifest,
        "local_archive": str(target),
        "verified_files": count,
        "verification_version": 2,
        "all_release_hashes_verified": True,
        "compressed_bytes": pending.stat().st_size,
        "restored_release": selected,
        "restored_files": restored_files,
        "restored_bytes": restored_bytes,
        "restored_tree_sha256": tree_hash,
        "restore_verified": True,
    }
    report_text = json.dumps(report, indent=2)
    require_local_space(OUT, minimum, len(report_text.encode("utf-8")))
    pending.replace(target)
    (OUT / "release-archive-verified.json").write_text(report_text, encoding="utf-8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("archive_sha256", "candidate_bytes", "verified_files", "compressed_bytes")
            }
        ),
        flush=True,
    )
    # Apply only to unchanged candidates after the verified archive exists offsite.
    if args.apply:
        applied = json.loads(
            subprocess.check_output(
                [
                    *SSH,
                    f"python3 {HELPER} apply --archive {REMOTE} "
                    f"--verified-sha256 {h.hexdigest()} --verification-report -",
                ],
                input=json.dumps(report).encode("utf-8"),
            )
        )
        (OUT / "release-cleanup-applied.json").write_text(json.dumps(applied, indent=2))
        print(json.dumps(applied), flush=True)
    # Retain the verified local restore beside its audit for explicit retention.


if __name__ == "__main__":
    main()
