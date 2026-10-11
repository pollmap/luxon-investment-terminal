from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from packages.core.source_trace import SourceTrace

from .metrics import METRICS
from .store import now

NAVER_FLOW_SCOPE = "네이버 제공처 일별 수급 · 거래시장 구분 미제공"
NAVER_FLOW_KIND = "naver-provider-daily-investor-v1"


def read_naver_flows_fallback(store, code):
    """Explicit provider-scoped fallback; never blend into the Toss series."""
    if not re.fullmatch(r"[0-9A-Z]{6}", code):
        raise ValueError("Invalid security code")
    key = "naver:investor-trading:" + code
    revision_keys = (key, "detail:" + code)
    before = store.revisions(revision_keys)
    snapshot = store.metadata(key, {})
    result = {"code": code, "rows": [], "status": "no_data", "source": "naver",
              "scope": NAVER_FLOW_SCOPE, "venue": "PROVIDER", "as_of": None,
              "received_at": None, "history_complete": False, "fallback": True}
    legacy_rows, legacy_status, legacy_received = _naver_legacy_flows(store, code)
    if snapshot:
        if (snapshot.get("code") != code or snapshot.get("aggregation") != NAVER_FLOW_KIND
                or snapshot.get("_series_ref") != key):
            raise ValueError("Naver investor source identity mismatch")
        rows = store.time_series.rows(key, reverse=True)
        if len(rows) != snapshot.get("row_count"):
            raise ValueError("Naver investor source range mismatch")
        if _validate_naver_flow_rows(rows, code):
            raise ValueError("Naver investor row evidence mismatch")
        by_date = {row["date"]: row for row in legacy_rows}
        by_date.update({row["date"]: row for row in rows})
        rows = sorted(by_date.values(), key=lambda row: row["date"], reverse=True)
        result.update(rows=rows, status=snapshot.get("status", "no_data"),
                      as_of=rows[0]["date"] if rows else None,
                      received_at=snapshot.get("received_at"))
        if legacy_status not in ("no_data", "stale"):
            result["legacy_status"] = legacy_status
    else:
        result.update(rows=legacy_rows, status=legacy_status,
                      as_of=legacy_rows[0]["date"] if legacy_rows else None,
                      received_at=legacy_received, legacy=bool(legacy_rows))
    if store.revisions(revision_keys) != before:
        raise ValueError("Naver investor source changed during read")
    return result


def _naver_legacy_flows(store, code):
    legacy = store.metadata("detail:" + code, {})
    rows = legacy.get("flows", [])
    if not rows:
        return [], "no_data", None
    if legacy.get("_trading_projection", {}).get("fields", {}).get("flows"):
        return [], "legacy_reference_conflict", None
    issue = _validate_naver_flow_rows(rows, code)
    if issue:
        return [], issue, None
    return sorted(rows, key=lambda row: row["date"], reverse=True), "stale", legacy.get(
        "flows_received_at")


