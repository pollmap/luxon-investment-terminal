"""Independent short-sale trades/balances; no inferred balances or credentials in traces."""

import asyncio
import calendar
import logging
import os
import shutil
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .collection import update_collection
from .history_worker import elapsed
from .providers import number, trace
from .store import now

log = logging.getLogger("market-short-selling")
KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_PAGE = "https://data.krx.co.kr/contents/MDC/STAT/srt/MDCSTAT300.jsp"
FIELDS = {
    "trades": (
        "TRD_DD",
        {
            "volume": "CVSRTSELL_TRDVOL",
            "value": "CVSRTSELL_TRDVAL",
            "total_volume": "ACC_TRDVOL",
            "total_value": "ACC_TRDVAL",
            "ratio": "TRDVOL_WT",
            "value_ratio": "TRDVAL_WT",
        },
    ),
    "balance": (
        "RPT_DUTY_OCCR_DD",
        {
            "volume": "BAL_QTY",
            "value": "BAL_AMT",
            "listed_shares": "LIST_SHRS",
            "ratio": "BAL_RTO",
        },
    ),
}


def trading_date(value):
    value = str(value).replace("/", "").replace("-", "")
    return datetime.strptime(value, "%Y%m%d").date().isoformat()


def parse_rows(items, kind, evidence):
    if not isinstance(items, list):
        raise ValueError("Short selling response is not a list")
    date_key, fields = FIELDS[kind]
    result = {}
    for item in items:
        if not isinstance(item, dict) or date_key not in item or fields["volume"] not in item:
            raise ValueError("Short selling schema changed")
        day = trading_date(item[date_key])
        row = {key: number(item.get(source)) for key, source in fields.items()}
        if any(value is not None and value < 0 for value in row.values()):
            raise ValueError("Negative short selling observation")
        if any(row.get(key) is not None and row[key] > 100 for key in ("ratio", "value_ratio")):
            raise ValueError("Invalid short selling ratio")
        row.update(date=day, source_trace={**evidence, "period": day})
        if day in result and result[day] != row:
            raise ValueError("Conflicting short selling dates")
        result[day] = row
    return sorted(result.values(), key=lambda row: row["date"])


class ShortSellingCollector:
    def __init__(self, provider, authenticated):
        self.provider, self.authenticated = provider, authenticated
        self.store = provider.store
        self.circuits = {}

    async def krx(self, bld, **params):
        data = {"bld": bld, "locale": "ko_KR", **params}
        async with self.provider.lock:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, 1 - (loop.time() - self.provider.last_request)))
            try:
                response = await self.provider.client.post(
                    KRX_URL,
                    data=data,
                    headers={"Referer": KRX_PAGE},
                )
                response.raise_for_status()
            finally:
                self.provider.last_request = loop.time()
        url = KRX_URL + "?" + urlencode(data)
        digest = self.store.raw("krx_short_selling", url, response.content)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("KRX response is not an object")
        return payload, url, digest

    async def isin(self, code):
        cached = self.store.get("krx-identity:" + code, {})
        if cached.get("isin") and elapsed(cached.get("received_at")) < 7 * 86400:
            return cached["isin"]
        payload, url, digest = await self.krx(
            "dbms/comm/finder/finder_stkisu",
            mktsel="ALL",
            searchText=code,
            typeNo="0",
        )
        matches = [item for item in payload.get("block1", []) if item.get("short_code") == code]
        if len(matches) != 1 or len(matches[0].get("full_code", "")) != 12:
            raise ValueError("KRX security identity could not be verified")
        isin = matches[0]["full_code"]
        self.store.put(
            "krx-identity:" + code,
            {
                "isin": isin,
                "received_at": now(),
                "source_trace": trace("krx", url, digest, now()[:10], unit="종목"),
            },
        )
        return isin

    async def collect_krx(self, code, kind, start, end):
        isin = await self.isin(code)
        bld = "MDCSTAT30102" if kind == "trades" else "MDCSTAT30502"
        payload, url, digest = await self.krx(
            "dbms/MDC/STAT/srt/" + bld,
            isuCd=isin,
            strtDd=start,
            endDd=end,
        )
        if "OutBlock_1" not in payload:
            raise ValueError("KRX data block missing")
        evidence = trace("krx", url, digest, end, unit="주·원·%", scope="KRX 공매도 통계")
        return parse_rows(payload["OutBlock_1"], kind, evidence), "KRX"


    async def refresh(self, code):
        snapshot = dict(self.store.get("short-selling:" + code, {}))
        today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        end, start = today.strftime("%Y%m%d"), (today - timedelta(days=366)).strftime("%Y%m%d")
        changed = False
        for kind in ("balance",):
            previous = snapshot.get(kind, {})
            if elapsed(previous.get("checked_at")) < (
                21600 if previous.get("status") in ("ready", "no_data") else 300
            ):
                continue
            candidates = [("krx", self.collect_krx)]
            rows = None
            source = previous.get("source")
            for provider, collect in candidates:
                if elapsed(self.circuits.get(provider)) < 300:
                    continue
                try:
                    if provider == "krx":
                        rows, source = await collect(code, kind, start, end)
                    else:
                        rows, source = await collect(code, start, end)
                    break
                except Exception as exc:
                    self.circuits[provider] = now()
                    log.warning("Short selling %s %s: %s", code, kind, type(exc).__name__)
            changed = True
            if rows is None:
                snapshot[kind] = {
                    **previous,
                    "status": "stale" if previous.get("rows") else "error",
                    "checked_at": now(),
                }
            elif not rows and previous.get("rows"):
                # An empty response must not erase previously observed balances.
                snapshot[kind] = {**previous, "status": "stale", "checked_at": now()}
            else:
                snapshot[kind] = {
                    "status": "ready" if rows else "no_data",
                    "rows": sorted({**{r["date"]:r for r in previous.get("rows", [])}, **{r["date"]:r for r in rows}}.values(), key=lambda r:r["date"]),
                    "source": source,
                    "received_at": now(),
                    "checked_at": now(),
                }
            update_collection(
                self.store,
                code,
                {
                    "short_" + kind: {
                        "status": snapshot[kind]["status"],
                        "source": source,
                        "received_at": snapshot[kind].get("received_at"),
                        "as_of": snapshot[kind].get("rows", [{}])[-1].get("date")
                        if snapshot[kind].get("rows")
                        else None,
                    }
                },
            )
        if changed:
            snapshot.update(code=code, requested_range={"start": start, "end": end})
            self.store.put("short-selling:" + code, snapshot)
        return snapshot


