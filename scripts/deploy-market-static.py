"""Deploy a locally verified static build through the existing scoped VPS helper."""

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/srv/services/korea-stocks/incoming"


def run(args, timeout=120):
    result = subprocess.run(args, capture_output=True, timeout=timeout, check=True)
    return result.stdout.decode("utf-8", errors="replace").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", args.release):
        raise ValueError("Invalid release")
    source = ROOT / "apps/web/out"
    manifest = json.loads((source / "build-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("release") != args.release or manifest.get("profile") != "static":
        raise ValueError("Build release differs")
    run(["node", str(ROOT / "scripts/check-market-static.mjs")])
    folder = ROOT / "work/releases" / args.release
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "static-web.tar.gz"
    paths = [row["path"] for row in manifest["files"]] + ["build-manifest.json"]
    with tarfile.open(archive, "w:gz") as tar:
        for relative in sorted(paths):
            path = (source / relative).resolve()
            if not path.is_relative_to(source.resolve()) or path.is_symlink():
                raise ValueError("Unsafe asset")
            tar.add(path, arcname=relative, recursive=False)
    digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    destination = REMOTE + "/static-" + args.release + ".tar.gz"
    run(["scp", str(archive), "chanhee-vps:" + destination])
    run(["scp", str(ROOT / "ops/market/promote_static_web.py"),
         "chanhee-vps:" + REMOTE + "/promote_static_web.py"])
    command = ["python3", REMOTE + "/promote_static_web.py", "--expected-release",
               args.release, "--archive", destination, "--sha256", digest]
    dry_run = run(["ssh", "-o", "BatchMode=yes", "chanhee-vps",
                   shlex.join(command + ["--dry-run"])])
    result = {"release": args.release, "archive_sha256": digest, "dry_run": dry_run,
              "applied": args.apply, "files": len(paths)}
    if args.apply:
        result["result"] = run(["ssh", "-o", "BatchMode=yes", "chanhee-vps",
                                shlex.join(command)])
    (folder / "static-deployment.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
