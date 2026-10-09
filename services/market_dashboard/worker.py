from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from datetime import UTC, datetime

from .authenticated_sources import AuthenticatedSources, DartDailyBudgetExceeded
from .collection import SOURCES, update_collection
from .dart_periods import DartPeriodCollector
from .xbrl_facts import xbrl_loop
from .history_worker import history_loop, refresh_history
from .krx_openapi import KrxOpenApi, krx_loop
from .providers import PublicProvider
from .runtime import load_worker_credentials
from .short_selling import ShortSellingCollector, short_selling_loop
from .store import Store, now
from .toss import TossCollector
from .news import news_loop
from .financial_worker import financial_reports_loop
from .reviewed_eps_tables import reviewed_eps_loop
from .corporate_events import corporate_events_loop
from .market_context import market_context_loop
from .share_basis_worker import share_basis_review_loop

log = logging.getLogger("market-worker")


def update_worker_state(store, **changes):
    """Update one worker result without erasing the independent process heartbeat."""
    store.put("worker", {**store.get("worker", {}), **changes})


async def worker_heartbeat_loop(store, interval=30):
    """Liveness only: never freshen collected data or override a failed result."""
    while True:
        try:
            update_worker_state(store, process_heartbeat_at=now())
        except Exception as exc:
            log.warning("Worker heartbeat write failed: %s", type(exc).__name__)
        await asyncio.sleep(interval)


def publish_enriched(store, detail):
    from .enriched_view import publish
    publish(store, detail)


def age(payload, key="received_at"):
    try:
        value = datetime.fromisoformat(payload[key].replace("Z", "+00:00"))
        return max(0, (datetime.now(UTC) - value.astimezone(UTC)).total_seconds())
    except (KeyError, TypeError, ValueError, AttributeError):
        return float("inf")


def source_result(status, source, *, note=None, received_at=None, as_of=None):
    return {
        "status": status,
        "source": source,
        "received_at": received_at,
        "as_of": as_of,
        "note": note,
    }


def has_official_financial_data(official):
    if official.get("statements"):
        return True
    reports = official.get("financial_reports") or {}
    if any(report.get("status") == "ready" and report.get("rows") for report in reports.values()):
        return True
    indicators = official.get("financial_indicators") or {}
    return any(item.get("status") == "ready" and item.get("rows") for item in indicators.values())




def next_konex_target(rows, cursor=0):
    """Return one fair, rotating KONEX enrichment target and the next cursor."""
    codes = [row["code"] for row in rows if row.get("market") == "KONEX"]
    if not codes:
        return None, 0
    index = int(cursor) % len(codes)
    return codes[index], (index + 1) % len(codes)


