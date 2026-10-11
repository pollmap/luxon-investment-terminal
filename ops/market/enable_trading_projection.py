"""Enable summary-only trading writes after both production readers are verified.

Never remove the durable reader requirement after an attempted activation: the
worker may already have persisted references even if a later check fails.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/srv/services/korea-stocks")
FORMAT = "toss-trading-reference-v1"
ENV = "MARKET_COMPACT_TRADING_PROJECTIONS"


def atomic(path, data):
    if path.is_symlink():
        raise RuntimeError("Linked configuration rejected")
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
    pattern = r"^      " + ENV + r":.*$"
    existing = re.findall(pattern, content, re.M)
    if existing:
        if len(existing) != 1 or existing[0] not in (f"      {ENV}: '0'", f"      {ENV}: '1'"):
            raise RuntimeError("Unexpected projection setting")
        return content.replace(existing[0], f"      {ENV}: '1'", 1)
    marker = "      MARKET_RELEASE: ${RELEASE}\n"
    if (
        content.count(marker) != 1
        or "environment: &common" not in content
        or "<<: *common" not in content
    ):
        raise RuntimeError("Unexpected common environment")
    return content.replace(marker, marker + f"      {ENV}: '1'\n", 1)


def require_reader(path, expected):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 16384:
        raise RuntimeError("Existing persistent storage contract required")
    state = json.loads(path.read_text())
    required = state.get("required_read_formats")
    if (
        state.get("version") != 1
        or not isinstance(required, list)
        or any(not isinstance(item, str) for item in required)
    ):
        raise RuntimeError("Invalid persistent storage contract")
    if FORMAT in required:
        return state
    result = {
        **state,
        "required_read_formats": [*required, FORMAT],
        "previous_rollback_floor": state.get("rollback_floor"),
        "rollback_floor": expected,
        "trading_projection_activated_at": datetime.now(UTC).isoformat(),
    }
    atomic(path, (json.dumps(result, indent=2) + "\n").encode())
    return result


def reader_state(service, expected):
    name = "korea-stocks-" + service + "-1"
    identity = (
        subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Id}}|{{.State.Status}}|{{.Config.Image}}|"
                '{{index .Config.Labels "com.docker.compose.project"}}|'
                '{{index .Config.Labels "com.docker.compose.service"}}',
                name,
            ],
            text=True,
        )
        .strip()
        .split("|")
    )
    if len(identity) != 5 or identity[1:] != [
        "running",
        "korea-stocks-api:" + expected,
        "korea-stocks",
        service,
    ]:
        raise RuntimeError("Unexpected active reader")
    command = (
        "import json,os; "
        "from services.market_dashboard.trading_projection import "
        "FORMAT,read_financing,read_flows,read_short_selling; "
        f'print(json.dumps({{"format":FORMAT,"enabled":os.getenv("{ENV}","0")=="1"}}))'
    )
    result = json.loads(
        subprocess.check_output(
            ["docker", "exec", name, "python", "-c", command], text=True, timeout=15
        )
    )
    if result.get("format") != FORMAT:
        raise RuntimeError("Production trading reader not verified")
    return {"id": identity[0], "enabled": result["enabled"]}


def compose_command(config, expected):
    env_file = ROOT / "shared/secrets/release.env"
    if env_file.is_symlink() or not env_file.is_file():
        raise RuntimeError("Plain release environment required")
    declared = re.findall(r"^RELEASE=([^\r\n]+)$", env_file.read_text(), re.M)
    if declared != [expected]:
        raise RuntimeError("Persistent release environment does not match active release")
    command = [
        "docker",
        "compose",
        "--project-name",
        "korea-stocks",
        "--env-file",
        str(env_file),
        "-f",
        str(config),
    ]
    # A caller's shell must not override the reviewed persistent release.
    environment = {**os.environ, "RELEASE": expected}
    result = subprocess.run(
        [*command, "config", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )
    if result.returncode:
        raise RuntimeError("Compose configuration validation failed")
    services = json.loads(result.stdout).get("services", {})
    for name in ("market-api", "market-worker"):
        service = services.get(name, {})
        if (
            service.get("image") != "korea-stocks-api:" + expected
            or service.get("environment", {}).get("MARKET_RELEASE") != expected
        ):
            raise RuntimeError("Rendered reader image or release does not match")
    return command, environment


def enable(expected):
    if not re.fullmatch("[A-Za-z0-9-]{5,80}", expected):
        raise ValueError("Invalid release")
    os.umask(0o077)
    with (ROOT / ".deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release = (ROOT / "current").resolve()
        if release != ROOT / "releases" / expected:
            raise RuntimeError("Active release changed")
        api = reader_state("market-api", expected)
        worker = reader_state("market-worker", expected)
        capability = json.loads(
            (release / "services/market_dashboard/storage_capabilities.json").read_text()
        )
        if capability.get("version") != 1 or FORMAT not in capability.get("read_formats", []):
            raise RuntimeError("Release storage capability missing")
        config = release / "ops/market/compose.desktop.yml"
        if config.is_symlink():
            raise RuntimeError("Linked compose rejected")
        original = config.read_bytes()
        updated = enable_compose(original.decode()).encode()
        compose, environment = compose_command(config, expected)
        backup_dir = ROOT / "backups/config"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_dir / f"compose-before-trading-projection-{stamp}.yml"
        with backup.open("xb") as stream:
            stream.write(original)
        contract = require_reader(ROOT / "shared/data/storage-format.json", expected)
        if updated == original and worker["enabled"]:
            return {
                "status": "already_enabled",
                "release": expected,
                "rollback_floor": contract["rollback_floor"],
            }
        try:
            atomic(config, updated)
            compose, environment = compose_command(config, expected)
            subprocess.run(
                [*compose, "up", "-d", "--no-deps", "market-worker"],
                check=True,
                timeout=90,
                env=environment,
            )
            deadline = time.monotonic() + 20
            while True:
                active = reader_state("market-worker", expected)
                if active["enabled"]:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Projection worker readiness timeout")
                time.sleep(1)
            if reader_state("market-api", expected)["id"] != api["id"]:
                raise RuntimeError("Protected API changed")
            with urllib.request.urlopen(
                "http://127.0.0.1:8211/api/market/v1/securities/005930/financing?period=month",
                timeout=15,
            ) as response:
                if json.load(response).get("code") != "005930":
                    raise RuntimeError("Trading reader smoke failed")
        except Exception:
            atomic(config, original)
            subprocess.run(
                [*compose, "up", "-d", "--no-deps", "market-worker"],
                check=True,
                timeout=90,
                env=environment,
            )
            raise
        return {
            "status": "enabled",
            "release": expected,
            "format": FORMAT,
            "api_unchanged": True,
            "worker_changed": active["id"] != worker["id"],
            "rollback_floor": contract["rollback_floor"],
            "backup": str(backup),
            "raw_rows_deleted": 0,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True)
    print(json.dumps(enable(parser.parse_args().expected)))
