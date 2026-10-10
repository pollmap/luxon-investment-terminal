"""Bounded first-issuer evidence work; discovery never certifies share units.

The existing extension worker starts from a reviewed share basis. This queue
fills the preceding gap: actual EPS periods, missing XBRL requests and exact
unit-review requirements. It never writes share-basis or financial facts.
"""

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import text

from .eps_calculation import EPS_ACCOUNTS, calculate_ttm_eps, quarters_at
from .share_basis import valid_evidence
from .share_basis_review_store import review_index_key
from .store import now
from .dart_retry import provider_retry_for

VERSION = "first-share-basis-evidence-v1"
REPLAY_VERSION = "eps-input-archive-replay-v1"


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def bootstrap_target(store, reviewed, cursor):
    """One persistent round-robin slot; already reviewed issuers remain separate."""
    companies = store.get("dart-corporations", {}).get("codes", {})
    candidates = sorted(
        code for code in store.listed_codes() if code not in reviewed and companies.get(code)
    )
    if not candidates:
        return None
    from .share_basis_bootstrap_notes import BINDINGS

    priority = sorted(set(candidates) & (set(store.active()) | {b["code"] for b in BINDINGS}))
    if priority and cursor % 5 == 0:
        return priority[(cursor // 5) % len(priority)]
    return candidates[(cursor - (cursor // 5 + 1) if priority else cursor) % len(candidates)]


def read_eps_facts(store, company):
    # No FactStore constructor/schema writes on this observation path.
    conditions = " OR ".join(f"account_id LIKE :a{i}" for i in range(len(EPS_ACCOUNTS)))
    params = {
        "company": company,
        **{f"a{i}": "%}" + name for i, name in enumerate(sorted(EPS_ACCOUNTS))},
    }
    with store.read_connection() as db:
        rows = db.execute(
            text(
                "SELECT id, payload FROM market_financial_facts "
                "WHERE company_id=:company AND (" + conditions + ") ORDER BY period_end, id"
            ),
            params,
        ).all()
    from .source_trace_store import SourceTraceStore
    restored = SourceTraceStore(store, create_schema=False).restore_payloads(
        [payload for _, payload in rows]
    )
    return [{**fact, "fact_id": identity}
            for (identity, _), fact in zip(rows, restored, strict=True)]


def describe_inputs(facts, company):
    # Never inherit a share-basis marker from a stale projection or other issuer.
    scoped = [
        {**f, "per_share_basis": None}
        for f in facts
        if (f.get("company_id") or f.get("entity_id")) == company
    ]
    result = calculate_ttm_eps(scoped)
    inputs = result["inputs"]
    basis = "CFS" if any(r["basis"] == "CFS" for r in inputs) else "OFS"
    group = [r for r in inputs if r["basis"] == basis]
    quarters = quarters_at(group, max((r["published_at"] for r in group), default=""))
    latest = quarters[-1]["period_end"] if quarters else None
    windows = [r for r in result["rows"] if r["basis"] == basis and r["period_end"] == latest]
    window = max(windows, key=lambda r: r["published_at"]) if windows else None
    # A requested search range is not a verified accounting period. Exact
    # quarters below come only from reconciled source contexts.
    begin = None
    if latest:
        end = date.fromisoformat(latest)
        try:
            begin = (end.replace(year=end.year - 1) + timedelta(days=1)).isoformat()
        except ValueError:  # Leap-day endpoint.
            begin = end.replace(year=end.year - 1, day=28).isoformat()
    gaps, cursor = [], begin
    for row in quarters:
        if not begin or row["period_end"] < begin:
            continue
        if row["period_start"] > cursor:
            gaps.append(
                {
                    "start": cursor,
                    "end": (
                        date.fromisoformat(row["period_start"]) - timedelta(days=1)
                    ).isoformat(),
                }
            )
        cursor = max(
            cursor, (date.fromisoformat(row["period_end"]) + timedelta(days=1)).isoformat()
        )
    return {
        "basis": basis if group else None,
        "input_count": len(inputs),
        "input_fact_ids": sorted({i for r in inputs for i in r["input_fact_ids"]}),
        "filing_ids": sorted({r["filing_id"] for r in inputs}),
        "periods": [
            {
                k: r[k]
                for k in (
                    "period_start",
                    "period_end",
                    "filing_id",
                    "published_at",
                    "input_fact_ids",
                )
            }
            for r in quarters
        ],
        "candidate_window": {"start": begin, "end": latest} if latest else None,
        "period_inputs_complete": window is not None,
        "missing_period_ranges": gaps,
        "rejected": result["rejected"],
    }


def _statement_identity(row, company, report_code, digest):
    trace = row.get("source_trace") or {}
    url = urlsplit(trace.get("source_url") or "")
    query = parse_qs(url.query)
    return (
        url.scheme == "https"
        and url.netloc == "opendart.fss.or.kr"
        and url.path == "/api/fnlttSinglAcntAll.json"
        and query.get("corp_code") == [company]
        and query.get("reprt_code") == [report_code]
        and row.get("report_code") == report_code
        and trace.get("sha256", trace.get("filing_id")) == digest
    )


def missing_reports(store, code, company, official, inputs):
    """Use real response receipts/codes, never guess Q1/Q3 from a month."""
    index = {
        r["receipt"]: r
        for r in store.get("filing-index:" + code, {}).get("rows", [])
        if r.get("receipt")
    }
    begin = (inputs.get("candidate_window") or {}).get("start")
    found = {}
    for report in official.get("financial_reports", {}).values():
        report_code, digest = report.get("report_code"), report.get("source_hash")
        if (
            report.get("status") != "ready"
            or report_code not in ("11011", "11012", "11013", "11014")
            or not re.fullmatch(r"[0-9a-f]{64}", str(digest or ""))
            or begin
            and str(report.get("year", "")) < begin[:4]
        ):
            continue
        key = report.get("snapshot_key")
        if not key or not key.startswith("financial-report:" + code + ":"):
            continue
        snapshot = store.get(key, {})
        for row in snapshot.get("rows", []):
            receipt = str(row.get("receipt") or "")
            if (
                not re.fullmatch(r"\d{14}", receipt)
                or receipt in inputs["filing_ids"]
                or not _statement_identity(row, company, report_code, digest)
            ):
                continue
            filing = index.get(receipt, {})
            state = store.get("xbrl-job:" + receipt, {})
            found[receipt] = {
                "filing_id": receipt,
                "report_code": report_code,
                "source_snapshot": key,
                "source_hash": digest,
                "publication": filing.get("date"),
                "title": filing.get("title"),
                "document_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt,
                "xbrl_status": state.get("status", "not_collected"),
                "error": state.get("error"),
                "retry_after": state.get("retry_after"),
                "raw_hash": state.get("raw_hash"),
            }
    # A bootstrap owns a small missing-input budget, not a second full collector.
    return sorted(found.values(), key=lambda r: r["filing_id"], reverse=True)[:4]


def queue_missing_reports(store, code, company, reports):
    queued = []
    for report in reports:
        receipt = report["filing_id"]
        key = "xbrl-request:" + receipt
        request = store.get(key, {})
        if request and (
            request.get("code") != code or request.get("report_code") != report["report_code"]
        ):
            queued.append({**report, "intent_status": "request_identity_conflict"})
            continue
        if not request:
            store.put(
                key,
                {
                    "code": code,
                    "filing_id": receipt,
                    "report_code": report["report_code"],
                    "requested_at": now(),
                    "discovered_from": report["source_snapshot"],
                },
            )
        replay = report["xbrl_status"] in ("ready", "unsupported_instance")
        revision = _hash(
            [REPLAY_VERSION, company, receipt, report["raw_hash"], report["publication"]]
        )
        intent = {
            "code": code,
            "company_id": company,
            "filing_id": receipt,
            "report_code": report["report_code"],
            "revision": revision,
            "mode": "archive_replay" if replay else "collect",
            "raw_hash": report["raw_hash"],
            "publication": report["publication"],
            "source_snapshot": report["source_snapshot"],
            "source_hash": report["source_hash"],
        }
        intent_key = "xbrl-bootstrap-intent:" + receipt
        previous = store.get(intent_key, {})
        if previous.get("revision") == revision:
            intent = previous
        else:
            intent.update(status="requested", requested_at=now())
            store.put(intent_key, intent)
        queued.append({**report, "intent_status": intent["status"], "intent_revision": revision})
    return queued


def prepare_bootstrap(store, code, *, day=None):
    review_index_key(code)
    if code not in store.listed_codes():
        return {"status": "out_of_scope"}
    company = store.get("dart-corporations", {}).get("codes", {}).get(code)
    if not company or not re.fullmatch(r"\d{8}", str(company)):
        return {"status": "company_identity_required"}
    evidence = store.get("share-basis:" + code, {})
    if evidence.get("security_code") == code and valid_evidence(evidence, company):
        return {"status": "already_reviewed"}
    key = "share-basis-bootstrap:" + code
    today = day or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    previous = store.get(key, {})
    keys = [
        "official:" + code,
        "filing-index:" + code,
        "corporate-event-index:" + code,
        "share-unit-reviews:" + code,
        "share-basis:" + code,
    ]
    revisions = store.revisions(
        keys + ["xbrl-job:" + r["filing_id"] for r in previous.get("reports", [])]
    )
    if (
        previous.get("company_id") == company
        and previous.get("input_revisions") == revisions
        and previous.get("checked_day") == today
    ):
        return previous
    official = store.get("official:" + code, {})
    inputs = describe_inputs(read_eps_facts(store, company), company)
    reports = queue_missing_reports(
        store, code, company, missing_reports(store, code, company, official, inputs)
    )
    revisions = store.revisions(keys + ["xbrl-job:" + r["filing_id"] for r in reports])
    prices = store.metadata(f"bars:{code}:통합:raw", {})
    price_end = prices.get("last_time") if prices.get("adjustment") == "raw" else None
    missing = [
        {
            "kind": "initial_share_unit_review",
            "security_code": code,
            "company_id": company,
            "start": (inputs.get("candidate_window") or {}).get("start"),
            "through": price_end,
            "required": [
                "issuer_share_history_original_hash",
                "ordinary_share_identity",
                "complete_unfiltered_disclosure_window",
                "share_change_effect_reviews",
                "filing_unit_and_correction_alignment",
            ],
        }
    ]
    if not inputs["period_inputs_complete"]:
        missing.insert(
            0,
            {
                "kind": "continuous_four_quarter_inputs",
                "required": [
                    "ordinary_attributable_profit",
                    "weighted_ordinary_shares",
                    "reported_basic_eps",
                ],
                "ranges": inputs["missing_period_ranges"],
                "filing_ids": [r["filing_id"] for r in reports],
            },
        )
    result = {
        "version": VERSION,
        "security_code": code,
        "company_id": company,
        "status": "review_required" if inputs["period_inputs_complete"] else "awaiting_inputs",
        "inputs": inputs,
        "reports": reports,
        "missing_proof": missing,
        "corporate_event_index_status": store.get("corporate-event-index:" + code, {}).get(
            "status", "not_collected"
        ),
        "input_revisions": revisions,
        "checked_day": today,
        "updated_at": now(),
    }
    store.put(key, result)
    return result


async def service_xbrl_intent(source):
    """Spend at most one existing collector slot; obey its provider backoff."""
    from .xbrl_facts import collect_xbrl

    store = source.store
    provider_wait = (
        not source.dart or provider_retry_for(store, "https://opendart.fss.or.kr/api/fnlttXbrl.xml").get("next_at", "") > now()
    )
    companies = store.get("dart-corporations", {}).get("codes", {})
    listed = set(store.listed_codes())
    intents = store.list_prefix("xbrl-bootstrap-intent:")
    for key, intent in sorted(intents.items(), key=lambda item: item[1].get("requested_at", "")):
        if intent.get("status") not in ("requested", "error"):
            continue
        replay = intent.get("mode") == "archive_replay"
        if not replay and provider_wait:
            continue
        code, receipt = intent["code"], intent["filing_id"]
        request = store.get("xbrl-request:" + receipt, {})
        if (
            code not in listed
            or companies.get(code) != intent.get("company_id")
            or request.get("code") != code
            or request.get("report_code") != intent["report_code"]
        ):
            store.put(key, {**intent, "status": "identity_conflict", "checked_at": now()})
            continue
        state = store.get("xbrl-job:" + receipt, {})
        if state.get("retry_after", "") > now():
            continue
        filing = next(
            (
                r
                for r in store.get("filing-index:" + code, {}).get("rows", [])
                if r.get("receipt") == receipt
            ),
            {},
        )
        publication = filing.get("date")
        if not publication or publication != intent.get("publication"):
            store.put(key, {**intent, "status": "publication_review_required", "checked_at": now()})
            continue
        if replay and (not intent.get("raw_hash") or state.get("raw_hash") != intent["raw_hash"]):
            store.put(key, {**intent, "status": "archive_review_required", "checked_at": now()})
            continue
        result = await collect_xbrl(
            source,
            code,
            receipt,
            intent["report_code"],
            publication=publication,
            replay_revision=intent["revision"] if replay else None,
        )
        # A successful parse is not a successful EPS mapping or share review.
        status = (
            "processed"
            if result.get("status") in ("ready", "unsupported_instance")
            else ("archive_review_required" if replay else "error")
        )
        store.put(
            key,
            {
                **intent,
                "status": status,
                "collector_status": result.get("status"),
                "error": result.get("error"),
                "retry_after": result.get("retry_after"),
                "checked_at": now(),
            },
        )
        return True
    return False
