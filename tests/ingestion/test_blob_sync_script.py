import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_blob_sync_dry_run_validates_queue_without_token(tmp_path):
    payload = tmp_path / "payload.html"
    payload.write_text("<html>AAPL</html>", encoding="utf-8")
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "raw__sec__aapl.json").write_text(
        json.dumps(
            {
                "local_path": str(payload),
                "blob_key": "raw/sec/AAPL/payload.html",
                "content_type": "text/html",
                "metadata": {"ticker": "AAPL"},
            }
        ),
        encoding="utf-8",
    )
    (queue / "old.result.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        ["node", "scripts/blob-sync.mjs", "--queue-root", str(queue), "--dry-run"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(result.stdout)

    assert summary["mode"] == "dry_run"
    assert summary["scanned"] == 1
    assert summary["ready"] == 1
    assert summary["uploaded"] == 0
    assert summary["results"][0]["blob_key"] == "raw/sec/AAPL/payload.html"


def test_blob_sync_dry_run_reports_missing_local_file(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "missing.json").write_text(
        json.dumps(
            {
                "local_path": str(tmp_path / "missing.html"),
                "blob_key": "raw/sec/AAPL/missing.html",
                "content_type": "text/html",
                "metadata": {"ticker": "AAPL"},
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "scripts/blob-sync.mjs", "--queue-root", str(queue), "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    summary = json.loads(result.stdout)

    assert result.returncode == 3
    assert summary["errors"][0]["code"] == "missing_local_path"
