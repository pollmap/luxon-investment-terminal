"""Approved KRX stock APIs, separate from the KRX website short-selling collector.

Official contracts: openapi.krx.co.kr, stock basic information and daily trading.
The key is sent only in AUTH_KEY. Sample endpoints are never production sources.
"""

import asyncio
import os
import re
from datetime import UTC, date, datetime, timedelta

from .analysis import numeric
from .store import now

BASE = "https://data-dbg.krx.co.kr/svc/apis/sto/"
APIS = {
    "KOSPI": ("stk_isu_base_info", "stk_bydd_trd"),
    "KOSDAQ": ("ksq_isu_base_info", "ksq_bydd_trd"),
}


def day(value):
    text = str(value or "").strip().replace("/", "").replace("-", "")
    if not re.fullmatch(r"\d{8}", text):
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def amount(value, *, positive=False):
    value = numeric(str(value).replace(",", "").strip())
    if value is None or (positive and value <= 0):
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def identities(rows, market, as_of, evidence):
    output, excluded = {}, []
    for row in rows:
        code, isin = str(row.get("ISU_SRT_CD", "")), str(row.get("ISU_CD", ""))
        kind = str(row.get("KIND_STKCERT_TP_NM", ""))
        share_class = "ordinary" if kind == "보통주" else "preferred" if "우선주" in kind else None
        if (
            not re.fullmatch(r"[A-Z0-9]{6}", code)
            or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin)
            or not share_class
            or row.get("SECUGRP_NM") != "주권"
            or not day(row.get("LIST_DD"))
            or day(row.get("LIST_DD")) > as_of
        ):
            excluded.append({"code": code, "kind": kind, "reason": "identity_or_scope_unverified"})
            continue
        item = {
            "code": code,
            "isin": isin,
            "name": row.get("ISU_ABBRV") or row.get("ISU_NM"),
            "market": market,
            "share_class": share_class,
            "share_class_label": kind,
            "listing_date": day(row.get("LIST_DD")),
            "as_of": as_of,
            "listed_shares": amount(row.get("LIST_SHRS"), positive=True),
            "source_trace": evidence,
        }
        if code in output and output[code] != item:
            raise ValueError("Conflicting security identity")
        output[code] = item
    return output, excluded


def daily_rows(rows, securities, as_of, evidence):
    by_isin = {row["isin"]: code for code, row in securities.items()}
    output, rejected = {}, []
    for row in rows:
        identity = str(row.get("ISU_CD", ""))
        code = identity if identity in securities else by_isin.get(identity)
        if code is None or day(row.get("BAS_DD")) != as_of:
            rejected.append({"identity": identity, "reason": "identity_or_date_mismatch"})
            continue
        values = {
            key: amount(row.get(field), positive=True)
            for key, field in (
                ("open", "TDD_OPNPRC"),
                ("high", "TDD_HGPRC"),
                ("low", "TDD_LWPRC"),
                ("close", "TDD_CLSPRC"),
            )
        }
        volume = amount(row.get("ACC_TRDVOL"))
        turnover = amount(row.get("ACC_TRDVAL"))
        if values["close"] is None or any(v is not None and v < 0 for v in (volume, turnover)):
            rejected.append({"identity": identity, "reason": "invalid_price_or_quantity"})
            continue
        if all(value is not None for value in values.values()) and not (
            values["low"]
            <= min(values["open"], values["close"])
            <= max(values["open"], values["close"])
            <= values["high"]
        ):
            rejected.append({"identity": identity, "reason": "invalid_ohlc"})
            continue
        output[code] = {
            "date": as_of,
            **values,
            "volume": volume,
            "value": turnover,
            "cap": amount(row.get("MKTCAP"), positive=True),
            "change": amount(row.get("FLUC_RT")),
            "source_trace": evidence,
        }
    return output, rejected


