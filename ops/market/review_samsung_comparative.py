"""One reviewed ordinary-share context; default offline dry-run, never fetches DART.

No source facts/publication/split dates are edited. Local evidence is optional;
no third-party archive or operational snapshot is distributed with this tool.
Database writes require explicit apply, reviewer, and three pre-reviewed digests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.market_dashboard.fundamental_metrics import (  # noqa: E402
    calculate_fundamental_metrics,
)
from services.market_dashboard.share_basis import aligned_facts  # noqa: E402
from services.market_dashboard.split_units import (  # noqa: E402
    SHARES,
    factor_after,
    normalize_fact,
    valid_history,
)
from services.market_dashboard.store import next_snapshot_revision  # noqa: E402

CODE, COMPANY, FILING = "005930", "00126380", "20260814003699"
START, END, UNIT_DAY = "2025-01-01", "2025-06-30", "2026-06-30"
BASIS_KEY, OFFICIAL_KEY = "share-basis:005930", "official:005930"
REVIEW_KEY = "share-unit-review:005930:20260814003699:CFS:20250101:20250630:v112"
NOTE_HASH = "0ab63e7e0ea445e37a88b5aebab621cad48706ed74737386c7bbcef56915e0f9"
XBRL_HASH = "95504ab7d7b3bb213428329a2b455ae2d2d7c36190b8308bebffb322c3dce427"
FACT_ID = "2524d46e2864258fbe1e74c2954828e847f604353556974981955525681e6b01"
PINNED = {
    "005930-6.html": "9914a33865c2de2f929622689350342cf044cbbb9a17ae6c4498ddbb46c10048",
    "005930-7.html": "df2a8793a85b97115adb06a7c7f0b267d752360620c24e1a58e43595861ab2ae",
    "005930-48.html": NOTE_HASH,
}


def encode(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def local(value):
    return value.rsplit("}", 1)[-1]


def target(row):
    return all(
        row.get(k) == v
        for k, v in {
            "filing_id": FILING,
            "basis": "CFS",
            "period_start": START,
            "period_end": END,
        }.items()
    )


def parse_note(raw):
    soup = BeautifulSoup(raw, "html.parser")
    label = "가중평균유통보통주식수(단위: 주)"
    matches = []
    for table in soup.find_all("table"):
        if label not in table.get_text(" ", strip=True):
            continue
        previous = table.find_previous("table")
        if not previous or not previous.get_text(" ", strip=True).startswith("전반기"):
            continue
        rows = [
            [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            for tr in table.find_all("tr")
        ]
        if ["", "", "보통주", "우선주"] not in rows or [
            "",
            "",
            "3개월",
            "누적",
            "3개월",
            "누적",
        ] not in rows:
            raise ValueError("Ordinary/preferred cumulative column boundaries differ")
        chosen = [r for r in rows if r and r[0] == label]
        if len(chosen) != 1 or chosen[0] != [label, "5,881,153,521", "5,903,847,141", "", ""]:
            raise ValueError("Reviewed ordinary cumulative shares differ")
        matches.append(Decimal(chosen[0][2].replace(",", "")))
    if matches != [Decimal("5903847141")]:
        raise ValueError("Comparative ordinary-share note is ambiguous")
    return matches[0]


def archive_evidence(path):
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if (
            len(members) != 4
            or {m.name for m in members} != {"sources.json", *PINNED}
            or any(not m.isfile() or m.size > 1024 * 1024 for m in members)
        ):
            raise ValueError("Archive membership/size differs from the reviewed three documents")
        sources = json.load(archive.extractfile("sources.json"))
        if len(sources) != 3 or {r.get("file") for r in sources} != set(PINNED):
            raise ValueError("Source manifest differs")
        note = None
        for row in sources:
            raw = archive.extractfile(row["file"]).read()
            parsed = urlparse(row.get("url", ""))
            if (
                hashlib.sha256(raw).hexdigest() != PINNED[row["file"]]
                or row.get("sha256") != PINNED[row["file"]]
                or row.get("code") != CODE
                or parsed.scheme != "https"
                or parsed.netloc != "dart.fss.or.kr"
                or parse_qs(parsed.query).get("rcpNo") != [FILING]
            ):
                raise ValueError("Archive hash/issuer/filing evidence mismatch")
            if row["file"] == "005930-48.html":
                if row.get("title") != "23. 주당이익 (연결)":
                    raise ValueError("Note is not the reviewed consolidated EPS note")
                note = {"shares": str(parse_note(raw)), "source_url": row["url"]}
        return {**note, "document_hashes": PINNED.copy(), "manifest_sha256": digest(sources)}


def contains_fixture(value):
    """Fixture evidence never authorizes a complete operational review."""
    if isinstance(value, dict):
        return value.get("fixture_non_production") is True or any(
            contains_fixture(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_fixture(item) for item in value)
    return False


def validate_inputs(basis, official, evidence, complete):
    if complete and any(contains_fixture(value) for value in (basis, official, evidence)):
        raise ValueError("fixture_non_production cannot authorize a complete review")
    if (
        not valid_history(basis)
        or basis.get("security_code") != CODE
        or basis.get("company_id") != COMPANY
        or basis.get("end", "") < UNIT_DAY
    ):
        raise ValueError("Current reviewed Samsung ordinary share basis required")
    if factor_after(basis, UNIT_DAY) != Decimal(1):
        raise ValueError("New split after the reviewed unit date requires renewed review")
    intervals = [
        r
        for r in basis.get("unchanged_intervals", [])
        if r.get("verified") is True
        and r.get("no_split_interval") is True
        and r.get("security_code") == CODE
        and r.get("company_id") == COMPANY
        and r.get("start") == "2019-01-01"
        and r.get("end") == UNIT_DAY
        and r.get("source_trace", {}).get("sha256")
    ]
    if len(intervals) != 1:
        raise ValueError("Existing bounded post-split interval is missing or ambiguous")
    facts = official.get("xbrl_facts", [])
    shares = [
        f for f in facts if target(f) and local(f.get("account_id", "")) == "WeightedAverageShares"
    ]
    if len(shares) != 1:
        raise ValueError("Expected one exact ordinary cumulative share fact")
    fact = shares[0]
    dims = {local(k): local(v) for k, v in fact.get("dimensions", {}).items()}
    if (
        fact.get("company_id") != COMPANY
        or fact.get("unit") != SHARES
        or fact.get("currency") is not None
        or fact.get("fact_id") != FACT_ID
        or fact.get("published_at") not in ("20260814", "2026-08-14")
        or dims
        != {
            "ClassesOfShareCapitalAxis": "OrdinarySharesMember",
            "ConsolidatedAndSeparateFinancialStatementsAxis": "ConsolidatedMember",
        }
        or fact.get("source_trace", {}).get("sha256") != XBRL_HASH
        or Decimal(str(fact.get("value"))) != Decimal(evidence["shares"])
        or fact.get("split_normalization")
        or complete
        and fact.get("period_verified") is not True
    ):
        raise ValueError("Source fact does not match the reviewed period/unit/class/value")
    ocf = [
        f
        for f in facts
        if target(f) and local(f.get("account_id", "")) == "CashFlowsFromUsedInOperatingActivities"
    ]
    if len(ocf) != 1 or ocf[0].get("company_id") != COMPANY:
        raise ValueError("Exact existing company cash-flow fact required")
    return fact


def proposed_review(evidence):
    return {
        "verified": True,
        "security_code": CODE,
        "company_id": COMPANY,
        "share_class": "ordinary",
        "filing_id": FILING,
        "basis": "CFS",
        "period_start": START,
        "period_end": END,
        "unit_as_of": UNIT_DAY,
        "review_id": REVIEW_KEY,
        "source_trace": {
            "source": "dart_issuer_report",
            "company_id": COMPANY,
            "filing_id": FILING,
            "available_at": "2026-08-14",
            "sha256": NOTE_HASH,
            "source_url": evidence["source_url"],
        },
    }


def semantic(row):
    keys = (
        "metric",
        "company_id",
        "basis",
        "period_start",
        "period_end",
        "frequency",
        "published_at",
        "exact_value",
        "numerator",
        "weighted_shares",
        "attribution",
        "formula",
    )
    return encode({k: row.get(k) for k in keys})


def metrics(official, basis):
    facts = [
        {k: v for k, v in f.items() if k != "per_share_basis"}
        for f in official.get("xbrl_facts", [])
    ]
    return calculate_fundamental_metrics(
        aligned_facts(facts, basis), official.get("cashflow_reviews", [])
    )


def plan(basis, official, evidence, *, complete=False, stamp=None, reviewer=None):
    before_hash = digest(basis)
    official_hash = digest(official)
    fact = validate_inputs(basis, official, evidence, complete)
    review = proposed_review(evidence)
    review_hash = digest(review)
    existing = [r for r in basis.get("fact_unit_reviews", []) if target(r)]
    if len(existing) > 1:
        raise ValueError("Duplicate period reviews require separate investigation")
    if existing:
        normalized = deepcopy(existing[0])
        normalized.get("source_trace", {}).pop("reviewed_at", None)
        normalized.pop("reviewed_by", None)
        if normalized != review:
            raise ValueError("Existing review differs; never overwrite it")
        updated = deepcopy(basis)
    else:
        updated = deepcopy(basis)
        added = deepcopy(review)
        if stamp:
            added["source_trace"]["reviewed_at"] = stamp
            added["reviewed_by"] = reviewer
        updated.setdefault("fact_unit_reviews", []).append(added)
    old_fact = normalize_fact(fact, basis)
    new_fact = normalize_fact(fact, updated)
    if (
        new_fact.get("split_normalization", {}).get("factor") != "1"
        or new_fact["value"] != str(Decimal(str(fact["value"])))
        or new_fact["published_at"] != fact["published_at"]
    ):
        raise ValueError("Reviewed share normalization changed units/value/publication")
    before = metrics(official, basis)
    after = metrics(official, updated)
    prior_rows = {semantic(r) for r in before["rows"]}
    new_rows = {semantic(r) for r in after["rows"]}
    preserved = prior_rows <= new_rows
    latest = [
        r
        for r in after["rows"]
        if r.get("metric") == "OCF"
        and r.get("basis") == "CFS"
        and r.get("frequency") == "ttm"
        and r.get("period_end") == "2026-06-30"
    ]
    if complete and (not preserved or not latest):
        raise ValueError(
            "Full calculation did not preserve old results and establish the target OCF TTM"
        )
    if digest(basis) != before_hash or digest(official) != official_hash:
        raise ValueError("Pure review unexpectedly mutated source inputs")
    report = {
        "code": CODE,
        "scope": {
            "filing": FILING,
            "basis": "CFS",
            "start": START,
            "end": END,
            "unit_as_of": UNIT_DAY,
            "share_class": "ordinary",
        },
        "basis_sha256": before_hash,
        "official_sha256": official_hash,
        "review_sha256": review_hash,
        "document_hashes": evidence["document_hashes"],
        "already_applied": bool(existing),
        "source_fact_unchanged": True,
        "basis_id_unchanged": updated["basis_id"] == basis["basis_id"],
        "normalized_before": bool(old_fact.get("split_normalization")),
        "normalized_after": bool(new_fact.get("split_normalization")),
        "factor_after": "1",
        "complete_official_snapshot": complete,
        "calculated_before_rows": len(before["rows"]),
        "calculated_after_rows": len(after["rows"]),
        "prior_calculation_rows_preserved": preserved,
        "target_ocf_ttm_ready": bool(latest),
        "target_ocf_ttm": [
            {
                k: r.get(k)
                for k in (
                    "period_start",
                    "period_end",
                    "published_at",
                    "exact_value",
                    "numerator",
                    "weighted_shares",
                )
            }
            for r in latest
        ],
        "apply_ready": complete and preserved and bool(latest),
        "applied": False,
    }
    return updated, report


class Database:
    """Two exact keys only; no Store constructor/schema creation/provider calls."""

    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def transaction(self, write=False):
        from sqlalchemy import text

        with self.engine.begin() as db:
            if not write:
                db.execute(text("SET TRANSACTION READ ONLY"))
            db.execute(text("SET LOCAL statement_timeout='3s'"))
            db.execute(text("SET LOCAL lock_timeout='1s'"))
            yield Session(db, write)


class Session:
    def __init__(self, db, write):
        self.db, self.write = db, write

    def get(self, key, lock=None):
        from sqlalchemy import text

        suffix = " FOR SHARE" if lock == "share" else " FOR UPDATE" if lock == "update" else ""
        row = self.db.execute(
            text("SELECT payload,updated_at FROM market_snapshots WHERE key=:key" + suffix),
            {"key": key},
        ).first()
        return {"payload": json.loads(row[0]), "raw": row[0], "revision": row[1]} if row else None

    def immutable(self, key, payload, stamp):
        from sqlalchemy import text

        value = encode(payload)
        self.db.execute(
            text(
                "INSERT INTO market_snapshots(key,payload,updated_at) VALUES(:key,:payload,:stamp) "
                "ON CONFLICT(key) DO NOTHING"
            ),
            {"key": key, "payload": value, "stamp": stamp},
        )
        row = self.get(key)
        if not row or digest(row["payload"]) != digest(payload):
            raise ValueError("Immutable review/history key already contains different evidence")

    def cas_basis(self, old, updated, stamp):
        from sqlalchemy import text

        revision = next_snapshot_revision(old["revision"], stamp)
        result = self.db.execute(
            text(
                "UPDATE market_snapshots SET payload=:payload,updated_at=:revision "
                "WHERE key=:key AND payload=:old AND updated_at=:old_revision"
            ),
            {
                "key": BASIS_KEY,
                "payload": encode(updated),
                "revision": revision,
                "old": old["raw"],
                "old_revision": old["revision"],
            },
        )
        if result.rowcount != 1:
            raise ValueError("Concurrent basis update; retry a new reviewed dry-run")
        return revision


def run(
    database,
    evidence,
    *,
    apply=False,
    expected_basis=None,
    expected_official=None,
    expected_review=None,
    reviewer=None,
    basis_writer_paused=False,
):
    if apply and basis_writer_paused is not True:
        raise ValueError("Pause the share-basis writer before applying this manual review")
    if apply and (
        not reviewer
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", value or "")
            for value in (expected_basis, expected_official, expected_review)
        )
    ):
        raise ValueError("Apply needs reviewer and all three approved dry-run digests")
    with database.transaction(write=apply) as tx:
        # Match the official writer's ordering; never write or replace official.
        official = tx.get(OFFICIAL_KEY, "share" if apply else None)
        basis = tx.get(BASIS_KEY, "update" if apply else None)
        if not official or not basis:
            raise ValueError("Existing official and reviewed basis snapshots required")
        stamp = datetime.now(UTC).isoformat()
        updated, report = plan(
            basis["payload"],
            official["payload"],
            evidence,
            complete=True,
            stamp=stamp if apply else None,
            reviewer=reviewer,
        )
        if not apply:
            return report
        if (
            report["official_sha256"] != expected_official
            or report["review_sha256"] != expected_review
        ):
            raise ValueError("Official/review digest differs from the approved dry-run")
        if report["already_applied"]:
            saved = tx.get(REVIEW_KEY)
            if (
                not saved
                or saved["payload"].get("prior_basis_sha256") != expected_basis
                or saved["payload"].get("review_sha256") != expected_review
            ):
                raise ValueError("Existing review lacks the matching atomic application receipt")
            return {**report, "applied": True, "idempotent": True, "revision": basis["revision"]}
        if report["basis_sha256"] != expected_basis:
            raise ValueError("Basis digest differs from the approved dry-run")
        history_key = "share-basis-review-history:005930:" + expected_basis
        tx.immutable(
            history_key, {"payload": basis["payload"], "source_revision": basis["revision"]}, stamp
        )
        tx.immutable(
            REVIEW_KEY,
            {
                "review": updated["fact_unit_reviews"][-1],
                "prior_basis_sha256": expected_basis,
                "official_sha256": expected_official,
                "review_sha256": expected_review,
                "new_basis_sha256": digest(updated),
            },
            stamp,
        )
        revision = tx.cas_basis(basis, updated, stamp)
        # Read models compare this revision. Never rewrite original official or
        # eagerly save an analysis result that may race another source update.
        return {
            **report,
            "applied": True,
            "idempotent": False,
            "revision": revision,
            "projection_invalidation": "share_basis_revision_mismatch",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, default=ROOT / "work/remaining-v70/samsung-review.tar.gz"
    )
    parser.add_argument(
        "--inputs",
        type=Path,
        default=ROOT / "work/samsung-ocf-basis-v112/current-basis-inputs.json",
    )
    parser.add_argument("--database", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reviewer")
    parser.add_argument("--expect-basis-sha")
    parser.add_argument("--expect-official-sha")
    parser.add_argument("--expect-review-sha")
    parser.add_argument("--basis-writer-paused", action="store_true")
    args = parser.parse_args()
    engine = None
    try:
        evidence = archive_evidence(args.archive)
        if args.apply and not args.database:
            raise ValueError("Offline inputs never authorize operational apply")
        if args.database:
            from sqlalchemy import create_engine

            url = os.environ.get("MARKET_DATABASE_URL", "")
            if not url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("Explicit configured PostgreSQL environment required")
            engine = create_engine(url, connect_args={"connect_timeout": 3})
            report = run(
                Database(engine),
                evidence,
                apply=args.apply,
                reviewer=args.reviewer,
                expected_basis=args.expect_basis_sha,
                expected_official=args.expect_official_sha,
                expected_review=args.expect_review_sha,
                basis_writer_paused=args.basis_writer_paused,
            )
        else:
            data = json.loads(args.inputs.read_text(encoding="utf-8-sig"))
            _, report = plan(data["basis"], {"xbrl_facts": data["facts"]}, evidence, complete=False)
        print(json.dumps(report, ensure_ascii=False))
    except Exception as error:
        print(
            json.dumps(
                {
                    "applied": False,
                    "error_type": type(error).__name__,
                    "operation_success_unconfirmed": True,
                }
            )
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
