"""Recover verified periods from existing raw DART archives without network calls."""

import argparse
import json
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from services.market_dashboard.dart_periods import normalize_period_accounts
from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.raw_archive import read_original_verified
from services.market_dashboard.store import Store, now


def replay(store, apply=False):
    result = {
        "audited_at": now(),
        "applied": apply,
        "archives": 0,
        "valid_archives": 0,
        "companies": 0,
        "facts": 0,
        "errors": dict(),
    }
    companies = defaultdict(list)
    errors = Counter()
    periods = Counter()
    for path in (store.root / "raw" / "dart").glob("*.json"):
        try:
            with path.open(encoding="utf-8") as file:
                header = file.read(1200)
        except (OSError, UnicodeError):
            errors["archive_metadata_unreadable"] += 1
            continue
        if "fnlttMultiAcnt.json" not in header:
            continue
        result["archives"] += 1
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict):
                raise ValueError("Source metadata must be an object")
        except (OSError, ValueError, UnicodeError):
            errors["archive_metadata_invalid"] += 1
            continue
        if envelope.get("sha256") != path.stem:
            errors["archive_hash_mismatch"] += 1
            continue
        try:
            # Both legacy UTF-8 envelopes and verified binary/gzip archives.
            # Every surviving copy and its metadata must agree before replay.
            raw = read_original_verified(path.parent, path.stem)
        except (OSError, ValueError, UnicodeError):
            errors["archive_hash_mismatch"] += 1
            continue
        parts = urlsplit(envelope.get("url", ""))
        if parts.hostname != "opendart.fss.or.kr" or parts.path != "/api/fnlttMultiAcnt.json":
            errors["source_mismatch"] += 1
            continue
        source_url = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(
                    [
                        (k, v)
                        for k, v in parse_qsl(parts.query)
                        if k in ("corp_code", "bsns_year", "reprt_code")
                    ]
                ),
                "",
            )
        )
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("list", []), list):
                raise ValueError("Invalid DART response shape")
        except (ValueError, UnicodeError):
            errors["archive_payload_invalid"] += 1
            continue
        if payload.get("status") != "000":
            errors["source_response_not_ready"] += 1
            continue
        result["valid_archives"] += 1
        for row in payload.get("list", []):
            if not isinstance(row, dict):
                errors["source_row_invalid"] += 1
                continue
            corp = str(row.get("corp_code") or "")
            code = str(row.get("stock_code") or "")
            if not re.fullmatch(r"\d{8}", corp) or not re.fullmatch(r"[A-Z0-9]{6}", code):
                errors["identity_missing"] += 1
                continue
            for fact in normalize_period_accounts([row], source_url, path.stem):
                fact["company_id"] = corp
                fact["source_trace"]["received_at"] = envelope.get("received_at")
                companies[(code, corp)].append(fact)
    facts_db = FactStore(store) if apply else None
    for (code, corp), facts in companies.items():
        if code not in store.listed_codes():
            errors["security_not_in_saved_universe"] += 1
            continue
        official = store.get("official:" + code, {})
        if official.get("company", {}).get("corp_code") not in (None, corp):
            errors["corporation_mismatch"] += 1
            continue
        publications = {
            str(f.get("receipt") or f.get("rcept_no") or ""): f.get("date")
            for f in store.get("filings:" + code, {}).get("rows", [])
        }
        for fact in facts:
            fact["publication_date"] = publications.get(fact["filing_id"]) or fact.get(
                "publication_date"
            )
            fact["source_trace"]["available_at"] = fact["publication_date"]

        def identity(f):
            return (
                f["account_id"],
                f["basis"],
                f["period_start"],
                f["period_end"],
                f.get("filing_id") or f.get("source_trace", {}).get("filing_id"),
            )

        unique = {identity(f): f for f in facts}
        for f in unique.values():
            periods[f["period_start"] + "/" + f["period_end"]] += 1
        result["companies"] += 1
        result["facts"] += len(unique)
        if apply:
            facts_db.append(corp, list(unique.values()))
            official = store.get("official:" + code, {}, use_redis=False)
            existing = {identity(f): f for f in official.get("period_facts", [])}
            existing.update(unique)
            official["period_facts"] = list(existing.values())
            store.put("official:" + code, official)
    result["errors"] = dict(errors)
    result["periods"] = dict(periods)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", default="docs/dart-archive-replay-20260928.json")
    args = parser.parse_args()
    store = Store()
    if args.apply and store.engine.dialect.name == "sqlite" and store.engine.url.database:
        database = Path(store.engine.url.database).resolve()
        if shutil.disk_usage(database.parent).free < database.stat().st_size + 768 * 1024**2:
            raise SystemExit("Insufficient backup space")
        backup = (
            store.root / "backups" / ("before-period-replay-" + now().replace(":", "-") + ".sqlite")
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise SystemExit("Backup already exists")
        with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
            source.backup(destination, pages=1000)
    result = replay(store, apply=args.apply)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