class KrxOpenApi:
    def __init__(self, store, client, *, api_key=None):
        self.store, self.client = store, client
        self.key = (api_key if api_key is not None else os.getenv("MARKET_KRX_API_KEY", "")).strip()
        self.lock, self.last_request = asyncio.Lock(), 0.0
        self.daily_budget = max(1, min(10000, int(os.getenv("MARKET_KRX_DAILY_BUDGET", "120"))))

    async def get(self, api, as_of):
        if api not in {item for pair in APIS.values() for item in pair} or day(as_of) != as_of:
            raise ValueError("Unsupported KRX stock query")
        if not self.key:
            raise RuntimeError("KRX credentials not configured")
        from zoneinfo import ZoneInfo

        stamp = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        async with self.lock:
            budget = self.store.get("krx-api-budget:" + stamp, {"requests": 0})
            if budget["requests"] >= self.daily_budget:
                raise RuntimeError("KRX local daily request budget reached")
            self.store.put("krx-api-budget:" + stamp, {"requests": budget["requests"] + 1})
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, 1 - (loop.time() - self.last_request)))
            try:
                response = await self.client.get(
                    BASE + api,
                    params={"basDd": as_of.replace("-", "")},
                    headers={"AUTH_KEY": self.key},
                )
                response.raise_for_status()
            finally:
                self.last_request = loop.time()
        url = BASE + api + "?basDd=" + as_of.replace("-", "")
        digest = self.store.raw("krx-openapi", url, response.content)
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("OutBlock_1"), list):
            raise ValueError("KRX response schema changed or service is not approved")
        evidence = {
            "source": "krx_openapi",
            "source_url": url,
            "sha256": digest,
            "period": as_of,
            "available_at": now(),
            "received_at": now(),
            "unit": "KRW/shares",
            "currency": "KRW",
            "method": "official_daily_api",
        }
        return payload["OutBlock_1"], evidence

    async def collect_day(self, market, as_of):
        if market not in APIS or day(as_of) != as_of:
            raise ValueError("Invalid KRX collection scope")
        key = f"krx-day:{market}:{as_of}"
        previous = self.store.get(key, {})
        if not self.key:
            return {"status": "credentials_missing"}
        if previous.get("status") == "ready":
            return previous
        if previous.get("retry_after", "") > now():
            return previous
        try:
            base_rows, base_trace = await self.get(APIS[market][0], as_of)
            prices, price_trace = await self.get(APIS[market][1], as_of)
            if not base_rows or not prices:
                result = {"status": "empty_response", "as_of": as_of, "received_at": now()}
            else:
                stocks, excluded = identities(base_rows, market, as_of, base_trace)
                bars, rejected = daily_rows(prices, stocks, as_of, price_trace)
                if not stocks or not bars:
                    raise ValueError("No verified in-scope KRX stocks")
                self.store.put(
                    f"krx-listings:{market}:{as_of}",
                    {
                        "rows": list(stocks.values()),
                        "excluded": excluded,
                        "as_of": as_of,
                        "source_trace": base_trace,
                    },
                )
                existing = {r["code"]: r for r in self.store.get("universe", {}).get("rows", [])}
                for code, bar in bars.items():
                    identity_key = "identity:krx:" + code
                    if self.store.get(identity_key, {}).get("as_of", "") <= as_of:
                        self.store.put(identity_key, stocks[code])
                    # Keep primary-provider history intact. A separate snapshot is
                    # available to the explicit same-venue raw-price fallback.
                    history_key = f"krx-bars:{code}:KRX:raw"
                    history = self.store.get(history_key, {})
                    merged = {r["date"]: r for r in history.get("rows", [])}
                    merged[as_of] = bar
                    self.store.put(
                        history_key,
                        {
                            "code": code,
                            "venue": "KRX",
                            "adjustment": "raw",
                            "interval": "day",
                            "currency": "KRW",
                            "rows": [merged[d] for d in sorted(merged)],
                            "received_at": now(),
                            "source_trace": price_trace,
                        },
                    )
                known = {code for code, r in existing.items() if r.get("market") == market}
                result = {
                    "status": "ready" if not rejected else "partial",
                    "as_of": as_of,
                    "stocks": len(stocks),
                    "bars": len(bars),
                    "excluded": excluded,
                    "rejected": rejected,
                    "received_at": now(),
                    "listing_comparison": {
                        "new_candidates": sorted(set(stocks) - known),
                        "missing_from_response": sorted(known - set(stocks)),
                        "removal_applied": False,
                    },
                }
        except Exception as exc:
            result = {"status": "error", "error": type(exc).__name__, "received_at": now()}
        if result["status"] != "ready":
            result["retry_after"] = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
        self.store.put(key, result)
        return result


async def krx_loop(source):
    """Advance a persisted day/market checkpoint within the per-key request budget."""
    from zoneinfo import ZoneInfo

    while True:
        if source.key:
            today = datetime.now(ZoneInfo("Asia/Seoul")).date()
            current = (today - timedelta(days=1)).isoformat()
            for market in APIS:
                state = source.store.get("krx-progress:" + market, {})
                # Refresh the latest session first, then backfill one older date.
                await source.collect_day(market, current)
                cursor = state.get("cursor") or (today - timedelta(days=2)).isoformat()
                earliest = date(today.year - 10, today.month, min(today.day, 28)).isoformat()
                if cursor >= earliest:
                    result = await source.collect_day(market, cursor)
                    if result["status"] in ("ready", "empty_response"):
                        source.store.put(
                            "krx-progress:" + market,
                            {
                                "cursor": (
                                    date.fromisoformat(cursor) - timedelta(days=1)
                                ).isoformat(),
                                "updated_at": now(),
                            },
                        )
        await asyncio.sleep(60)
