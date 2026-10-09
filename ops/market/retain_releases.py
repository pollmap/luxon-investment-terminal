"""Archive inactive app releases before project-scoped retention. Never touches data/images."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

ROOT = Path("/srv/services/korea-stocks")
PUBLIC_ROOT = Path("/var/www/korea-stocks")
SERVICES = ("market-api", "market-worker", "market-web", "openalgo")
RELEASE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{4,79}")


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**2:
        raise ValueError("Unexpected retention metadata file")
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_containers():
    # Project only; never read or print container environment/credentials.
    template = (
        '{"name":{{json .Name}},"image":{{json .Config.Image}},'
        '"state":{{json .State.Status}},"mounts":{{json .Mounts}},'
        '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
    )
    output = subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            template,
            *["korea-stocks-" + service + "-1" for service in SERVICES],
        ],
        text=True,
        timeout=15,
    )
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    if (
        len(rows) != len(SERVICES)
        or {r["service"] for r in rows} != set(SERVICES)
        or any(
            r["project"] != "korea-stocks" or not r["image"].startswith("korea-stocks-")
            for r in rows
        )
    ):
        raise ValueError("Unexpected container ownership")
    return rows


def static_web_reference():
    current = (ROOT / "current").resolve(strict=True)
    if current.parent != (ROOT / "releases").resolve():
        raise ValueError("Unexpected current release")
    marker, manifest = (
        current / "static-web-deployment.json",
        current / "static-web/build-manifest.json",
    )
    if not marker.exists() and not manifest.exists():
        return None
    state, build = read_json(marker), read_json(manifest)
    public = Path(state.get("public_root", "/nonexistent"))
    if (
        state.get("state") != "active"
        or not state.get("release")
        or state["release"] != build.get("release")
        or build.get("profile") != "static"
        or digest(manifest) != state.get("manifest_sha256")
        or public.is_symlink()
        or public.resolve().parent != (PUBLIC_ROOT / "releases").resolve()
        or (PUBLIC_ROOT / "current").resolve(strict=True) != public.resolve()
        or digest(public / "build-manifest.json") != state["manifest_sha256"]
    ):
        raise ValueError("Static web activation or manifest does not match")
    return {"release": state["release"], "public_root": str(public)}


def pending_name(name):
    # .next and .env.example are legitimate release files, not pending writes.
    return (
        name.endswith((".partial", ".tmp", ".staging"))
        or ".partial." in name
        or name == "current.next"
    )


def release_files(path):
    files = list(path.rglob("*"))
    if any(
        p.is_symlink() or pending_name(p.name) or not (p.is_file() or p.is_dir()) for p in files
    ):
        raise ValueError("Release contains pending, linked or unsupported entries")
    # Match Linux Path component order explicitly on every OS. Full pathname
    # string order differs for sibling files such as a.b and directory a/x.
    return sorted(
        (p for p in files if p.is_file()),
        key=lambda p: PurePosixPath(p.relative_to(path).as_posix()).parts,
    )


def fingerprint_files(path, files):
    signature = hashlib.sha256()
    total = 0
    for item in files:
        size = item.stat().st_size
        signature.update(
            json.dumps(
                [item.relative_to(path).as_posix(), size, digest(item)],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature.update(b"\n")
        total += size
    return {"bytes": total, "files": len(files), "content_sha256": signature.hexdigest()}


def inventory(*, measure_bytes=True, containers=None):
    root = (ROOT / "releases").resolve()
    current = (ROOT / "current").resolve(strict=True)
    if current.parent != root:
        raise ValueError("Unexpected current release")
    keep = {current.name}
    reasons = {current.name: ["current"]}

    def protect(name, reason):
        keep.add(name)
        reasons.setdefault(name, []).append(reason)

    containers = inspect_containers() if containers is None else containers
    mounts = []
    for container in containers:
        image = container["image"]
        if not image.startswith("korea-stocks-"):
            raise ValueError("Unexpected image ownership")
        protect(image.rsplit(":", 1)[-1], "container:" + container["service"])
        mounts.extend(Path(m["Source"]).resolve() for m in container["mounts"])
    static = static_web_reference()
    if static:
        protect(static["release"], "active_static_web")
    contract_path = ROOT / "shared/data/storage-format.json"
    if contract_path.exists():
        contract = read_json(contract_path)
        floor = contract.get("rollback_floor", "")
        if (
            contract.get("version") != 1
            or not RELEASE_NAME.fullmatch(floor)
            or not (root / floor).is_dir()
        ):
            raise ValueError("Storage rollback floor is missing or invalid")
        protect(floor, "storage_rollback_floor")
    paths = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
    # File existence alone is not a successful deployment: failed/prepared
    # releases must neither displace rollback versions nor be auto-cleaned.
    good, blocked = [], []
    for path in paths:
        try:
            report = read_json(path / "deployment.json")
            if (
                not RELEASE_NAME.fullmatch(path.name)
                or report.get("release") != path.name
                or report.get("state", report.get("status"))
                not in ("active", "activated_local_checks_passed")
            ):
                raise ValueError("Deployment is not a confirmed successful release")
            good.append(path)
        except (ValueError, OSError):
            protect(path.name, "unconfirmed_or_pending_release")
            blocked.append({"name": path.name, "reason": "unconfirmed_or_pending_release"})
    good.sort(key=lambda p: (p / "deployment.json").stat().st_mtime, reverse=True)
    for path in [p for p in good if p.name != current.name][:2]:
        protect(path.name, "recent_successful_predecessor")
    selected = []
    for path in sorted(paths):
        if path.name in keep or any(m == path or m.is_relative_to(path) for m in mounts):
            if path.name not in keep:
                protect(path.name, "container_mount")
            continue
        if path.resolve().parent != root:
            raise ValueError("Release escapes root")
        if measure_bytes:
            try:
                measured = fingerprint_files(path, release_files(path))
            except (ValueError, OSError):
                protect(path.name, "pending_or_linked_content")
                blocked.append({"name": path.name, "reason": "pending_or_linked_content"})
                continue
        else:
            measured = {"bytes": None}  # Scheduled monitor must not hash whole release trees.
        selected.append({"name": path.name, **measured})
    return {
        "inventory_version": 2,
        "current": current.name,
        "keep": sorted(keep),
        "protected_reasons": reasons,
        "blocked": blocked,
        "candidates": selected,
        "inspection": "content_hashes" if measure_bytes else "metadata_only",
        "candidate_bytes": sum(p["bytes"] for p in selected) if measure_bytes else None,
    }


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


def add_releases(tar, state):
    # Most releases contain the same compiled assets. Archive each distinct
    # byte sequence once and preserve the remaining paths as tar hard links.
    content_paths = {}
    for item in state["candidates"]:
        directory = ROOT / "releases" / item["name"]
        for path in release_files(directory):
            name = path.relative_to(ROOT / "releases").as_posix()
            fingerprint = digest(path)
            info = tar.gettarinfo(str(path), arcname=name)
            if fingerprint in content_paths:
                info.type = tarfile.LNKTYPE
                info.linkname = content_paths[fingerprint]
                info.size = 0
                tar.addfile(info)
            else:
                content_paths[fingerprint] = name
                with path.open("rb") as stream:
                    tar.addfile(info, stream)


@contextmanager
def deployment_lock():
    """Share the existing promoter lock; never prune during an app transition."""
    import fcntl

    lock_path = ROOT / ".deploy.lock"
    if lock_path.is_symlink():
        raise ValueError("Unexpected deploy lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def validate_verification(saved, report, verified_sha256):
    """Bind an offsite full-payload check and isolated restore to this inventory."""
    candidates = saved.get("candidates", [])
    restored = next((p for p in candidates if p["name"] == report.get("restored_release")), None)
    if (
        saved.get("inventory_version") != 2
        or not candidates
        or not re.fullmatch(r"[0-9a-f]{64}", verified_sha256 or "")
        or saved.get("archive_sha256") != verified_sha256
        or report.get("archive_sha256") != verified_sha256
        or report.get("candidates") != candidates
        or report.get("verification_version") != 2
        or report.get("restore_verified") is not True
        or report.get("all_release_hashes_verified") is not True
        or not isinstance(report.get("local_archive"), str)
        or not report["local_archive"]
        or report["local_archive"].startswith("/srv/")
        or not isinstance(report.get("compressed_bytes"), int)
        or report["compressed_bytes"] <= 0
        or report.get("verified_files") != sum(p.get("files", 0) for p in candidates)
        or restored is None
        or report.get("restored_files") != restored.get("files")
        or report.get("restored_bytes") != restored.get("bytes")
        or report.get("restored_tree_sha256") != restored.get("content_sha256")
        or not all(re.fullmatch(r"[0-9a-f]{64}", p.get("content_sha256", "")) for p in candidates)
    ):
        raise ValueError("Verified offsite archive and isolated restore evidence required")


def apply_verified(saved, report, verified_sha256, state):
    """Caller holds the deploy lock; complete every check before removing anything."""
    validate_verification(saved, report, verified_sha256)
    if (
        saved.get("current") != state["current"]
        or saved.get("keep") != state["keep"]
        or saved.get("candidates") != state["candidates"]
    ):
        raise ValueError("Release inventory changed")
    targets = []
    releases = (ROOT / "releases").resolve()
    for item in state["candidates"]:
        target = ROOT / "releases" / item["name"]
        if (
            not RELEASE_NAME.fullmatch(item["name"])
            or target.is_symlink()
            or target.resolve().parent != releases
            or target.name in state["keep"]
            or fingerprint_files(target, release_files(target))
            != {k: item[k] for k in ("bytes", "files", "content_sha256")}
        ):
            raise ValueError("Unsafe or changed release removal")
        targets.append(target)
    # Retain the verification receipt on the VPS as well as on the offsite PC.
    receipt = ROOT / "backups" / ("inactive-releases-verified-" + verified_sha256 + ".json")
    if receipt.exists() or receipt.is_symlink():
        if read_json(receipt) != report:
            raise ValueError("Existing verification receipt differs")
    else:
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
    for target in targets:
        shutil.rmtree(target)
    return {
        "removed": len(targets),
        "reclaimed_bytes": state["candidate_bytes"],
        "kept": state["keep"],
    }


def archive_or_apply(args):
    state = inventory()
    if args.archive is None or args.archive.is_symlink():
        raise ValueError("Explicit plain archive path required")
    archive = args.archive.resolve()
    if archive.parent != (ROOT / "backups").resolve() or not re.fullmatch(
        r"inactive-releases-[0-9]{8}(?:-[0-9]+)?\.tar\.gz", archive.name
    ):
        raise ValueError("Unexpected archive path")
    manifest = archive.with_suffix(".manifest.json")
    if args.mode == "archive":
        pending = archive.with_suffix(".partial")
        if any(p.exists() or p.is_symlink() for p in (archive, manifest, pending)):
            raise ValueError("Archive or pending file already exists")
        if not state["candidates"]:
            raise ValueError("No verified inactive releases to archive")
        if args.stream:

            class HashWriter:
                def __init__(self):
                    self.hash = hashlib.sha256()

                def write(self, data):
                    self.hash.update(data)
                    return sys.stdout.buffer.write(data)

            output = HashWriter()
            with tarfile.open(fileobj=output, mode="w|gz", compresslevel=1) as tar:
                add_releases(tar, state)
            sys.stdout.buffer.flush()
            state["archive_sha256"] = output.hash.hexdigest()
            state["offsite_stream"] = True
            with manifest.open("x", encoding="utf-8") as stream:
                json.dump(state, stream, indent=2)
            return
        with pending.open("xb") as stream:
            with tarfile.open(fileobj=stream, mode="w:gz", compresslevel=1) as tar:
                add_releases(tar, state)
        pending.replace(archive)
        state["archive_sha256"] = digest(archive)
        with manifest.open("x", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
        print(
            json.dumps(
                {
                    "archive": str(archive),
                    "bytes": archive.stat().st_size,
                    "sha256": state["archive_sha256"],
                    "releases": len(state["candidates"]),
                }
            )
        )
        return
    saved = read_json(manifest)
    if not args.verification_report:
        raise ValueError("Offsite restore verification report required")
    if args.verification_report == "-":
        raw = sys.stdin.read(1024**2 + 1)
        if len(raw) > 1024**2:
            raise ValueError("Verification report too large")
        report = json.loads(raw)
    else:
        report = read_json(Path(args.verification_report))
    if not saved.get("offsite_stream") and digest(archive) != args.verified_sha256:
        raise ValueError("Archive checksum mismatch")
    print(json.dumps(apply_verified(saved, report, args.verified_sha256, state)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("inventory", "archive", "apply"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--verified-sha256")
    parser.add_argument(
        "--verification-report", help="Offsite hash and isolated restore report; - reads stdin"
    )
    parser.add_argument("--stream", action="store_true")
    parser.add_argument(
        "--metadata-only", action="store_true", help="Inventory without walking or hashing payloads"
    )
    args = parser.parse_args()
    if args.mode == "inventory":
        print(json.dumps(inventory(measure_bytes=not args.metadata_only)))
        return
    with deployment_lock():
        archive_or_apply(args)


if __name__ == "__main__":
    main()
