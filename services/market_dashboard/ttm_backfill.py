"""Collect verified period inputs for selected securities without resetting the market cursor.

Run with --collect to request OpenDART; the default only reports existing evidence.
Neither annual observations nor a successful HTTP response establish a verified TTM.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

from .analysis import trailing_twelve_months
from .dart_periods import DartPeriodCollector
from .store import now


def checkpoint_for(codes):
    return (
        "dart-period-progress:priority-"
        + hashlib.sha256(",".join(sorted(set(codes))).encode()).hexdigest()[:24]
    )


def inspect_ttm(store, codes):
    result = {}
    for code in codes:
        official = store.get("official:" + code, {})
        facts = official.get("period_facts", []) + official.get("xbrl_facts", [])
        verified = [f for f in facts if f.get("kind") == "duration" and f.get("period_verified")]
        by_basis = {}
        for basis in ("CFS", "OFS"):
            output = trailing_twelve_months([f for f in verified if f.get("basis") == basis])
            by_basis[basis] = {
                "status": output["status"],
                "rows": output["rows"],
                "accounts": len(output["rows"]),
            }
        result[code] = {
            "verified_duration_inputs": len(verified),
            "observed_periods": sorted(
                {(f.get("period_start", ""), f.get("period_end", "")) for f in verified}
            ),
            "account_ttm_ready": any(b["accounts"] for b in by_basis.values()),
            "by_basis": by_basis,
            "per_share_ttm_status": "not_assessed",
            "per_share_note": (
                "Monetary duration totals do not verify EPS or split-adjusted per-share inputs."
            ),
        }
    return result


async def backfill(source, codes, *, max_batches=12, collect=False, pause_seconds=1):
    codes = sorted(set(codes))
    if not codes or len(codes) > 100 or any(not re.fullmatch(r"[A-Z0-9]{6}", c) for c in codes):
        raise ValueError("Select 1 to 100 valid security codes")
    if not 1 <= max_batches <= 64:
        raise ValueError("Batch budget must be between 1 and 64")
    known = set(source.store.listed_codes())
    if not set(codes).issubset(known):
        raise ValueError("Unknown listed security")
    key = checkpoint_for(codes)
    before = inspect_ttm(source.store, codes)
    attempts = []
    if collect:
        collector = DartPeriodCollector(source)
        for _ in range(max_batches):
            attempt = await collector.collect(codes, checkpoint_key=key)
            attempts.append(attempt)
            # A complete scan, failure, exhausted daily budget, or missing identity
            # ends this run. Reinvocation resumes the same independent checkpoint.
            if attempt["status"] != "collecting":
                break
            if len(attempts) < max_batches:
                await asyncio.sleep(max(0, pause_seconds))
    after = inspect_ttm(source.store, codes)
    return {
        "checked_at": now(),
        "mode": "collect" if collect else "audit",
        "checkpoint": key,
        "collection_attempts": attempts,
        "collection_state": source.store.get(key, {}),
        "account_ttm_ready_count": sum(v["account_ttm_ready"] for v in after.values()),
        "codes": codes,
        "before": before,
        "after": after,
        "complete": all(v["account_ttm_ready"] for v in after.values())
        and (not collect or (attempts and attempts[-1]["status"] in ("ready", "current"))),
        "scope": (
            "Dated monetary accounts only; "
            "not EPS, prices, dividends or public release verification"
        ),
    }


async def main_async(args):
    from .authenticated_sources import AuthenticatedSources
    from .providers import PublicProvider
    from .runtime import load_worker_credentials
    from .store import Store

    load_worker_credentials()
    store = Store()
    provider = PublicProvider(store)
    try:
        report = await backfill(
            AuthenticatedSources(provider),
            args.codes.split(","),
            max_batches=args.max_batches,
            collect=args.collect,
        )
    finally:
        await provider.client.aclose()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "mode",
                    "account_ttm_ready_count",
                    "codes",
                    "complete",
                    "collection_attempts",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["complete"] else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", default="005930,000660")
    parser.add_argument("--max-batches", type=int, default=12)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--output", required=True)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