async def run(once=False):
    load_worker_credentials()
    store = Store()
    provider = PublicProvider(store)
    authenticated = AuthenticatedSources(provider)
    period_collector = DartPeriodCollector(authenticated)
    short_selling = ShortSellingCollector(provider, authenticated)
    toss = TossCollector(store)

    async def market_loop():
        while True:
            started = time.monotonic()
            try:
                update_worker_state(store, status="collecting", updated_at=now())
                universe = await provider.universe()
                await provider.indices()
                archive_day = now()[:10]
                if store.get("universe-archive-day") != archive_day:
                    store.archive("universe", universe["rows"])
                    store.put("universe-archive-day", archive_day)
                eligible_securities = len(store.listed_codes())
                log.info("Collected %s in-scope securities", eligible_securities)
                update_worker_state(
                    store, status="ready", updated_at=now(), error=None,
                    market_collection_seconds=round(time.monotonic() - started, 2),
                    quote_policy="공유 원천 속도 제한 · 요청 종목 우선 갱신",
                    listed_securities=eligible_securities,
                )
            except Exception as exc:
                update_worker_state(
                    store, status="error", updated_at=now(), error=type(exc).__name__
                )
                log.warning("Market collection failed: %s", type(exc).__name__)
            if once:
                return
            await asyncio.sleep(
                max(
                    15,
                    float(os.getenv("MARKET_UNIVERSE_INTERVAL", "300"))
                    - (time.monotonic() - started),
                )
            )

    async def market_filings_loop():
        while True:
            if not authenticated.dart:
                store.put(
                    "market-filings",
                    {
                        "rows": [],
                        "status": "unavailable",
                        "scope": "OpenDART 인증키가 설정되지 않았습니다.",
                    },
                )
            else:
                try:
                    await authenticated.market_filings()
                except Exception as exc:
                    previous = store.get("market-filings", {})
                    store.put(
                        "market-filings",
                        {
                            **previous,
                            "status": "stale" if previous.get("rows") else "error",
                            "last_error": type(exc).__name__,
                        },
                    )
                    log.warning("Market disclosures unavailable: %s", type(exc).__name__)
            if once:
                return
            await asyncio.sleep(max(60, float(os.getenv("MARKET_FILINGS_INTERVAL", "900"))))

    async def dated_accounts_loop():
        from .storage_budget import collection_capacity
        while True:
            delay = 3
            try:
                if collection_capacity(store, 'dated-accounts')['limited']:
                    delay = 60
                elif authenticated.dart:
                    result = await period_collector.collect(store.listed_codes())
                    if result.get("status") != "collecting":
                        delay = 60
                else:
                    delay = 60
            except Exception as exc:
                log.warning("OpenDART dated accounts: %s", type(exc).__name__)
                delay = 60
            await asyncio.sleep(delay)

    async def detail_loop():
        while True:
            universe = store.get("universe", {"rows": []})
            # Stable code order makes the persisted cursor fair even when ranks move.
            from .service_scope import included
            ordered = sorted((r for r in universe["rows"] if included(r)), key=lambda r: r["code"])
            cursor = store.get("enrichment_cursor", {"index": 0})["index"]
            start = cursor % max(1, len(ordered))
            batch_size = max(1, min(20, int(os.getenv("MARKET_ENRICHMENT_BATCH", "3"))))
            background = ordered[start : start + batch_size] if ordered else []
            konex_code, next_konex_cursor = next_konex_target(
                ordered, store.get("konex_enrichment_cursor", {}).get("index", 0)
            )
            active = store.active()
            targets = list(
                dict.fromkeys(
                    active
                    + [r["code"] for r in (ordered[:16] if once else background)]
                    + ([konex_code] if konex_code else [])
                )
            )
            if not once and authenticated.dart and ordered:
                try:
                    result = await authenticated.major_accounts_batch(
                        [row["code"] for row in ordered]
                    )
                    for code, batch_status in result.get("codes", {}).items():
                        official = store.get("official:" + code, {})
                        has_company = bool(official.get("company"))
                        has_statements = has_official_financial_data(official)
                        has_full_statements = bool(
                            (
                                official.get("financials_received_at")
                                or official.get("financial_data_received_at")
                            )
                            and has_statements
                        )
                        source_status = (
                            "ready"
                            if has_company and has_full_statements
                            else "partial"
                            if has_company or has_statements
                            else batch_status
                        )
                        update_collection(
                            store,
                            code,
                            {
                                "official": source_result(
                                    source_status,
                                    "OpenDART",
                                    note=official.get("major_accounts_scope"),
                                    received_at=official.get("major_accounts_received_at")
                                    or official.get("financial_data_received_at")
                                    or official.get("financials_received_at")
                                    or official.get("company_received_at"),
                                    as_of=official.get("major_accounts_period"),
                                )
                            },
                        )
                    if result.get("processed"):
                        log.info(
                            "OpenDART annual major accounts: %s securities (%s)",
                            result["processed"],
                            result["status"],
                        )
                    elif result.get("status") in ("error", "rate_limited"):
                        log.warning("OpenDART annual major accounts deferred: %s", result["status"])
                except Exception as exc:
                    log.warning("OpenDART annual major accounts failed: %s", type(exc).__name__)
            for code in targets:
                current_status = store.get("collection:" + code, {})
                last_attempt = current_status.get("last_attempt_at")
                if current_status.get("attempts") and age({"received_at": last_attempt}) < 60:
                    continue
                update_collection(store, code, attempted=True)
                updates = {}
                failures = []
                previous = store.get("detail:" + code, {})
                previous_coverage = current_status.get("sources", {})
                fundamental_state = previous.get("source_status", {}).get(
                    "fundamentals", previous_coverage.get("fundamentals", {})
                )
                fundamental_age = age(
                    {
                        "received_at": fundamental_state.get("received_at")
                        or fundamental_state.get("attempted_at")
                    }
                )
                refresh = fundamental_age > 86400
                try:
                    detail = await provider.detail(code, include_fundamentals=refresh)
                    publish_enriched(store, detail)
                    updates.update(
                        {
                            key: value
                            for key, value in detail.get("source_status", {}).items()
                            if key in SOURCES
                        }
                    )
                except Exception as exc:
                    failures.append(type(exc).__name__)
                    log.warning("Detail %s failed: %s", code, type(exc).__name__)
                    for key in ("flows", "fundamentals"):
                        old = previous_coverage.get(key, {})
                        if key not in updates:
                            updates[key] = source_result(
                                "stale"
                                if old.get("status") in ("ready", "partial", "stale")
                                else "error",
                                old.get("source") or "네이버 증권 / FnGuide",
                                note="최신 종목 상세 원천 조회에 실패했습니다.",
                                received_at=old.get("received_at"),
                            )

                if once:
                    await refresh_history(store, provider, code)

                # Independent source reads continue even when another provider fails.
                filing_state = previous_coverage.get("filings", {})
                filing_age = age({"received_at": filing_state.get("received_at")})
                filing_retry_age = age(
                    {
                        "received_at": filing_state.get("failed_at")
                        or filing_state.get("attempted_at")
                    }
                )
                if filing_state.get("status") == "rate_limited":
                    filing_retry_age = age({"received_at": filing_state.get("failed_at")})
                if not authenticated.dart:
                    updates["filings"] = source_result(
                        "unavailable",
                        "OpenDART",
                        note="서버에 OpenDART 인증키가 설정되지 않았습니다.",
                    )
                    updates["official"] = source_result(
                        "unavailable", "OpenDART", note="OpenDART 인증키가 설정되지 않았습니다."
                    )
                elif (filing_state.get("status") == "partial" and filing_age > 30) or (filing_age > 86400 and filing_retry_age > (
                    86400 if filing_state.get("status") == "rate_limited" else 1800
                )):
                    try:
                        await authenticated.filings(code)
                        filing_snapshot = store.get("filings:" + code, {})
                        official_snapshot = store.get("official:" + code, {})
                        filing_status = filing_snapshot.get("status", "pending")
                        if filing_status == "DART 고유번호 연결 자료 없음":
                            filing_status = "unavailable"
                        updates["filings"] = source_result(
                            filing_status,
                            "OpenDART",
                            note=(
                                "DART 종목코드와 법인 고유번호 연결을 찾지 못했습니다."
                                if filing_status == "unavailable"
                                else None
                            ),
                            received_at=filing_snapshot.get("received_at"),
                        )
                        if filing_status == "unavailable":
                            updates["official"] = source_result(
                                "unavailable",
                                "OpenDART",
                                note="종목코드에 연결되는 DART 법인 고유번호가 없습니다.",
                                received_at=filing_snapshot.get("received_at"),
                            )
                        elif official_snapshot:
                            has_company = bool(official_snapshot.get("company"))
                            has_statements = has_official_financial_data(official_snapshot)
                            official_status = (
                                "ready"
                                if has_company and has_statements
                                else "partial"
                                if has_company or has_statements
                                else "no_data"
                            )
                            updates["official"] = source_result(
                                official_status,
                                "OpenDART",
                                note=official_snapshot.get("financials_status"),
                                received_at=official_snapshot.get("financial_data_received_at")
                                or official_snapshot.get("financials_received_at")
                                or official_snapshot.get("company_received_at"),
                            )
                        else:
                            updates["official"] = source_result(
                                "no_data",
                                "OpenDART",
                                note="기업정보·재무 응답을 아직 받지 못했습니다.",
                            )
                    except DartDailyBudgetExceeded:
                        updates["filings"] = source_result(
                            "rate_limited",
                            "OpenDART",
                            note=(
                                "일일 요청 안전 한도에 도달했습니다. 다음 기준일에 이어 수집합니다."
                            ),
                        )
                        if not previous.get("official"):
                            updates["official"] = source_result(
                                "rate_limited",
                                "OpenDART",
                                note="기업 개황·재무 요청이 일일 안전 한도 뒤에 남아 있습니다.",
                            )
                        failures.append("DartDailyBudgetExceeded")
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                        old = filing_state
                        updates["filings"] = source_result(
                            "stale"
                            if old.get("status") in ("ready", "partial", "stale")
                            else "error",
                            "OpenDART",
                            note="최신 공시 원천 조회에 실패했습니다.",
                            received_at=old.get("received_at"),
                        )
                        official_old = previous_coverage.get("official", {})
                        updates["official"] = source_result(
                            "stale"
                            if official_old.get("status") in ("ready", "partial", "stale")
                            else "error",
                            "OpenDART",
                            note="최신 기업 개황·재무 원천 조회에 실패했습니다.",
                            received_at=official_old.get("received_at"),
                        )
                        log.warning("DART %s failed: %s", code, type(exc).__name__)

                if authenticated.dart and code in active:
                    try:
                        financial_result = await authenticated.refresh_financial_data(
                            code, max_tasks=3
                        )
                        if financial_result.get("status") in ("error", "rate_limited"):
                            log.info(
                                "OpenDART financial data %s: %s",
                                code,
                                financial_result["status"],
                            )
                        if financial_result.get("processed"):
                            official_snapshot = store.get("official:" + code, {})
                            has_company = bool(official_snapshot.get("company"))
                            has_financial_data = has_official_financial_data(official_snapshot)
                            updates["official"] = source_result(
                                "ready"
                                if has_company and has_financial_data
                                else "partial"
                                if has_company or has_financial_data
                                else "pending",
                                "OpenDART",
                                received_at=official_snapshot.get("financial_data_received_at")
                                or official_snapshot.get("company_received_at"),
                            )
                    except Exception as exc:
                        log.warning(
                            "OpenDART financial data %s failed: %s",
                            code,
                            type(exc).__name__,
                        )

                if authenticated.dart:
                    try:
                        report_result = await authenticated.periodic_report_facts(code)
                        if report_result.get("status") not in ("current", "ready", "no_data"):
                            log.info(
                                "OpenDART company report %s: %s",
                                report_result.get("section") or "",
                                report_result.get("status"),
                            )
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                        log.warning("DART report facts %s failed: %s", code, type(exc).__name__)

                broker_state = previous_coverage.get("brokers", {})
                broker_snapshot = previous.get("source_status", {}).get("brokers", broker_state)
                broker_age = age({"received_at": broker_snapshot.get("received_at")})
                if code in active or broker_age > 86400:
                    try:
                        await provider.brokers(code)
                        latest = store.get("detail:" + code, {})
                        updates["brokers"] = latest.get("source_status", {}).get(
                            "brokers",
                            source_result(
                                "no_data",
                                "네이버 증권 공개 거래원 집계",
                                note="원천 응답에 거래원 항목이 없습니다.",
                                received_at=latest.get("brokers_received_at"),
                            ),
                        )
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                        old_rows = previous.get("brokers", [])
                        updates["brokers"] = source_result(
                            "stale" if old_rows else "error",
                            broker_state.get("source") or "공개 거래원 원천",
                            note="최신 거래원 원천 조회에 실패했습니다."
                            + (" 이전 자료를 표시합니다." if old_rows else ""),
                            received_at=broker_state.get("received_at"),
                        )
                        log.warning("Broker %s failed: %s", code, type(exc).__name__)

                if code in active or code == konex_code:
                    try:
                        quote = await provider.quote(code)
                        if quote:
                            updates["quote"] = source_result(
                                "ready",
                                "네이버 증권 종목 시세",
                                received_at=quote.get("received_at"),
                                as_of=quote.get("as_of"),
                            )
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                        log.warning("Quote %s failed: %s", code, type(exc).__name__)

                update_collection(
                    store,
                    code,
                    updates,
                    error=",".join(dict.fromkeys(failures)) if failures else None,
                )
            next_cursor = (cursor + len(background)) % max(1, len(ordered))
            store.put("enrichment_cursor", {"index": next_cursor, "updated_at": now()})
            store.put(
                "enrichment_progress",
                {
                    "cursor": next_cursor,
                    "universe_total": len(ordered),
                    "batch_size": batch_size,
                    "last_batch_at": now(),
                },
            )
            store.put(
                "konex_enrichment_cursor",
                {"index": next_konex_cursor, "updated_at": now()},
            )
            if once:
                return
            await asyncio.sleep(5)



    try:
        if once:
            await market_loop()
            await market_filings_loop()
            await detail_loop()
            for code in store.active():
                await short_selling.refresh(code)
                await toss.history(code, "1d")
                await toss.history(code, "1m", pages=5)
                await toss.snapshot(code)
                await toss.fundamentals_of_trading(code)
            await market_context_loop(store, once=True)
        else:
            await asyncio.gather(
                worker_heartbeat_loop(store),
                market_loop(),
                market_filings_loop(),
                market_context_loop(store),
                news_loop(provider),
                financial_reports_loop(authenticated),
                reviewed_eps_loop(provider),
                corporate_events_loop(authenticated),
                share_basis_review_loop(authenticated),
                detail_loop(),
                toss.run(),
                history_loop(store, provider),
                short_selling_loop(store, short_selling),
                xbrl_loop(authenticated),
                dated_accounts_loop(),
                krx_loop(KrxOpenApi(store, provider.client)),
            )
    finally:
        await provider.client.aclose()
        await toss.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(args.once))
