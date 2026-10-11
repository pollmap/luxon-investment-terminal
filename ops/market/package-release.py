"""Package an allowlist of application sources, excluding runtime data and secrets."""
import hashlib
import os
import sys
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
output = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else root / "work/deploy-v2/hannun-source.tgz"
)
output.parent.mkdir(parents=True, exist_ok=True)
files = []
for folder in ["apps/web", "packages/core", "services/market_dashboard", "ops/market"]:
    for parent, dirs, names in os.walk(root / folder):
        dirs[:] = [
            d
            for d in dirs
            if d not in {"node_modules", "__pycache__", "test-results", "playwright-report"}
            and not d.startswith(".next")
        ]
        for name in names:
            p = Path(parent) / name
            if p.relative_to(root).as_posix() == "apps/web/public/stocks/ux-review.html":
                continue  # Local responsive-review controls are not a product route.
            if (name.startswith(".env") and name != ".env.example") or p.suffix in {".pyc", ".tsbuildinfo"}:
                continue
            files.append(p)
files += [root / p for p in ["package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "compose.market.yml", ".dockerignore"]]
with tarfile.open(output, "w:gz") as archive:
    for path in files:
        archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
print(hashlib.sha256(output.read_bytes()).hexdigest())