async def short_selling_loop(store, collector):
    while True:
        try:
            codes = list(store.listed_codes())
            known = set(codes)
            cursor = int(store.get("short-progress", {}).get("cursor", 0)) % max(1, len(codes))
            active = [code for code in store.active() if code in known]
            free = shutil.disk_usage(store.root).free
            reserve = max(256, int(os.getenv("MARKET_SHORT_RESERVE_MB", "1024"))) * 1024**2
            background = codes[cursor : cursor + 1] if free > reserve else []
            targets = list(dict.fromkeys(active + background)) if free > 256 * 1024**2 else []
            for code in targets:
                await collector.refresh(code)
            store.put(
                "short-progress",
                {
                    "cursor": (cursor + 1) % max(1, len(codes)) if background else cursor,
                    "status": "ready" if free > reserve else "capacity_limited",
                    "updated_at": now(),
                },
            )
        except Exception as exc:
            log.warning("Short selling queue: %s", type(exc).__name__)
        await asyncio.sleep(15)


def public_short_selling(snapshot, period):
    result = {"code": snapshot.get("code"), "period": period}
    for kind in FIELDS:
        section = snapshot.get(kind, {})
        rows = section.get("rows", [])
        if rows and period != "all":
            last = datetime.fromisoformat(rows[-1]["date"]).date()
            month_index = (
                last.year * 12 + last.month - 1 - {"month": 1, "quarter": 3, "year": 12, "three_years":36, "five_years":60}[period]
            )
            year, month = month_index // 12, month_index % 12 + 1
            cutoff = last.replace(
                year=year, month=month, day=min(last.day, calendar.monthrange(year, month)[1])
            ).isoformat()
            rows = [row for row in rows if row["date"] >= cutoff]
        # Keep full provenance internally; the phone only needs dates and numeric columns.
        columns = ("date", *FIELDS[kind][1])
        result[kind] = {
            "status": section.get("status", "pending"),
            "rows": [{key: row.get(key) for key in columns} for row in rows],
        }
    return result


if __name__ == "__main__":
    import argparse

    from .authenticated_sources import AuthenticatedSources
    from .providers import PublicProvider
    from .runtime import load_worker_credentials
    from .store import Store

    parser = argparse.ArgumentParser(description="Collect public short-selling data independently")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--codes", nargs="*", default=[])
    args = parser.parse_args()

    async def run():
        load_worker_credentials()
        store = Store()
        provider = PublicProvider(store)
        collector = ShortSellingCollector(provider, AuthenticatedSources(provider))
        known = {row["code"] for row in store.get("universe", {}).get("rows", [])}
        if any(code not in known for code in args.codes):
            raise ValueError("Requested security is not in the listed universe")
        try:
            if args.once:
                for code in args.codes or store.active():
                    result = await collector.refresh(code)
                    print(code, {kind: result.get(kind, {}).get("status") for kind in FIELDS})
            else:
                await short_selling_loop(store, collector)
        finally:
            await provider.client.aclose()

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run())
