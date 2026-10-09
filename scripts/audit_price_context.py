"""Offline all-security validation; no broker requests or collection queue changes."""
import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from services.market_dashboard.price_context import price_context


def audit(database):
    started = time.monotonic()
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as db:
        universe = json.loads(db.execute(
            "SELECT payload FROM market_snapshots WHERE key='universe'"
        ).fetchone()[0])
        codes = {row["code"] for row in universe["rows"]}
        results = {}
        for key, payload in db.execute(
            "SELECT key,payload FROM market_snapshots WHERE key LIKE 'prices:%'"
        ):
            code = key.split(":", 1)[1]
            if code not in codes:
                continue
            try:
                result = price_context(json.loads(payload), code=code)
                results[code] = {
                    "status": result["status"], "summary": result["summary"],
                    "excluded_rows": result["quality"]["excluded_rows"],
                    "conflicting_dates": len(result["quality"]["conflicting_dates"]),
                    "long_gaps": len(result["quality"]["long_gaps"]),
                    "basis": result["basis"],
                }
            except Exception as exc:
                results[code] = {"status": "calculation_error", "error_type": type(exc).__name__}
        for code in codes - results.keys():
            results[code] = {"status": "not_collected"}
    return {
        "scope": "stored domestic listed securities; not upstream verification",
        "version": "observed-price-context-v1", "network_requests": 0,
        "codes": len(codes), "statuses": dict(Counter(r["status"] for r in results.values())),
        "seconds": round(time.monotonic() - started, 3), "securities": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="storage/public-market/market.db")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = audit(args.database)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "securities"}, ensure_ascii=False))
    return int(bool(report["statuses"].get("calculation_error")))


if __name__ == "__main__":
    raise SystemExit(main())
