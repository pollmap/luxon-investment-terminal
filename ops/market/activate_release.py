"""Server-side application-only deployment. No secret output, pruning or DB migration."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path("/srv/hannun")
PROJECT = "hannun-public-market"
APPS = ("market-api", "market-web", "market-worker")


def run(args, log_path=None, **kwargs):
    # Never relay Docker config/build output, which can include expanded credentials.
    result = subprocess.run(args, capture_output=True, **kwargs)
    if log_path:
        log_path.write_bytes(result.stdout + b"\n" + result.stderr)
        log_path.chmod(0o600)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args[0]} {args[1]} (exit {result.returncode})")
    return result.stdout


def inspect_services():
    result = {}
    for service in (*APPS, "market-db", "market-redis"):
        name = f"{PROJECT}-{service}-1"
        template = (
            '{"id":{{json .Id}},"image":{{json .Image}},'
            '"labels":{{json .Config.Labels}},"state":{{json .State.Status}}}'
        )
        item = json.loads(run(["docker", "inspect", "--format", template, name]))
        labels = item["labels"] or {}
        if (
            labels.get("com.docker.compose.project") != PROJECT
            or labels.get("com.docker.compose.service") != service
        ):
            raise ValueError("Container ownership does not match this service")
        if item["state"] != "running":
            raise ValueError(f"Existing {service} is not running")
        result[service] = {key: item[key] for key in ("id", "image", "state")}
    return result


def preflight():
    if not ROOT.is_dir():
        raise ValueError("Expected service root is missing")
    secret = ROOT / "secrets/runtime.env"
    if not secret.is_file() or secret.stat().st_mode & 0o077:
        raise ValueError("Runtime credentials must exist with owner-only permissions")
    if shutil.disk_usage(ROOT).free < 3 * 1024**3:
        raise ValueError("At least 3 GiB free disk is required; no automatic deletion is performed")
    run(["docker", "compose", "version"])
    return inspect_services()


def validate_release(release):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{5,79}", release):
        raise ValueError("Invalid release identifier")
    return release


def unpack(archive_path, source, expected_hash):
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("Source archive hash mismatch")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        total = 0
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or member.name in names
            ):
                raise ValueError("Unsafe or duplicated archive member")
            if any(
                part in ("storage", "node_modules", ".git") or part.startswith(".next")
                for part in path.parts
            ):
                raise ValueError("Runtime content is not a deployable source")
            if path.name == "runtime.env" or (
                path.name.startswith(".env") and path.name != ".env.example"
            ):
                raise ValueError("Private configuration must not be packaged")
            total += member.size
            if total > 256 * 1024**2:
                raise ValueError("Unexpectedly large source archive")
            names.add(member.name)
        if (
            not {"compose.market.yml", "services/market_dashboard/app.py", "apps/web/package.json"}
            <= names
        ):
            raise ValueError("Required application files are missing")
        source.mkdir(parents=True, exist_ok=False)
        for member in members:
            target = source / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as incoming, target.open("xb") as output:
                shutil.copyfileobj(incoming, output)


def compose(source, override):
    return [
        "docker",
        "compose",
        "--project-name",
        PROJECT,
        "--env-file",
        str(ROOT / "secrets/runtime.env"),
        "-f",
        str(source / "compose.market.yml"),
        "-f",
        str(override),
    ]


def read_url(url):
    with urllib.request.urlopen(url, timeout=15) as response:
        if response.status != 200:
            raise ValueError("HTTP check failed")
        return response.read()


def smoke(new_features=True):
    read_url("http://127.0.0.1:3210/stocks?code=005930")
    base = "http://127.0.0.1:8210/api/market/v1"
    search = json.loads(read_url(base + "/search?q=005930"))
    if not any(row.get("code") == "005930" for row in search.get("rows", [])):
        raise ValueError("Known security missing from search")
    detail = json.loads(read_url(base + "/securities/005930"))
    if detail.get("security", {}).get("code") != "005930":
        raise ValueError("Security detail contract failed")
    if new_features:
        for suffix, sections in (
            ("financing", ("lending", "credit", "stock_loan")),
            ("short-selling", ("trades", "balance")),
        ):
            data = json.loads(read_url(base + "/securities/005930/" + suffix))
            if any(not isinstance(data.get(key, {}).get("rows"), list) for key in sections):
                raise ValueError("Activity API contract failed")


def wait_ready(new_features=True):
    deadline = time.monotonic() + 150
    while True:
        try:
            inspect_services()
            smoke(new_features)
            return
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError("Application readiness deadline exceeded") from None
            time.sleep(3)


def activate(release, digest):
    validate_release(release)
    if not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("Invalid archive digest")
    os.umask(0o077)
    before = preflight()
    directory = ROOT / "releases" / release
    if directory.exists():
        raise ValueError("Release already exists; use a fresh immutable release identifier")
    source = directory / "source"
    unpack(ROOT / "incoming" / (release + ".tgz"), source, digest)
    report = {"release": release, "source_sha256": digest, "status": "prepared", "before": before}
    report_path = directory / "deployment.json"

    def save(status):
        report["status"] = status
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    save("prepared")
    backup = ROOT / "backups" / ("market-predeploy-" + release + ".dump")
    backup.parent.mkdir(parents=True, exist_ok=True)
    db = f"{PROJECT}-market-db-1"
    # Stream the backup instead of holding the DB in process memory.
    with backup.open("xb") as output:
        process = subprocess.run(
            ["docker", "exec", db, "pg_dump", "-U", "market", "-d", "market", "-Fc"],
            stdout=output,
            stderr=subprocess.PIPE,
        )
    if process.returncode or backup.stat().st_size == 0:
        save("backup_failed")
        raise RuntimeError("Database backup failed; applications were not changed")
    with backup.open("rb") as incoming:
        run(["docker", "exec", "-i", db, "pg_restore", "--list"], stdin=incoming)
    report["backup"] = {"path": str(backup), "bytes": backup.stat().st_size}
    rollback = directory / "rollback.json"
    candidate = directory / "candidate.json"
    rollback_images, candidate_images = {}, {}
    for service in APPS:
        repository = PROJECT + "-" + service
        tag = repository + ":rollback-" + release
        run(["docker", "tag", before[service]["image"], tag])
        rollback_images[service] = {"image": tag}
        candidate_images[service] = {"image": repository + ":" + release}
    rollback.write_text(json.dumps({"services": rollback_images}), encoding="utf-8")
    candidate.write_text(json.dumps({"services": candidate_images}), encoding="utf-8")
    command = compose(source, candidate)
    save("building")
    try:
        run([*command, "build", *APPS], log_path=directory / "build-private.log")
    except Exception:
        save("build_failed")
        raise
    if shutil.disk_usage(ROOT).free < 512 * 1024**2:
        save("capacity_blocked")
        raise RuntimeError("Insufficient space to activate; running applications were preserved")
    try:
        save("activating")
        run([*command, "up", "-d", "--no-deps", "--no-build", *APPS])
        wait_ready()
        after = inspect_services()
        for service in ("market-db", "market-redis"):
            if before[service]["id"] != after[service]["id"]:
                raise RuntimeError("Infrastructure container changed unexpectedly")
        report["after"] = after
        save("activated_local_checks_passed")
    except Exception:
        save("rolling_back")
        try:
            run([*compose(source, rollback), "up", "-d", "--no-deps", "--no-build", *APPS])
            wait_ready(new_features=False)
            save("rolled_back")
        except Exception:
            save("rollback_failed")
            raise RuntimeError("Activation and rollback failed; inspect the saved report") from None
        raise RuntimeError("Activation failed; previous application images restored") from None
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--release")
    parser.add_argument("--sha256")
    args = parser.parse_args()
    try:
        result = (
            {"status": "preflight_passed", "services": preflight()}
            if args.inspect
            else activate(args.release, args.sha256)
        )
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        raise SystemExit(1) from None
