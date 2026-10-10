"""Explicitly inspected EPS notes for the first Kia bootstrap, not a scraper.

Reviewed 2026-10-09 against DART's EPS note and separate income-statement
periods. These bindings certify table interpretation only, never split units.
New/corrected filings require new document and period hashes.
"""

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from bs4 import BeautifulSoup

from .raw_archive import read_original
from .reviewed_eps_tables import CONCEPTS, KRW, SHARES
from .share_basis_review_store import _document_receipt
from .store import now
from .dart_retry import provider_retry_for

VERSION = "reviewed-bootstrap-eps-notes-v1"
BASE = "https://dart.fss.or.kr/report/viewer.do?"
BINDINGS = (
    {
        "code": "000270",
        "company_id": "00106641",
        "filing_id": "20251114002676",
        "published_at": "2025-11-14",
        "title": "17. 주당이익 (연결)",
        "url": BASE
        + "rcpNo=20251114002676&dcmNo=10883860&eleId=42&offset=934689&length=16566&dtd=dart4.xsd",
        "sha256": "06c974e06223c2c7f946b98e836114a5d67303aee2448d17746dff9d04d75ff2",
        "period_url": BASE
        + "rcpNo=20251114002676&dcmNo=10883860&eleId=21&offset=238383&length=28201&dtd=dart4.xsd",
        "period_sha256": "83cb905ec8fb836e31e119412ad84cf2e89a46d3bb54524c37a6f3036c2c771a",
        "period_label": "제 82 기 3분기 2025.01.01 부터 2025.09.30 까지",
        "headers": [["", "주식"], ["", "보통주"], ["", "3개월", "누적"]],
        "periods": [("2025-07-01", "2025-09-30"), ("2025-01-01", "2025-09-30")],
        "labels": {
            "profit": "지배기업소유주지분순이익",
            "shares": "가중평균유통보통주식수(*)(단위 : 주)",
            "eps": "기본주당이익(손실)",
        },
    },
    {
        "code": "000270",
        "company_id": "00106641",
        "filing_id": "20260312001224",
        "published_at": "2026-03-12",
        "title": "24. 주당이익 (연결)",
        "url": BASE
        + "rcpNo=20260312001224&dcmNo=11116515&eleId=52&offset=1685547&length=10128&dtd=dart4.xsd",
        "sha256": "6e503b6361e157be5301c2837b84b49f0127d3e8c3bf7088176e03f674f39ee7",
        "period_url": BASE
        + "rcpNo=20260312001224&dcmNo=11116515&eleId=21&offset=375072&length=23004&dtd=dart4.xsd",
        "period_sha256": "f561b30fe22862280d5e368281c9b478c9a07c88d3966a88bfc5c3623af8109b",
        "period_label": "제 82 기 2025.01.01 부터 2025.12.31 까지",
        "headers": [["", "보통주"]],
        "periods": [("2025-01-01", "2025-12-31")],
        "labels": {
            "profit": "지배기업소유주지분순이익",
            "shares": "가중평균유통보통주식수",
            "eps": "기본주당이익(손실)",
        },
    },
)


def parse_reviewed_note(content, period_content, binding):
    receipt = binding["filing_id"]
    for raw, url_key, hash_key in (
        (content, "url", "sha256"),
        (period_content, "period_url", "period_sha256"),
    ):
        if (
            hashlib.sha256(raw).hexdigest() != binding[hash_key]
            or _document_receipt(binding[url_key]) != receipt
        ):
            raise ValueError("Reviewed EPS source identity or hash changed")
    soup = BeautifulSoup(content, "html.parser")
    words = soup.get_text(" ", strip=True)
    period_text = BeautifulSoup(period_content, "html.parser").get_text(" ", strip=True)
    if (
        binding["title"] not in words
        or "(연결)" not in binding["title"]
        or "(단위 : 원)" not in words
        or "연결 손익계산서" not in period_text
        or binding["period_label"] not in period_text
    ):
        raise ValueError("Reviewed EPS scope, unit or period changed")
    labels = binding["labels"]
    tables = [
        table
        for table in soup.find_all("table")
        if all(label in table.get_text(" ", strip=True) for label in labels.values())
    ]
    if len(tables) != 2:
        raise ValueError("Reviewed current/prior EPS table count changed")
    rows = [
        [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"], recursive=False)]
        for row in tables[0].find_all("tr")
    ]
    if rows[: len(binding["headers"])] != binding["headers"]:
        raise ValueError("Reviewed ordinary-share columns changed")
    values = {}
    for role, label in labels.items():
        matches = [row[1:] for row in rows if row and row[0] == label]
        if len(matches) != 1 or len(matches[0]) != len(binding["periods"]):
            raise ValueError("Reviewed EPS account columns changed")
        if any(not re.fullmatch(r"\(?-?[\d,]+(?:\.\d+)?\)?", v) for v in matches[0]):
            raise ValueError("Reviewed EPS amount missing")
        values[role] = [
            Decimal(v.replace(",", "").replace("(", "-").replace(")", "")) for v in matches[0]
        ]
    facts = []
    for column, (start, end) in enumerate(binding["periods"]):
        length = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        if not 70 <= length <= 380 or date.fromisoformat(
            binding["published_at"]
        ) < date.fromisoformat(end):
            raise ValueError("Reviewed EPS accounting period invalid")
        profit, shares, eps = (values[role][column] for role in ("profit", "shares", "eps"))
        if shares <= 0 or abs(profit / shares - eps) > Decimal(".5") + Decimal(".5") / shares:
            raise ValueError("Reviewed EPS reconciliation failed")
        for role, unit in (("profit", KRW), ("shares", SHARES), ("eps", KRW + "/" + SHARES)):
            trace = {
                "source": "dart_reviewed_note",
                "company_id": binding["company_id"],
                "filing_id": receipt,
                "available_at": binding["published_at"],
                "source_url": binding["url"],
                "sha256": binding["sha256"],
                "period_source_url": binding["period_url"],
                "period_source_sha256": binding["period_sha256"],
                "table": 0,
                "column": column,
                "mapping_version": VERSION,
                "unit": unit,
            }
            facts.append(
                {
                    "account_id": "{urn:korea-stocks:reviewed-eps-note}" + CONCEPTS[role],
                    "company_id": binding["company_id"],
                    "filing_id": receipt,
                    "basis": "CFS",
                    "share_class": "ordinary",
                    "kind": "duration",
                    "unit": unit,
                    "currency": "KRW" if role != "shares" else None,
                    "decimals": 0,
                    "value": str(values[role][column]),
                    "period_start": start,
                    "period_end": end,
                    "period_verified": True,
                    "published_at": binding["published_at"],
                    "mapping_verified": True,
                    "source_account_label": labels[role],
                    "dimensions": {},
                    "source_trace": trace,
                }
            )
    return facts


def _read_reviewed_archive(directory, url, digest):
    content = read_original(directory, digest)
    try:
        metadata = json.loads((directory / (digest + ".json")).read_bytes())
    except FileNotFoundError as exc:
        raise ValueError("Reviewed source envelope is missing") from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("url") != url
        or metadata.get("sha256") != digest
    ):
        raise ValueError("Reviewed source archive identity changed")
    return content


