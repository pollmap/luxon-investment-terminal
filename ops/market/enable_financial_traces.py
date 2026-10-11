"""Enable only future financial trace writes after independently verified readers.

This does not collect, generate, migrate, or delete financial facts. A successful
activation still needs a real new collection/read canary. The durable reader
requirement survives any failed activation because a worker may have written.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/srv/services/korea-stocks")
FORMAT = "financial-source-trace-sha256-v1"
ENV = "MARKET_SHARED_FINANCIAL_TRACES"
PROBE = (
    "import json,os; from pathlib import Path; "
    "from services.market_dashboard import fact_store; "
    "from services.market_dashboard.share_basis_bootstrap import read_eps_facts; "
    "p=Path(fact_store.__file__).with_name('storage_capabilities.json'); "
    "print(json.dumps({'capabilities':fact_store.storage_capabilities(),"
    "'manifest':json.loads(p.read_text()),"
    f"'enabled':os.getenv('{ENV}','0')=='1'}}))"
)


def plain_file(path, *, maximum=1048576):
    if (not path.is_file() or path.is_symlink() or path.stat().st_size > maximum
            or any(parent.is_symlink() for parent in path.parents)):
        raise RuntimeError("Plain bounded configuration required")
    return path.read_bytes()


def atomic(path, data):
    plain_file(path)
    pending = path.with_name(path.name + ".next")
    if pending.exists() or pending.is_symlink():
        raise RuntimeError("Pending configuration exists")
    with pending.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    pending.chmod(0o600)
    pending.replace(path)


def enable_compose(content):
    settings = re.findall(r"^[ \t]*" + ENV + r":.*$", content, re.M)
    marker = "      MARKET_RELEASE: ${RELEASE}\n"
    if (content.count(marker) != 1 or content.count("environment: &common") != 1
            or content.count("<<: *common") != 1):
        raise RuntimeError("Unexpected common environment")
    lines = content.splitlines()
    if lines.count("    environment: &common") != 1:
        raise RuntimeError("Unexpected common environment indentation")
    body = []
    for line in lines[lines.index("    environment: &common") + 1:]:
        if line.strip() and not line.startswith("      "):
            break
        body.append(line)
    if marker.rstrip("\n") not in body or (settings and settings[0] not in body):
        raise RuntimeError("Financial flag must belong to the common environment")
    if settings:
        if len(settings) != 1 or settings[0] not in (f"      {ENV}: '0'", f"      {ENV}: '1'"):
            raise RuntimeError("Unexpected financial setting")
        return content.replace(settings[0], f"      {ENV}: '1'", 1)
    return content.replace(marker, marker + f"      {ENV}: '1'\n", 1)


def read_contract(path):
    state = json.loads(plain_file(path, maximum=16384))
    required = state.get("required_read_formats")
    if (state.get("version") != 1 or not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)):
        raise RuntimeError("Invalid persistent storage contract")
    return state


def require_reader(path, original, expected, rollback):
    if read_contract(path) != original:
        raise RuntimeError("Persistent storage contract changed")
    if FORMAT in original["required_read_formats"]:
        return original
    state = {
        **original,
        "required_read_formats": [*original["required_read_formats"], FORMAT],
        "previous_rollback_floor": original.get("rollback_floor"),
        "rollback_floor": rollback,
        "financial_reader_release": expected,
        "financial_rollback_release": rollback,
        "financial_traces_activated_at": datetime.now(UTC).isoformat(),
    }
    atomic(path, (json.dumps(state, indent=2) + "\n").encode())
    return state


def checked_output(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        # Docker/config stderr can include credentials. Never copy it to output.
        raise RuntimeError("Reader verification command failed")
    return result.stdout.strip()


def image_id(expected):
    identity = checked_output([
        "docker", "image", "inspect", "--format", "{{.Id}}", "korea-stocks-api:" + expected,
    ])
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
        raise RuntimeError("Invalid reader image identity")
    return identity


def validate_capabilities(state, required):
    capability = state.get("capabilities", {})
    manifest = state.get("manifest", {})
    supplied = manifest.get("read_formats")
    if (capability.get("reference_write_env") != ENV
            or capability.get("reference_writes_default") is not False
            or capability.get("inline_financial_facts") is not True
            or FORMAT not in capability.get("read_formats", [])
            or manifest.get("version") != 1 or not isinstance(supplied, list)
            or any(not isinstance(item, str) for item in supplied)
            or not set(required) <= set(supplied)):
        raise RuntimeError("Financial reader or required capabilities missing")


def reader_state(service, expected, identity, required):
    name = "korea-stocks-" + service + "-1"
    info = checked_output([
        "docker", "inspect", "--format",
        "{{.Id}}|{{.State.Status}}|{{.Config.Image}}|{{.Image}}|"
        '{{index .Config.Labels "com.docker.compose.project"}}|'
        '{{index .Config.Labels "com.docker.compose.service"}}', name,
    ]).split("|")
    if len(info) != 6 or info[1:] != [
        "running", "korea-stocks-api:" + expected, identity, "korea-stocks", service,
    ]:
        raise RuntimeError("Unexpected active reader image")
    state = json.loads(checked_output(["docker", "exec", name, "python", "-c", PROBE]))
    validate_capabilities(state, required)
    return {"id": info[0], "enabled": state.get("enabled") is True}


def verify_rollback(rollback, required):
    release = ROOT / "releases" / rollback
    manifest = json.loads(plain_file(
        release / "services/market_dashboard/storage_capabilities.json", maximum=16384,
    ))
    identity = image_id(rollback)
    state = json.loads(checked_output([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "32", "--memory", "128m", "--entrypoint", "python",
        identity, "-c", PROBE,
    ]))
    validate_capabilities(state, required)
    if state.get("manifest") != manifest or state.get("enabled") is not False:
        raise RuntimeError("Rollback reader is not the verified default-off image")
    return identity


def compose_command(config, expected, *, enabled=None):
    env_file = ROOT / "shared/secrets/release.env"
    declared = re.findall(r"^RELEASE=([^\r\n]+)\r?$", plain_file(env_file).decode(), re.M)
    if declared != [expected]:
        raise RuntimeError("Persistent release does not match")
    command = [
        "docker", "compose", "--project-name", "korea-stocks", "--env-file", str(env_file),
        "-f", str(config),
    ]
    environment = {**os.environ, "RELEASE": expected}
    result = subprocess.run(
        [*command, "config", "--format", "json"], capture_output=True, text=True,
        timeout=20, env=environment,
    )
    if result.returncode:
        raise RuntimeError("Compose configuration validation failed")
    services = json.loads(result.stdout).get("services", {})
    for name in ("market-api", "market-worker"):
        service = services.get(name, {})
        values = service.get("environment", {})
        if (service.get("image") != "korea-stocks-api:" + expected
                or values.get("MARKET_RELEASE") != expected
                or (enabled is not None and values.get(ENV, "0") != str(int(enabled)))):
            raise RuntimeError("Rendered reader image, release or financial flag does not match")
    return command, environment


def restart_worker(command, environment):
    result = subprocess.run(
        [*command, "up", "-d", "--no-deps", "market-worker"], capture_output=True,
        timeout=90, env=environment,
    )
    if result.returncode:
        raise RuntimeError("Financial worker restart failed")


def enable(expected, rollback):
    if any(not re.fullmatch(r"[A-Za-z0-9-]{5,80}", value) for value in (expected, rollback)):
        raise ValueError("Invalid release")
    if expected == rollback:
        raise ValueError("Separate verified rollback release required")
    os.umask(0o077)
    lock_path = ROOT / ".deploy.lock"
    if any(path.is_symlink() for path in (lock_path, *lock_path.parents)):
        raise RuntimeError("Linked deployment lock rejected")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release = ROOT / "releases" / expected
        if (ROOT / "current").resolve() != release or release.is_symlink():
            raise RuntimeError("Active release changed")
        marker = ROOT / "shared/data/storage-format.json"
        previous = read_contract(marker)
        required = sorted(set(previous["required_read_formats"]) | {FORMAT})
        manifest = json.loads(plain_file(
            release / "services/market_dashboard/storage_capabilities.json", maximum=16384,
        ))
        # Validate release files as well as the bytes in both running containers.
        if (manifest.get("version") != 1
                or not set(required) <= set(manifest.get("read_formats", []))):
            raise RuntimeError("Release storage capability missing")
        identity = image_id(expected)
        api = reader_state("market-api", expected, identity, required)
        worker = reader_state("market-worker", expected, identity, required)
        rollback_identity = verify_rollback(rollback, required)
        config = release / "ops/market/compose.desktop.yml"
        original = plain_file(config)
        updated = enable_compose(original.decode()).encode()
        compose, environment = compose_command(config, expected)
        backup_dir = ROOT / "backups/config"
        if any(path.is_symlink() for path in (backup_dir, *backup_dir.parents)):
            raise RuntimeError("Linked backup path rejected")
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_dir / f"compose-before-financial-traces-{stamp}.yml"
        with backup.open("xb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        contract = require_reader(marker, previous, expected, rollback)
        result = {
            "release": expected, "format": FORMAT, "rollback_floor": contract["rollback_floor"],
            "verified_rollback": rollback, "rollback_image": rollback_identity,
            "backup": str(backup), "existing_rows_rewritten": 0,
            "canary": "awaiting_actual_collection_and_read",
        }
        if updated == original and worker["enabled"]:
            return {**result, "status": "already_enabled", "api_unchanged": True}
        try:
            atomic(config, updated)
            compose, environment = compose_command(config, expected, enabled=True)
            if image_id(expected) != identity:
                raise RuntimeError("Reader image tag changed")
            restart_worker(compose, environment)
            deadline = time.monotonic() + 20
            while True:
                try:
                    active = reader_state("market-worker", expected, identity, required)
                    if active["enabled"]:
                        break
                except (RuntimeError, subprocess.SubprocessError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("Financial worker readiness timeout")
                time.sleep(1)
            if reader_state("market-api", expected, identity, required)["id"] != api["id"]:
                raise RuntimeError("Protected API changed")
        except Exception:
            atomic(config, original)
            compose, environment = compose_command(config, expected)
            if image_id(expected) != identity:
                raise RuntimeError(
                    "Configuration restored; changed image prevents safe worker restart"
                ) from None
            restart_worker(compose, environment)
            # Required formats intentionally remain: a short-lived writer may
            # have persisted facts before failing. Disabling is not a downgrade.
            raise
        return {
            **result, "status": "enabled", "api_unchanged": True,
            "worker_changed": active["id"] != worker["id"],
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True)
    parser.add_argument("--rollback", required=True)
    args = parser.parse_args()
    print(json.dumps(enable(args.expected, args.rollback)))
