"""Reviewed issuer IR tables; historical payments remain separate from ex dates."""

import hashlib
import json
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from .dividend_evidence import enrich_dividends
from .store import now

URL = "https://www.hyundai.com/worldwide/en/company/ir/stock-information/shareholder-return-policy"
HEADERS = [
    "Dividend record date",
    "Dividend payment date",
    "Common share",
    "Preferred series 1",
    "Preferred series 2",
    "Preferred series 3",
]
VERSION = "hyundai-dividend-table-v3"
# Reviewed names correspond to Hyundai IR's embedded Common/Series 1/2/3 tabs.
# Runtime listings must match exactly; no code-suffix or fuzzy name inference.
SYMBOLS = {
    "005380": ("현대차", 2),
    "005385": ("현대차우", 3),
    "005387": ("현대차2우B", 4),
    "005389": ("현대차3우B", 5),
}
IDENTITY_URL = "https://asia.tools.euroland.com/tools/custom/kr-hyu/bundle_A.html?lang=en-GB"


def parse_history(content, evidence, code="005380"):
    """Use only an explicit reviewed security-to-column mapping."""
    if code not in SYMBOLS:
        raise ValueError("Unmapped issuer security")
    column = SYMBOLS[code][1]
    share_class = "ordinary" if column == 2 else "preferred"
    soup = BeautifulSoup(content, "html.parser")
    tables = []
    for table in soup.find_all("table"):
        rows = [
            [
                re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                for cell in row.find_all(["td", "th"], recursive=False)
            ]
            for row in table.find_all("tr")
        ]
        if rows and rows[0] == HEADERS:
            tables.append(rows[1:])
    if len(tables) != 1:
        raise ValueError("Issuer dividend table schema changed")
    observed = datetime.fromisoformat(evidence["available_at"]).date()
    result = {}
    for cells in tables[0]:
        if len(cells) != 6:
            raise ValueError("Issuer dividend row schema changed")
        try:
            record, payment = [date.fromisoformat(v.replace(".", "-")) for v in cells[:2]]
            amount = Decimal(cells[column].replace(",", ""))
        except (ValueError, InvalidOperation):
            raise ValueError("Invalid issuer dividend date or amount") from None
        if not amount.is_finite() or amount < 0 or payment < record:
            raise ValueError("Inconsistent issuer dividend observation")
        # A future payment entry is an announcement, never payment evidence.
        if payment > observed:
            continue
        identity = hashlib.sha256(
            f"{code}:{share_class}:{record}:{amount}:{payment}".encode()
        ).hexdigest()
        row = {
            "id": identity,
            "security_code": code,
            "share_class": share_class,
            "share_series": None if column == 2 else column - 2,
            "record_date": record.isoformat(),
            "amount": str(amount),
            "currency": "KRW",
            "confirmed_payment_date": payment.isoformat(),
            "reviewed": (payment - record).days <= 365,
            "quality_flags": []
            if (payment - record).days <= 365
            else ["payment_delay_requires_review"],
            "review_method": VERSION,
            "source_trace": evidence,
        }
        if record in result and result[record] != row:
            raise ValueError("Conflicting issuer dividend rows")
        result[record] = row
    return sorted(result.values(), key=lambda r: r["record_date"])


def _source_evidence(digest, observed_at):
    return {
        "source": "hyundai_motor_ir",
        "source_url": URL,
        "sha256": digest,
        "available_at": observed_at,
        "availability_basis": "observed_at",
        "method": VERSION,
        "currency": "KRW",
        "security_mapping": {
            "version": VERSION,
            "source_url": IDENTITY_URL,
            "method": "reviewed_issuer_tabs_and_exact_listing_names",
        },
    }