async def collect_bootstrap_note(source, code, *, archive_only=False):
    """Replay verified archives offline; budget/backoff apply only to missing bytes.

    archive_only never performs a network request. It may append facts from an
    already inspected and revalidated archive; this is not a read-only mode.
    """
    from .fact_store import FactStore

    store = source.store
    company = store.get("dart-corporations", {}).get("codes", {}).get(code)
    if code not in store.listed_codes():
        return {"status": "out_of_scope"}
    filings = {r.get("receipt"): r for r in store.get("filing-index:" + code, {}).get("rows", [])}
    for binding in BINDINGS:
        if binding["code"] != code or binding["company_id"] != company:
            continue
        key = "bootstrap-eps-note:" + binding["filing_id"]
        state = store.get(key, {})
        if state.get("status") == "ready" and state.get("version") == VERSION:
            continue
        # Publication comes from the separately collected issuer index. Receipt
        # digits and the document's collection time are not publication proof.
        if str(filings.get(binding["filing_id"], {}).get("date", "")).replace("-", "") != binding[
            "published_at"
        ].replace("-", ""):
            return {"status": "publication_evidence_required", "filing_id": binding["filing_id"]}
        try:
            documents = []
            for url_key, hash_key in (("url", "sha256"), ("period_url", "period_sha256")):
                directory = store.root / "raw/dart-bootstrap-eps"
                try:
                    content = _read_reviewed_archive(directory, binding[url_key], binding[hash_key])
                except FileNotFoundError:
                    if archive_only:
                        return {
                            "status": "archive_missing",
                            "filing_id": binding["filing_id"],
                            "source_url": binding[url_key],
                            "sha256": binding[hash_key],
                        }
                    if not getattr(source, "dart", None):
                        return {"status": "credentials_missing", "filing_id": binding["filing_id"]}
                    if provider_retry_for(store, binding[url_key]).get("next_at", "") > now():
                        return {"status": "provider_retry_wait"}
                    if state.get("retry_after", "") > now():
                        return {
                            "status": "retry_wait",
                            "filing_id": binding["filing_id"],
                            "retry_after": state["retry_after"],
                        }
                    # Use the shared request budget/backoff, no private key in URL.
                    response = await source._dart_get(binding[url_key], params={})
                    response.raise_for_status()
                    content = response.content
                    store.raw("dart-bootstrap-eps", binding[url_key], content)
                documents.append(content)
            facts = parse_reviewed_note(*documents, binding)
            ledger = FactStore(store)
            inserted = ledger.append(company, facts)
            official = store.get("official:" + code, {}, use_redis=False)
            official["xbrl_facts"] = ledger.analysis_facts(company)
            store.put("official:" + code, official)
            result = {
                "status": "ready",
                "version": VERSION,
                "facts": len(facts),
                "inserted": inserted,
                "sha256": binding["sha256"],
                "period_sha256": binding["period_sha256"],
                "checked_at": now(),
            }
        except Exception as exc:
            result = {
                **state,
                "status": "error",
                "error": type(exc).__name__,
                "checked_at": now(),
                "retry_after": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            }
        store.put(key, result)
        return result
    return {"status": "no_pending_reviewed_notes"}
