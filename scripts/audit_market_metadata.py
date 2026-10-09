"""Audit provenance and period metadata for every populated public market metric."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

import httpx

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def decimal_value(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def audit_rows(rows, metric_definitions):
    violations = Counter()
    samples = []
    metrics = {
        key: {
            "label": value.get("label", key),
            "unit": value.get("unit"),
            "populated": 0,
            "with_fact": 0,
            "with_period": 0,
            "with_source": 0,
            "with_source_url": 0,
            "with_response_digest": 0,
            "with_ingested_at": 0,
            "available_at_known": 0,
            "sources": Counter(),
        }
        for key, value in metric_definitions.items()
    }
    by_market = defaultdict(lambda: {key: 0 for key in metric_definitions})
    seen_codes = set()

    def fail(kind, row, metric):
        violations[kind] += 1
        if len(samples) < 25:
            samples.append({"kind": kind, "code": row.get("code"), "metric": metric})

    for row in rows:
        code = row.get("code")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9A-Z]{6}", code):
            fail("invalid_security_code", row, None)
        elif code in seen_codes:
            fail("duplicate_security_code", row, None)
        seen_codes.add(code)
        market = row.get("market") or "UNKNOWN"

        for metric, definition in metric_definitions.items():
            value = row.get(metric)
            if value is None:
                continue
            stats = metrics[metric]
            stats["populated"] += 1
            by_market[market][metric] += 1

            fact = (row.get("facts") or {}).get(metric)
            if not isinstance(fact, dict):
                fail("missing_fact", row, metric)
                continue
            stats["with_fact"] += 1
            if decimal_value(fact.get("value")) != decimal_value(value):
                fail("fact_value_mismatch", row, metric)

            period = fact.get("period")
            if isinstance(period, str) and period.strip():
                stats["with_period"] += 1
            else:
                fail("missing_fact_period", row, metric)

            unit = fact.get("unit")
            expected_unit = definition.get("unit")
            if not isinstance(unit, str) or not unit.strip():
                fail("missing_fact_unit", row, metric)
            elif expected_unit and unit != expected_unit:
                fail("metric_unit_mismatch", row, metric)

            trace = fact.get("source_trace")
            if not isinstance(trace, dict):
                fail("missing_source_trace", row, metric)
                continue

            source = trace.get("source")
            if isinstance(source, str) and source.strip():
                stats["with_source"] += 1
                stats["sources"][source] += 1
            else:
                fail("missing_source", row, metric)

            source_period = trace.get("period")
            if not isinstance(source_period, str) or not source_period.strip():
                fail("missing_trace_period", row, metric)
            elif source_period != period:
                fail("fact_trace_period_mismatch", row, metric)

            source_url = trace.get("source_url")
            parsed_url = urlparse(source_url) if isinstance(source_url, str) else None
            if parsed_url and parsed_url.scheme == "https" and parsed_url.netloc:
                stats["with_source_url"] += 1
            else:
                fail("missing_or_invalid_source_url", row, metric)

            digest = trace.get("source_document_id")
            if isinstance(digest, str) and SHA256_RE.fullmatch(digest):
                stats["with_response_digest"] += 1
            else:
                fail("missing_or_invalid_response_digest", row, metric)

            if not isinstance(trace.get("unit"), str) or trace["unit"] != unit:
                fail("trace_unit_mismatch", row, metric)

            ingested_at = parse_timestamp(trace.get("ingested_at"))
            if ingested_at:
                stats["with_ingested_at"] += 1
            else:
                fail("missing_or_invalid_ingested_at", row, metric)

            if parse_timestamp(trace.get("available_at")):
                stats["available_at_known"] += 1

    for value in metrics.values():
        value["coverage_pct"] = round(value["populated"] / len(rows) * 100, 2) if rows else 0
        value["sources"] = dict(sorted(value["sources"].items()))
    market_row_counts = Counter(row.get("market") or "UNKNOWN" for row in rows)

    return {
        "passed": not violations,
        "listed_rows": len(rows),
        "unique_security_codes": len(seen_codes),
        "markets": {
            market: {"listed_rows": market_row_counts[market], "metrics": values}
            for market, values in sorted(by_market.items())
        },
        "metrics": metrics,
        "violations": dict(sorted(violations.items())),
        "violation_samples": samples,
    }


def fetch_snapshot(base_url, page_size=200, timeout=30.0):
    base_url = base_url.rstrip("/")
    with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as client:
        metrics_response = client.get(f"{base_url}/metrics")
        metrics_response.raise_for_status()
        metric_definitions = metrics_response.json()
        coverage_response = client.get(f"{base_url}/coverage")
        coverage_response.raise_for_status()
        coverage = coverage_response.json()

        rows = []
        expected_total = None
        for offset in range(0, int(coverage["total"]), page_size):
            response = client.post(
                f"{base_url}/screener",
                json={
                    "offset": offset,
                    "limit": page_size,
                    "market": "ALL",
                    "sort": "name",
                    "direction": "asc",
                },
            )
            response.raise_for_status()
            page = response.json()
            if expected_total is None:
                expected_total = page["total"]
            if page["total"] != expected_total:
                raise RuntimeError(
                    "Listed universe changed during the audit; rerun on a stable snapshot"
                )
            rows.extend(page["rows"])

        coverage_response = client.get(f"{base_url}/coverage")
        coverage_response.raise_for_status()
        coverage_after = coverage_response.json()

        if expected_total != coverage["total"] or len(rows) != coverage["total"]:
            raise RuntimeError(
                f"Incomplete listing scan: coverage={coverage['total']}, "
                f"screened={expected_total}, fetched={len(rows)}"
            )
        if coverage_after.get("total") != coverage["total"]:
            raise RuntimeError(
                "Listed universe changed during the audit; rerun on a stable snapshot"
            )

    return metric_definitions, coverage, coverage_after, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8210/api/market/v1",
        help="Market API base URL, including /api/market/v1",
    )
    parser.add_argument("--page-size", type=int, default=200, choices=range(1, 201))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        metric_definitions, coverage_before, coverage_after, rows = fetch_snapshot(
            args.base_url, args.page_size
        )
        report = {
            "audited_at": datetime.now(UTC).isoformat(),
            "api_base_url": args.base_url.rstrip("/"),
            "market_snapshot": {
                "listed_total": coverage_before.get("total"),
                "scope": coverage_before.get("scope"),
                "unavailable_markets": coverage_before.get("unavailable_markets", []),
                "coverage_at_scan_start": {
                    "updated_at": coverage_before.get("updated_at"),
                    "sources": coverage_before.get("sources", {}),
                    "metric_coverage": coverage_before.get("metric_coverage", {}),
                },
                "coverage_at_scan_end": {
                    "updated_at": coverage_after.get("updated_at"),
                    "sources": coverage_after.get("sources", {}),
                    "metric_coverage": coverage_after.get("metric_coverage", {}),
                },
            },
            "metadata_audit": audit_rows(rows, metric_definitions),
        }
    except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
        print(f"Market metadata audit failed: {type(exc).__name__}", file=sys.stderr)
        return 2

    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["metadata_audit"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
