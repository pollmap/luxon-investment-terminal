"""Dated major accounts for TTM; collection is never part of a user request."""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, timedelta

from .analysis import numeric
from .authenticated_sources import dart_financial_task_plan
from .providers import trace
from .store import now

ENDPOINT = "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json"


def normalize_period_accounts(rows, source_url, digest):
    facts = []
    for row in rows:
        # The balance sheet is an instant, and a single date is not a duration.
        if row.get("sj_div") != "IS" or row.get("fs_div") not in ("CFS", "OFS"):
            continue
        dates = re.findall(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(row.get("thstrm_dt") or ""))
        if len(dates) != 2:
            continue
        try:
            start, end = [date(*map(int, parts)) for parts in dates]
        except ValueError:
            continue
        duration = (end - start).days + 1
        if not 70 <= duration <= 380:
            continue
        cumulative = duration > 100
        if 100 < duration < 350 and numeric(str(row.get("thstrm_add_amount", "")).replace(",", "")) is None:
            # A quarterly amount cannot stand in for a missing YTD amount.
            continue
        field = "thstrm_add_amount" if cumulative and numeric(str(row.get("thstrm_add_amount", "")).replace(",", "")) is not None else "thstrm_amount"
        amount = numeric(str(row.get(field, "")).replace(",", ""))
        name, currency, receipt = row.get("account_nm"), row.get("currency"), str(row.get("rcept_no") or "")
        if amount is None or not name or not currency or not re.fullmatch(r"\d{14}", receipt):
            continue
        evidence = trace("opendart", source_url, digest, f"{start}/{end}", currency, row["fs_div"])
        evidence.update(filing_id=receipt, currency=currency, method=f"thstrm_dt / {field}", formula=None)
        facts.append({
            "account_id": "IS:" + name, "name": name, "basis": row["fs_div"],
            "unit": "원" if currency == "KRW" else currency, "currency": currency,
            "kind": "duration", "period_verified": True,
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            **({"fiscal_start": start.isoformat()} if cumulative else {}),
            "cumulative": cumulative, "value": float(amount), "exact_value": str(amount), "filing_id": receipt,
            "published_at": None, "publication_date": row.get("rcept_dt"),
            "source_trace": evidence,
        })
    return facts


class DartPeriodCollector:
    def __init__(self, authenticated):
        self.source, self.store = authenticated, authenticated.store
        from .fact_store import FactStore
        self.facts = FactStore(self.store)

    async def collect(self, codes, *, today=None, checkpoint_key="dart-period-progress"):
        """One batch per call, durable cursor and retry; last-good facts survive errors."""
        if not self.source.dart:
            return {"status": "credentials_missing", "processed": 0}
        if not re.fullmatch(r"dart-period-progress(?::[a-z0-9-]{1,64})?", checkpoint_key):
            raise ValueError("Invalid period collection checkpoint")
        today = today or date.today()
        key = checkpoint_key
        state = self.store.get(key, {})
        if (state.get("retry_after") or "") > now():
            return {"status": "retry_wait", "processed": 0}
        try:
            mapping = await self.source.corporations()
        except Exception as exc:
            state.update(status="error", stage="corporation_identity", error=type(exc).__name__,
                         updated_at=now(), retry_after=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
            self.store.put(key, state)
            return {"status": "error", "stage": "corporation_identity", "processed": 0}
        codes = sorted({code for code in codes if mapping.get(code)})
        tasks = list(dict.fromkeys((t["year"], t["report_code"]) for t in dart_financial_task_plan(today)
                                  if t["kind"] == "statement" and int(t["year"]) >= today.year - 2))
        if not codes or not tasks:
            return {"status": "identity_unavailable", "processed": 0}
        fingerprint = hashlib.sha256(repr((codes, tasks)).encode()).hexdigest()
        if state.get("fingerprint") != fingerprint:
            state = {"fingerprint": fingerprint, "task": 0, "offset": 0}
        if state.get("completed_at"):
            if self.source._age(state["completed_at"]) < 7 * 86400:
                return {"status": "current", "processed": 0}
            state.update(task=0, offset=0, completed_at=None)
        index, offset = state.get("task", 0), state.get("offset", 0)
        year, report = tasks[index]
        batch = codes[offset:offset + 100]
        corp_codes = ",".join(mapping[code] for code in batch)
        url = f"{ENDPOINT}?corp_code={corp_codes}&bsns_year={year}&reprt_code={report}"
        try:
            response = await self.source._dart_get(ENDPOINT, params={"crtfc_key": self.source.dart,
                "corp_code": corp_codes, "bsns_year": year, "reprt_code": report})
            response.raise_for_status()
            payload = response.json()
            status = str(payload.get("status", ""))
            if status not in ("000", "013"):
                raise ValueError("dart_status_" + status)
            digest = self.store.raw("dart", url, response.content)
        except Exception as exc:
            # Never persist exception text: HTTP client exceptions can contain a key URL.
            state.update(status="error", stage="period_accounts", error=type(exc).__name__, updated_at=now(),
                         retry_after=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
            self.store.put(key, state)
            return {"status": "error", "processed": 0}
        total = 0
        for code in batch:
            rows = [r for r in payload.get("list", []) if str(r.get("stock_code")) == code]
            facts = normalize_period_accounts(rows, url, digest)
            self.facts.append(mapping[code], facts)
            official = self.store.get("official:" + code, {})
            existing = official.get("period_facts", [])
            # Preserve corrections as separate facts; calculation chooses latest receipt.
            identity = lambda f: (f["account_id"], f["basis"], f["period_start"], f["period_end"], f.get("filing_id") or f.get("source_trace", {}).get("filing_id"))
            merged = {identity(f): f for f in existing}
            merged.update({identity(f): f for f in facts})
            official["period_facts"] = list(merged.values())
            progress = official.setdefault("period_query_progress", {})
            progress[f"{year}:{report}"] = {"status": "ready" if facts else "period_evidence_missing" if rows else "no_data",
                                            "received_at": now(), "facts": len(facts), "raw_hash": digest}
            self.store.put("official:" + code, official)
            total += len(facts)
        offset += len(batch)
        if offset >= len(codes):
            index, offset = index + 1, 0
        state.update(task=index, offset=offset, updated_at=now(), status="collecting", stage="period_accounts", error=None, retry_after=None)
        if index >= len(tasks):
            state.update(completed_at=now(), status="ready")
        self.store.put(key, state)
        return {"status": state["status"], "processed": len(batch), "facts": total}
