"""Deploy the prepared public-market source using the existing authorized SSH host."""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = "root@62.171.141.206"


def main():
    raise SystemExit(
        "This legacy deployer targets the retired /srv/hannun Compose project. "
        "Use scripts/deploy-market-backend-patch.py with explicit --release and --expected values."
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", default=datetime.now(UTC).strftime("%Y%m%d-%H%M%S-market"))
    parser.add_argument(
        "--identity", type=Path, default=Path.home() / ".ssh/openclaw_vmi3138243_ed25519"
    )
    parser.add_argument(
        "--inspect", action="store_true", help="Read-only connection/capacity/ownership check"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{5,79}", args.release):
        raise SystemExit("Invalid release identifier")
    if not args.identity.is_file():
        raise SystemExit("Existing SSH identity file was not found")
    ssh = [
        "ssh",
        "-i",
        str(args.identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ServerAliveInterval=15",
        HOST,
    ]
    server_script = (ROOT / "ops/market/activate_release.py").read_bytes()
    directory = ROOT / "work/releases" / args.release
    directory.mkdir(parents=True, exist_ok=False)

    def execute(command, **options):
        result = subprocess.run(command, capture_output=True, **options)
        if result.returncode:
            # SSH errors contain no credentials. Do not print remote build logs.
            message = (
                result.stderr.decode(errors="replace").strip()
                if command[0] in ("ssh", "scp")
                else ""
            )
            raise RuntimeError(
                message or result.stdout.decode(errors="replace").strip() or "Command failed"
            )
        return result.stdout

    try:
        preflight = execute([*ssh, "python3 - --inspect"], input=server_script, timeout=45)
        (directory / "preflight.json").write_bytes(preflight)
        if args.inspect:
            print(preflight.decode())
            return
        archive = directory / "source.tgz"
        execute(
            [
                str(ROOT / ".venv/Scripts/python.exe"),
                str(ROOT / "ops/market/package-release.py"),
                str(archive),
            ],
            timeout=120,
        )
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        execute([*ssh, "install -d -m 700 /srv/hannun/incoming"], timeout=30)
        execute(
            [
                "scp",
                "-i",
                str(args.identity),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                str(archive),
                f"{HOST}:/srv/hannun/incoming/{args.release}.tgz",
            ],
            timeout=300,
        )
        result = execute(
            [*ssh, f"python3 - --release {args.release} --sha256 {digest}"],
            input=server_script,
            timeout=2700,
        )
        (directory / "deployment.json").write_bytes(result)
        print(result.decode())
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        report = {
            "release": args.release,
            "status": "failed_or_unconfirmed",
            "reason": str(exc),
            "note": "Transport timeout leaves remote deployment status unconfirmed.",
        }
        (directory / "attempt.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