def _project_history(store, code, content, evidence):
    listed = {r["code"]: r.get("name") for r in store.get("universe", {}).get("rows", [])}
    mapped = [symbol for symbol, (name, _) in SYMBOLS.items() if listed.get(symbol) == name]
    if code not in mapped:
        raise ValueError("Issuer security listing identity mismatch")
    # Validate every class before writing any projection. A changed preferred
    # column cannot leave a partly imported issuer behind.
    parsed = {symbol: parse_history(content, evidence, symbol) for symbol in mapped}
    for symbol, observations in parsed.items():
        saved = store.get("dividend-observations:" + symbol, {})
        existing = saved.get("rows", [])
        merged = {r["id"]: r for r in existing}
        for row in observations:
            prior = merged.get(row["id"])
            if (
                prior
                and prior.get("review_method") != VERSION
                and prior.get("source_trace", {}).get("source") == "hyundai_motor_ir"
            ):
                store.put("dividend-observation-review:" + row["id"], prior)
                merged[row["id"]] = row
            else:
                merged.setdefault(row["id"], row)
        updated = list(merged.values())
        if updated != existing:
            store.put(
                "dividend-observations:" + symbol,
                {
                    **saved,
                    "rows": updated,
                    "received_at": saved.get("received_at") or evidence["available_at"],
                },
            )
        events = store.get("events:" + symbol, {})
        rows = deepcopy(events.get("rows", []))
        # The issuer history itself is an event source. Never overwrite a
        # conflicting DART amount, or create a second entitlement.
        active = [r for r in rows if not r.get("superseded_by")]
        for observation in observations:
            if not observation.get("reviewed"):
                for event in rows:
                    if event.get("id") == observation["id"]:
                        event.update(
                            payment_status="requires_review",
                            verified=False,
                            quality_flags=observation["quality_flags"],
                        )
                continue
            matches = [
                r
                for r in active
                if all(
                    r.get(k) == observation.get(k)
                    for k in ("security_code", "share_class", "record_date", "currency")
                )
                and r.get("kind") == "dividend"
            ]
            if matches:
                continue
            rows.append(
                {
                    **observation,
                    "kind": "dividend",
                    "share_label": SYMBOLS[symbol][0],
                    "date": observation["confirmed_payment_date"],
                    "payment_date": observation["confirmed_payment_date"],
                    "date_kind": "payment",
                    "payment_status": "confirmed",
                    "verified": False,
                    "status": "ex_date_required",
                    "title": "배당 지급 이력",
                    "source_url": URL,
                }
            )
        reconciled = enrich_dividends(rows, updated)
        if reconciled != events.get("rows", []):
            store.put(
                "events:" + symbol,
                {
                    **events,
                    "code": symbol,
                    "rows": reconciled,
                    "status": "partial",
                    "received_at": events.get("received_at") or evidence["available_at"],
                    "coverage": events.get("coverage")
                    or {"verified": False, "reason": "ex_dates_and_all_actions_required"},
                },
            )
    return {symbol: len(rows) for symbol, rows in parsed.items()}


def replay_issuer_dividends(store, code):
    """Rebuild from a referenced immutable IR archive, without any HTTP call.

    Replaying is a projection write, not a read-only audit. Original observation
    time and provider retry/last-fetch state remain unchanged.
    """
    if code not in SYMBOLS:
        return {"status": "unsupported_issuer"}
    from .raw_archive import read_original

    try:
        job = store.get("issuer-dividends-job:005380", {})
        digest = job.get("raw_hash")
        if not digest:
            # Older jobs lacked an archive pointer. Follow only an existing
            # issuer observation; never scan arbitrary files or infer a source.
            traces = [
                row.get("source_trace", {})
                for symbol in SYMBOLS
                for row in store.get("dividend-observations:" + symbol, {}).get("rows", [])
            ]
            candidates = [
                trace
                for trace in traces
                if trace.get("source") == "hyundai_motor_ir"
                and trace.get("source_url") == URL
                and re.fullmatch(r"[0-9a-f]{64}", str(trace.get("sha256", "")))
            ]
            if not candidates:
                return {"status": "archive_missing"}
            latest = max(
                candidates, key=lambda trace: datetime.fromisoformat(trace["available_at"])
            )
            digest = latest["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise ValueError("Invalid issuer archive reference")
        directory = store.root / "raw/issuer-dividends"
        metadata = json.loads((directory / (digest + ".json")).read_bytes())
        if (
            not isinstance(metadata, dict)
            or metadata.get("url") != URL
            or metadata.get("sha256") != digest
        ):
            raise ValueError("Issuer archive source identity changed")
        observed = datetime.fromisoformat(metadata["received_at"])
        if observed.tzinfo is None or observed > datetime.fromisoformat(now()):
            raise ValueError("Issuer archive observation time invalid")
        content = read_original(directory, digest)
        counts = _project_history(
            store, code, content, _source_evidence(digest, metadata["received_at"])
        )
        return {
            "status": "ready",
            "mode": "archive_replay",
            "version": VERSION,
            "raw_hash": digest,
            "source_received_at": metadata["received_at"],
            "observations": counts,
        }
    except FileNotFoundError:
        return {"status": "archive_missing"}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        # Keep the online error/backoff record and all last known good values.
        return {"status": "error", "mode": "archive_replay", "error": type(exc).__name__}


async def collect_issuer_dividends(source, code):
    if code not in SYMBOLS:
        return
    store = source.store
    job_key = "issuer-dividends-job:005380"
    job = store.get(job_key, {})
    if job.get("version") == VERSION and job.get("next_at", "") > now():
        return
    try:
        response = await source.client.get(URL)
        response.raise_for_status()
        digest = store.raw("issuer-dividends", URL, response.content)
        counts = _project_history(store, code, response.content, _source_evidence(digest, now()))
        store.put(
            job_key,
            {
                "status": "ready",
                "version": VERSION,
                "observations": counts,
                "raw_hash": digest,
                "received_at": now(),
                "next_at": (datetime.fromisoformat(now()) + timedelta(days=1)).isoformat(),
            },
        )
    except Exception as exc:
        # Failure cannot remove the previous observations or block DART work.
        store.put(
            job_key,
            {
                **job,
                "status": "error",
                "version": VERSION,
                "error": type(exc).__name__,
                "next_at": (datetime.fromisoformat(now()) + timedelta(hours=1)).isoformat(),
            },
        )