def _validate_naver_flow_rows(rows, code):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return "legacy_evidence_unverified"
    if any(not isinstance(row.get("source_trace", {}), dict) for row in rows):
        return "legacy_evidence_unverified"
    if any(row.get("source_trace", {}).get("source") != "naver" for row in rows):
        return "legacy_mixed_sources"
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    for row in rows:
        evidence = row.get("source_trace", {})
        digest = evidence.get("source_document_id", "")
        if (evidence.get("source_url") != url or evidence.get("unit") != "주"
                or evidence.get("scope") not in (NAVER_FLOW_SCOPE, "제공처 일별 수급")
                or evidence.get("period") != row.get("date")
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            return "legacy_evidence_unverified"
        try:
            datetime.strptime(row["date"], "%Y-%m-%d")
        except (KeyError, TypeError, ValueError):
            return "legacy_evidence_unverified"
    return None


def _publish_detail_preserving_flows(store, code, payload):
    """Rebase protected fields after network awaits, under the snapshot lock."""
    from sqlalchemy import select
    if store.engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    key = "detail:" + code
    with store.lock, store.engine.begin() as db:
        # Reserve an absent key without replacing a concurrent Toss insertion.
        statement = insert(store.snapshots).values(key=key, payload="{}", updated_at=now())
        db.execute(statement.on_conflict_do_nothing(index_elements=[store.snapshots.c.key]))
        query = select(store.snapshots.c.payload).where(store.snapshots.c.key == key)
        if store.engine.dialect.name == "postgresql":
            query = query.with_for_update()
        current = json.loads(db.execute(query).scalar_one())
        result = dict(payload)
        for field in ("flows", "flows_received_at", "flows_status", "_trading_projection"):
            if field in current:
                result[field] = current[field]
            else:
                result.pop(field, None)
        result["facts"] = dict(result.get("facts", {}))
        for field in ("foreign", "foreign_net", "institution_net"):
            if field in current.get("facts", {}):
                result["facts"][field] = current["facts"][field]
            else:
                result["facts"].pop(field, None)
        result["source_status"] = dict(result.get("source_status", {}))
        if "flows" in current.get("source_status", {}):
            result["source_status"]["flows"] = current["source_status"]["flows"]
        else:
            result["source_status"].pop("flows", None)
        changed = store.put(key, result, _connection=db)
    if changed:
        store.notify_changed(key)
    return result


def number(value):
    if value is None or str(value).strip() in ("", "-", "N/A", "null", "적자", "완전잠식"):
        return None
    text = re.sub(r"[,\s%배원주]", "", str(value)).replace("−", "-")
    try:
        result = float(text)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def trace(source, url, digest, period, unit="원", scope="제공처 집계", formula="source value"):
    model = SourceTrace(
        source=source,
        source_document_id=digest,
        filing_id=digest,
        period=period or "기준일 미제공",
        available_at=datetime.now(UTC),
        unit=unit,
        currency="KRW",
        method="public_source",
        formula=formula,
        source_url=url,
        quality_status="source_observed",
        quality_flags=["publication_time_unverified"],
        ingested_at=datetime.now(UTC),
        scope=scope,
        parser_version="market-v1",
    )
    model.assert_storage_ready()
    # Retrieval time is not historical publication time. No PIT claim is made.
    result = model.model_dump(mode="json")
    result["available_at"] = None
    return result


def normalize_stock(item, market, evidence):
    code = item.get("itemCode", "")
    if not re.fullmatch(r"[0-9A-Z]{6}", code):
        raise ValueError("Invalid security code")
    return {
        "code": code,
        "name": item["stockName"],
        "logo_url": item.get("itemLogoPngUrl") or item.get("itemLogoUrl"),
        "market": market,
        "issuer_id": None,
        "security_type": item.get("stockEndType", "stock"),
        "price": number(item.get("closePriceRaw", item.get("closePrice"))),
        "change": number(item.get("fluctuationsRatio")),
        "volume": number(
            item.get("accumulatedTradingVolumeRaw", item.get("accumulatedTradingVolume"))
        ),
        "value": number(item.get("accumulatedTradingValueRaw")),
        "cap": number(item.get("marketValueRaw")),
        "foreign": None,
        "per": None,
        "pbr": None,
        "roe": None,
        "dividend": None,
        "foreign_net": None,
        "institution_net": None,
        "revenue": None,
        "operating_profit": None,
        "sector": None,
        "as_of": item.get("localTradedAt"),
        "received_at": now(),
        "market_status": item.get("marketStatus", "UNKNOWN"),
        "venue": "PROVIDER",
        "scope": "제공처 시세 · 세션별 집계 범위는 원문 참조",
        "source_trace": evidence,
        "facts": {},
    }


def parse_fnguide(html, code, evidence):
    soup = BeautifulSoup(html, "html.parser")
    actual = soup.select_one("#cmp_cd")
    if not actual or actual.get("value") != code:
        raise ValueError("FnGuide security identity mismatch")
    result = {"financials": [], "shareholders": [], "company": {}, "source_trace": evidence}
    match = re.search(r"snpFinancial:\s*(\{[^\r\n]+\})\s*,", html)
    if match:
        data = json.loads(match.group(1))
        for idx, header in enumerate(data["header"]):
            period = header.get("YYMM")
            if not isinstance(period, str) or not period.strip():
                continue
            period = period.strip()
            frequency = "annual" if idx < 4 else "quarter"
            for row in data["data"]:
                name = row["NAME"].strip()
                raw_unit = (row.get("UNIT") or "").strip("()")
                unit = (
                    "%"
                    if name in ("ROE", "ROA") or "율" in name or "률" in name
                    else "배"
                    if name in ("PER", "PBR")
                    else raw_unit or ("주" if "주식수" in name else "억원")
                )
                result["financials"].append(
                    {
                        "name": name,
                        "period": period,
                        "frequency": frequency,
                        "basis": "CFS" if header["CONSOL_TYPE"] == "연결" else "OFS",
                        "estimate": (header.get("EP_CHK") or "").strip() == "E",
                        "value": number(row.get(header["CD"])),
                        "unit": unit,
                        "formula": row.get("FORMULA"),
                        "source_trace": {**evidence, "period": period, "unit": unit},
                    }
                )
    for table in soup.select("table"):
        caption = table.find("caption")
        if caption and caption.get_text(strip=True) == "주주현황":
            headers = [e.get_text(" ", strip=True) for e in table.select("thead th")]
            if "항목" not in headers:
                continue
            for row in table.select("tbody tr"):
                for hidden in row.select('[style*="display:none"], [style*="display: none"]'):
                    hidden.decompose()
                cells = [e.get_text(" ", strip=True) for e in row.select("th,td")]
                if len(cells) >= 4:
                    result["shareholders"].append(
                        {
                            "name": cells[0],
                            "shares": number(cells[1]),
                            "ratio": number(cells[2]),
                            "as_of": cells[3],
                        }
                    )
    name = soup.select_one("#cmp_nm")
    result["company"]["name"] = name.get("value") if name else code
    paragraphs = []
    for element in soup.select("li, p"):
        text = element.get_text(" ", strip=True)
        if 80 < len(text) < 600 and ("동사는" in text or "주요 사업" in text):
            paragraphs.append(text)
    if paragraphs:
        result["company"]["description"] = paragraphs[0]
    for selector in (".corp_group1", ".corp_group2", ".stxt_group"):
        elements = soup.select(selector)
        if elements:
            result["company"][selector[1:]] = " ".join(
                e.get_text(" ", strip=True) for e in elements
            )
    return result


class PublicProvider:
    def __init__(self, store):
        self.store = store
        self.client = httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers={"User-Agent": "LUXON-PublicDashboard/1.0"}
        )
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    async def fetch(self, url, source="naver"):
        # One request per second per worker, bounded retries and no credentials in logs.
        async with self.lock:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, 1.0 - (loop.time() - self.last_request)))
            for attempt in range(3):
                try:
                    response = await self.client.get(url)
                    response.raise_for_status()
                    self.last_request = loop.time()
                    digest = self.store.raw(source, url, response.text.encode("utf-8"))
                    return response, digest
                except httpx.HTTPError:
                    self.last_request = loop.time()
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** (attempt + 1))

    async def universe(self):
        rows, coverage = [], []
        for market in ("KOSPI", "KOSDAQ"):
            page, fetched, expected, listed = 1, 0, None, 0
            while page <= 60:
                url = f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100"
                response, digest = await self.fetch(url)
                payload = response.json()
                expected = payload["totalCount"]
                items = payload["stocks"]
                for item in items:
                    # ETFs/ETNs stay out of the listed-company universe.
                    if item.get("stockEndType") != "stock":
                        continue
                    listed += 1
                    evidence = trace("naver", url, digest, item.get("localTradedAt"))
                    row = normalize_stock(item, market, evidence)
                    old = self.store.get("detail:" + row["code"])
                    if old:
                        row["facts"] = old.get("facts", {})
                        for key in METRICS:
                            if key in row["facts"]:
                                row[key] = row["facts"][key]["value"]
                    rows.append(row)
                fetched += len(items)
                if fetched >= expected or not items:
                    break
                page += 1
            coverage.append(
                {
                    "market": market,
                    "received": listed,
                    "source_rows_received": fetched,
                    "source_rows_expected": expected,
                    "complete": fetched >= expected,
                    "scope": "상장주식 응답 행 · ETF/ETN 제외",
                }
            )
        unique = {r["code"]: r for r in rows}
        # KIND supplies issuer listings, including KONEX. Missing quotations remain null.
        unavailable = []
        try:
            listings = await self.listings()
            for item in listings:
                if item['market']=='KONEX':
                    continue
                code = item["code"]
                if code in unique:
                    unique[code].update(sector=item["sector"], issuer_id=code, company=item)
                else:
                    unique[code] = {
                        **normalize_stock(
                            {"itemCode": code, "stockName": item["name"]},
                            item["market"],
                            item["source_trace"],
                        ),
                        "sector": item["sector"],
                        "issuer_id": code,
                        "company": item,
                    }
        except (httpx.HTTPError, ValueError, KeyError):
            unavailable.append("KIND")
        snapshot = {
            "rows": list(unique.values()),
            "received_at": now(),
            "coverage": coverage,
            "unavailable_markets": unavailable,
            "mode": "source_observed",
        }
        self.store.put("universe", snapshot)
        return snapshot

    async def listings(self):
        url = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
        response, digest = await self.fetch(url, "kind")
        soup = BeautifulSoup(response.content, "html.parser", from_encoding="euc-kr")
        rows = []
        for tr in soup.select("table tr"):
            cells = [e.get_text(" ", strip=True) for e in tr.select("td")]
            if len(cells) < 7 or not re.fullmatch(r"[A-Z0-9]{6}", cells[2]):
                continue
            market = {
                "유가": "KOSPI",
                "유가증권": "KOSPI",
                "코스닥": "KOSDAQ",
                "코넥스": "KONEX",
            }.get(cells[1])
            if market:
                rows.append(
                    {
                        "code": cells[2],
                        "name": cells[0],
                        "market": market,
                        "sector": cells[3],
                        "products": cells[4],
                        "listed_at": cells[5],
                        "source_trace": trace("kind", url, digest, now()[:10], unit="종목"),
                    }
                )
        source_row_count = len(rows)
        unique = {}
        for row in rows:
            previous = unique.get(row["code"])
            if previous and any(previous[key] != row[key] for key in ("name", "market")):
                raise ValueError("Conflicting KIND listing identity")
            unique.setdefault(row["code"], row)
        rows = list(unique.values())
        if len(rows) < 100:
            raise ValueError("Incomplete KIND listing")
        self.store.put(
            "issuers", {"rows": rows, "received_at": now(), "source_row_count": source_row_count}
        )
        return rows

    async def quote(self, code):
        previous = self.store.get_quote(code)
        if previous.get('quote_trace', {}).get('source') in ('toss', 'toss_websocket'):
            return previous  # Do not overwrite integrated prices with another venue.
        url = f"https://m.stock.naver.com/api/stock/{code}/basic"
        response, digest = await self.fetch(url)
        item = response.json()
        if item.get("itemCode") != code:
            raise ValueError("Quote identity mismatch")
        price = number(item.get("closePrice"))
        change = number(item.get("fluctuationsRatio"))
        if price is None or price <= 0 or change is None:
            raise ValueError("Invalid quote price")
        previous = self.store.get_quote(code)
        as_of = item.get("localTradedAt")
        if as_of and previous.get("as_of") and as_of < previous["as_of"]:
            return
        fact = trace("naver", url, digest, as_of)
        quote = {
            "logo_url": item.get("itemLogoPngUrl") or item.get("itemLogoUrl"),
            "price": price,
            "change": change,
            "as_of": as_of,
            "market_status": item.get("marketStatus"),
            "received_at": now(),
            "quote_trace": fact,
        }
        return self.store.update_quote(code, quote)

    async def indices(self):
        rows = []
        for code in ("KOSPI", "KOSDAQ", "KPI200"):
            url = f"https://m.stock.naver.com/api/index/{code}/basic"
            try:
                response, digest = await self.fetch(url)
                item = response.json()
                rows.append(
                    {
                        "code": code,
                        "name": item["stockName"],
                        "price": number(item["closePrice"]),
                        "change": number(item["fluctuationsRatio"]),
                        "as_of": item["localTradedAt"],
                        "market_status": item["marketStatus"],
                        "source_trace": trace("naver", url, digest, item["localTradedAt"], "pt"),
                    }
                )
            except (httpx.HTTPError, KeyError, ValueError):
                previous = self.store.get("indices", {}).get("rows", [])
                rows.extend(r for r in previous if r["code"] == code)
        self.store.put("indices", {"rows": rows, "received_at": now()})

    async def detail(self, code, include_fundamentals=True):
        url = f"https://m.stock.naver.com/api/stock/{code}/integration"
        response, digest = await self.fetch(url)
        payload = response.json()
        if payload.get("itemCode") != code:
            raise ValueError("Security identity mismatch")
        data = self.store.get("detail:" + code, {})
        data = {
            **data,
            "code": code,
            "received_at": now(),
            "flows": data.get("flows", []),
            "facts": dict(data.get("facts", {})),
            "source_status": dict(data.get("source_status", {})),
        }
        evidence = trace("naver", url, digest, None)
        for item in payload.get("totalInfos") or []:
            metric = {
                "per": "per",
                "pbr": "pbr",
                "dividendYieldRatio": "dividend",
                "accumulatedTradingVolume": "volume",
                "accumulatedTradingValue": "value",
            }.get(item["code"])
            if metric:
                value = number(item.get("value"))
                # A dash or N/A in a new snapshot is not evidence that a previous
                # observation became zero or ceased to exist. Preserve last-good data.
                if value is None:
                    continue
                period = item.get("valueDesc") or evidence.get("period")
                data["facts"][metric] = {
                    "value": value,
                    "period": period,
                    "unit": METRICS[metric]["unit"],
                    "source_trace": {
                        **evidence,
                        "unit": METRICS[metric]["unit"],
                        "period": period or "기준일 미제공",
                        "scope": (
                            "제공처 일중 누적 거래량"
                            if metric == "volume"
                            else "제공처 일중 누적 거래대금"
                            if metric == "value"
                            else "제공처 재무 배수"
                        ),
                    },
                }
        flow_rows = payload.get("dealTrendInfos") or []
        new_flows = []
        for item in flow_rows:
            period = datetime.strptime(item["bizdate"], "%Y%m%d").date().isoformat()
            new_flows.append(
                {
                    "date": period,
                    "foreign": number(item["foreignerPureBuyQuant"]),
                    "institution": number(item["organPureBuyQuant"]),
                    "individual": number(item["individualPureBuyQuant"]),
                    "holding": number(item["foreignerHoldRatio"]),
                    "source_trace": {
                        **evidence,
                        "period": period,
                        "unit": "주",
                        "scope": NAVER_FLOW_SCOPE,
                    },
                }
            )
        flow_key = "naver:investor-trading:" + code
        previous_flows = self.store.metadata(flow_key, {})
        if previous_flows and (previous_flows.get("code") != code
                               or previous_flows.get("aggregation") != NAVER_FLOW_KIND
                               or previous_flows.get("_series_ref") != flow_key):
            raise ValueError("Naver investor source identity mismatch")
        flow_snapshot = {**previous_flows, "code": code, "aggregation": NAVER_FLOW_KIND,
                         "source": "naver", "scope": NAVER_FLOW_SCOPE, "venue": "PROVIDER",
                         "history_complete": False, "attempted_at": now()}
        if new_flows:
            from .time_series import put_series
            put_series(self.store, flow_key, {**flow_snapshot, "rows": new_flows,
                       "status": "ready", "source_trace": {**evidence, "unit": "주",
                           "scope": NAVER_FLOW_SCOPE,
                           "period": max(row['date'] for row in new_flows)}, "received_at": now()},
                       reverse=True)
            flow_snapshot = self.store.metadata(flow_key)
        elif previous_flows:
            flow_snapshot["status"] = "stale" if previous_flows.get("row_count") else "no_data"
            self.store.put(flow_key, flow_snapshot)
        research_rows = payload.get("researches") or []
        if research_rows:
            data["research"] = research_rows
            data["research_received_at"] = now()
        else:
            data.setdefault("research", [])
        if payload.get("consensusInfo"):
            data["consensus"] = payload["consensusInfo"]
        data["brokers"] = data.get("brokers", [])
        data["brokers_status"] = data.get("brokers_status", "거래원 자료 수집 대기")
        data["source_trace"] = evidence
        flow_date = flow_snapshot.get("last_time")
        data["source_status"]["naver_flows"] = {
            "status": flow_snapshot.get("status", "no_data"),
            "source": "네이버 증권 공개 투자자 집계",
            "scope": NAVER_FLOW_SCOPE,
            "received_at": flow_snapshot.get("received_at"),
            "as_of": flow_date,
            "note": None
            if new_flows
            else "최신 응답에 일별 수급 자료가 없습니다."
            if flow_snapshot.get("row_count")
            else "원천 응답에 일별 수급 자료가 없습니다.",
        }
        research_status = (
            "ready" if research_rows else "stale" if data.get("research") else "no_data"
        )
        data["source_status"]["research"] = {
            "status": research_status,
            "source": "네이버 증권 공개 리서치 목록",
            "received_at": data.get("research_received_at") or now(),
            "note": None
            if research_rows
            else "최신 응답에 리서치 항목이 없습니다."
            if data.get("research")
            else "연결된 공개 리서치 자료가 없습니다.",
        }
        if include_fundamentals:
            furl = f"https://wcomp.fnguide.com/CompanyInfo/Snapshot?cmp_cd={code}"
            try:
                response, digest = await self.fetch(furl, "fnguide")
                data.update(
                    parse_fnguide(
                        response.text, code, trace("fnguide", furl, digest, "제공처 표시 기간")
                    )
                )
                data["fundamentals_received_at"] = now()
                has_fundamental_values = any(
                    row.get("value") is not None for row in data.get("financials", [])
                )
                has_company_facts = bool(data.get("company", {}).get("description"))
                has_shareholders = bool(data.get("shareholders"))
                data["source_status"]["fundamentals"] = {
                    "status": "ready"
                    if has_fundamental_values or has_company_facts or has_shareholders
                    else "no_data",
                    "source": "FnGuide 공개 기업 스냅샷",
                    "received_at": data["fundamentals_received_at"],
                    "as_of": max(
                        (
                            row["period"]
                            for row in data.get("financials", [])
                            if isinstance(row.get("period"), str) and row["period"].strip()
                        ),
                        default=None,
                    ),
                    "note": None
                    if has_fundamental_values or has_company_facts or has_shareholders
                    else "기업 페이지 응답은 받았지만 표시 가능한 재무·주주 자료가 없습니다.",
                }
                actuals = [
                    r
                    for r in data["financials"]
                    if not r["estimate"]
                    and r["frequency"] == "annual"
                    and isinstance(r.get("period"), str)
                    and r["period"].strip()
                ]
                for key, name in [
                    ("roe", "ROE"),
                    ("revenue", "매출액"),
                    ("operating_profit", "영업이익"),
                ]:
                    candidates = [
                        r for r in actuals if r["name"] == name and r["value"] is not None
                    ]
                    if candidates:
                        row = max(candidates, key=lambda r: r["period"])
                        data["facts"][key] = {
                            "value": row["value"],
                            "period": row["period"],
                            "unit": row["unit"],
                            "source_trace": row["source_trace"],
                        }
            except (httpx.HTTPError, KeyError, ValueError):
                data["financials_status"] = "재무 원문 수집 실패 · 이전 자료 유지"
                has_previous = bool(data.get("financials") or data.get("shareholders"))
                data["source_status"]["fundamentals"] = {
                    "status": "stale" if has_previous else "error",
                    "source": "FnGuide 공개 기업 스냅샷",
                    "received_at": data.get("fundamentals_received_at"),
                    "attempted_at": now(),
                    "note": "최신 원천 조회에 실패했습니다."
                    + (" 이전 성공 자료를 표시합니다." if has_previous else ""),
                }
        return _publish_detail_preserving_flows(self.store, code, data)

    async def brokers(self, code):
        url = f"https://stock.naver.com/api/domestic/detail/{code}/traderInfo"
        response, digest = await self.fetch(url)
        payload = response.json()
        rows = payload.get("traderList", [])
        if any(row.get("itemcode") != code for row in rows):
            raise ValueError("Broker security identity mismatch")
        detail = self.store.get("detail:" + code, {"code": code})
        detail["brokers"] = sorted(
            [
                {
                    "name": r.get("display_name") or r["nameKr"],
                    "buy": number(r.get("buyQuant")),
                    "sell": number(r.get("sellQuant")),
                    "net": number(r.get("quant")),
                    "as_of": r.get("bizdate"),
                    "source_trace": trace(
                        "naver_traders",
                        url,
                        digest,
                        r.get("bizdate"),
                        "주",
                        "제공처 거래원 집계 · 거래시장 구분 미제공",
                    ),
                }
                for r in rows
            ],
            key=lambda r: (r.get("buy") or 0) + (r.get("sell") or 0),
            reverse=True,
        )
        detail["brokers_status"] = (
            f"{rows[0].get('bizdate', '')} 기준 · 공개 거래원 집계"
            if rows
            else "거래원 자료 미제공"
        )
        detail["brokers_received_at"] = now()
        detail["source_status"] = {
            **detail.get("source_status", {}),
            "brokers": {
                "status": "ready" if rows else "no_data",
                "source": "네이버 증권 공개 거래원 집계",
                "received_at": detail["brokers_received_at"],
                "as_of": rows[0].get("bizdate") if rows else None,
                "note": None if rows else "원천 응답에 거래원 항목이 없습니다.",
            },
        }
        _publish_detail_preserving_flows(self.store, code, detail)

    async def prices(self, code):
        url = f"https://fchart.stock.naver.com/sise.nhn?timeframe=day&count=20000&requestType=0&symbol={code}"
        response, digest = await self.fetch(url)
        rows = []
        for raw in re.findall(r'<item data="([^"]+)"', response.text):
            parts = raw.split("|")
            if len(parts) != 6:
                continue
            day = datetime.strptime(parts[0], "%Y%m%d").date().isoformat()
            values = [number(x) for x in parts[1:]]
            if values[3] is None or values[3] <= 0:
                continue
            rows.append(
                dict(
                    date=day,
                    open=values[0],
                    high=values[1],
                    low=values[2],
                    close=values[3],
                    volume=values[4],
                )
            )
        if not rows:
            raise ValueError("No historical prices")
        result = {
            "rows": sorted({r["date"]: r for r in rows}.values(), key=lambda r: r["date"]),
            "adjustment": "제공처 일봉 · 조정 방식 미확인",
            "received_at": now(),
            "source_trace": trace("naver", url, digest, rows[-1]["date"]),
            # This endpoint can cap its response below the requested count.
            # A short response is therefore not evidence of listing-to-date coverage.
            "requested_count": 20000,
            "response_rows": len(rows),
            "history_complete": False,
            "history_status": "provider_window_only",
        }
        from .time_series import put_series
        key = "prices:" + code
        previous = self.store.metadata(key,{})
        old_source = (previous.get('source_trace') or {}).get('source')
        if previous and old_source not in (None,'naver'):
            raise ValueError('Historical price provider mismatch')
        # Legacy snapshots move without losing rows outside today's response.
        merged = {r['date']: {**r, 'source_trace': r.get('source_trace')
                             or previous.get('source_trace', {})}
                  for r in previous.get('rows', [])}
        merged.update({r['date']: {**r, 'source_trace': result['source_trace']}
                       for r in result['rows']})
        put_series(self.store,key,{**result,'rows':list(merged.values())})
        return self.store.get(key)
