"""Private, quiet-on-success health checks. Never prunes files or sends messages."""

import json
import os
import shutil
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

try:
    from . import backup, retain_releases
except ImportError:  # Existing systemd unit runs this file directly.
    import backup
    import retain_releases

ROOT = Path("/srv/services/korea-stocks")


def retention_status(containers):
    # Every two minutes: metadata only. No payload walks, hashes or deletion.
    state = retain_releases.inventory(measure_bytes=False, containers=containers)
    return {
        "state": "blocked_offsite_verification" if state["candidates"] else "protected",
        "candidates": [p["name"] for p in state["candidates"]],
        "protected": state["protected_reasons"],
        "blocked": state["blocked"],
        "inspection": state["inspection"],
        "automatic_removal": False,
    }


def collect_report(result, issues=None, containers=None, now=None):
    issues = list(issues or [])
    now = now or datetime.now(UTC)
    usage = shutil.disk_usage(ROOT)
    fraction = usage.used / usage.total
    if fraction >= 0.8:
        issues.append("disk_critical" if fraction >= 0.9 else "disk_warning")
    broker = result.get("providers", {}).get("provider-toss", {})
    if broker.get("status") == "error":
        issues.append("toss_collection_error")
    static = None
    try:
        static = retain_releases.static_web_reference()
    except (OSError, ValueError, KeyError, TypeError):
        issues.append("static_web_activation_invalid")
    retention = {"state": "blocked", "reason": "container_inspection_unavailable"}
    try:
        containers = retain_releases.inspect_containers() if containers is None else containers
        for container in containers:
            service = container["service"]
            # Stopped Node is retained for rollback. Only verified static
            # activation (manifest/hash/public pointer) can replace it.
            if container["state"] != "running" and not (
                service == "market-web" and container["state"] == "exited" and static
            ):
                issues.append(service + "_stopped")
        try:
            retention = retention_status(containers)
        except (OSError, ValueError, KeyError, TypeError):
            retention = {"state": "blocked", "reason": "protected_reference_invalid"}
            issues.append("release_retention_blocked")
    except Exception as error:
        issues.append("containers_" + type(error).__name__)
    try:
        backups = backup.backup_health(ROOT / "backups", now)
        if backups["state"] != "ready":
            issues.append("backup_overdue")
        recovery_state = backups["offsite_recovery"]["state"]
        if recovery_state == "recheck_due":
            issues.append("backup_offsite_recheck_due")
        elif recovery_state == "verification_expired":
            issues.append("backup_offsite_reverification_required")
        elif recovery_state != "verified":
            issues.append("backup_offsite_restore_unverified")
        if backups.get("last_job_state") == "blocked_capacity":
            issues.append("backup_capacity_blocked")
        elif backups.get("last_job_state") == "failed":
            issues.append("backup_failed")
    except (OSError, ValueError):
        backups = {"state": "blocked", "reason": "backup_inventory_unavailable"}
        issues.append("backup_inventory_unavailable")
    return {
        "checked_at": now.isoformat(),
        "issues": sorted(set(issues)),
        "release": result.get("release"),
        "disk_used_fraction": fraction,
        "disk_free_bytes": usage.free,
        "static_web": static,
        "release_retention": retention,
        "backup": backups,
    }


def save_report(report):
    destination = ROOT / "shared/monitor.json"
    if destination.is_symlink():
        raise ValueError("Unexpected monitor destination")
    try:
        previous = (
            json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else {}
        )
    except (ValueError, OSError):
        previous = {}
    descriptor, temporary = tempfile.mkstemp(
        prefix=".monitor-", suffix=".partial", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(report, output)
        Path(temporary).replace(destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if report["issues"] and report["issues"] != previous.get("issues"):
        print(json.dumps(report))


def main():
    result, issues = {}, []
    try:
        key = (ROOT / "shared/secrets/internal.key").read_text().strip()
        request = urllib.request.Request(
            "http://127.0.0.1:8211/internal/market-data/health", headers={"X-Market-Key": key}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
    except Exception as error:
        issues.append("api_" + type(error).__name__)
    report = collect_report(result, issues)
    save_report(report)
    return bool(report["issues"])


if __name__ == "__main__":
    raise SystemExit(main())
