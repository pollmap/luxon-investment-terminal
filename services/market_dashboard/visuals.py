"""Source-backed market exploration. No generated chart points or memberships."""

import asyncio
import time
import re
from datetime import datetime

from .providers import PublicProvider, number, trace
from .store import now

_providers = {}
_locks = {}
_refresh_tasks = {}


def refresh_once(key, factory):
    if key not in _refresh_tasks:
        task = asyncio.create_task(factory())
        _refresh_tasks[key] = task
        task.add_done_callback(lambda _: _refresh_tasks.pop(key, None))


def provider(store):
    if id(store) not in _providers:
        _providers[id(store)] = PublicProvider(store)
    return _providers[id(store)]


def parse_chart(payload):
    values = payload.get("priceInfos", [])
    if isinstance(values, dict):
        values = [row for group in values.values() for row in group]
    points = {}
    for row in values:
        stamp = row.get("localDateTime") or row.get("localDate")
        value = number(row.get("currentPrice", row.get("closePrice")))
        if stamp and value is not None:
            points[stamp] = {"time": stamp, "value": value}
    return [points[key] for key in sorted(points)]


def chart_context(payload, period, expected_session=None):
    """Source date, not fetch time, defines the session. Never relabel stale prices."""
    result = dict(payload)
    points = result.get("points") or []
    stamp = str(points[-1].get("time", "")) if points else ""
    digits = re.sub(r"\D", "", stamp)
    session = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else None
    result.update(session_date=session, data_as_of=stamp or None, fetched_at=result.get("received_at"))
    if period == "day" and session:
        result["points"] = [p for p in points if re.sub(r"\D", "", str(p.get("time", "")))[:8] == digits[:8]]
    result["session_mismatch"] = bool(period == "day" and session and expected_session and session < expected_session[:10])
    if result["session_mismatch"]:
        result["status"] = "stale"
    return result


async def chart(store, code, period, kind="index", refresh=False):
    key = f"chart:{kind}:{code}:{period}"
    previous = store.get(key)
    ttl = 120 if period in ("day", "week") else 3600
    if previous and not refresh:
        if time.time() - datetime.fromisoformat(previous["received_at"]).timestamp() >= ttl:
            refresh_once((id(store), key), lambda: chart(store, code, period, kind, True))
            return chart_context({**previous, "status": "stale"}, period)
        return chart_context(previous, period)
    async with _locks.setdefault((id(store), key), asyncio.Lock()):
        previous = store.get(key)
        ttl = 120 if period in ("day", "week") else 3600
        if (
            previous
            and time.time() - datetime.fromisoformat(previous["received_at"]).timestamp() < ttl
        ):
            return chart_context(previous, period)
        url = f"https://api.stock.naver.com/chart/domestic/{kind}/{code}?periodType={period}"
        try:
            response, digest = await provider(store).fetch(url)
            payload = response.json()
            if payload.get("code") != code:
                raise ValueError("Chart identity mismatch")
            points = parse_chart(payload)
            if not points:
                raise ValueError("Empty source chart")
            baseline = (
                number(payload.get("lastClosePrice"))
                if period in ("day", "week")
                else points[0]["value"]
            )
            result = {
                "code": code,
                "period": period,
                "points": points,
                "baseline": baseline,
                "received_at": now(),
                "as_of": points[-1]["time"],
                "status": "ready",
                "source_trace": trace(
                    "naver_chart",
                    url,
                    digest,
                    points[-1]["time"],
                    "pt" if kind == "index" else "원",
                    "KRX · 원천 시계열",
                ),
                "return_pct": (points[-1]["value"] / baseline - 1) * 100 if baseline else None,
            }
            result = chart_context(result, period)
            store.put(key, result)
            return result
        except Exception:
            if previous:
                return chart_context({**previous, "status": "stale"}, period)
            return {"code": code, "period": period, "points": [], "status": "unavailable"}


async def themes(store, theme_id=None, refresh=False):
    key = "themes" if theme_id is None else f"theme:{theme_id}"
    previous = store.get(key)
    if previous and previous.get("complete") and not refresh:
        if time.time() - datetime.fromisoformat(previous["received_at"]).timestamp() >= 600:
            refresh_once((id(store), key), lambda: themes(store, theme_id, True))
            return {**previous, "status": "stale"}
        return previous
    async with _locks.setdefault((id(store), key), asyncio.Lock()):
        previous = store.get(key)
        if (
            previous
            and previous.get("complete")
            and time.time() - datetime.fromisoformat(previous["received_at"]).timestamp() < 600
        ):
            return previous
        url = "https://m.stock.naver.com/api/stocks/theme" + (
            f"/{theme_id}?page=1&pageSize=100" if theme_id else "?page=1&pageSize=100"
        )
        try:
            response, digest = await provider(store).fetch(url)
            payload = response.json()
            evidence = trace(
                "naver_theme",
                url,
                digest,
                now()[:10],
                "%",
                "제공처 테마 분류 · 공식 업종 분류와 다름",
            )
            if theme_id is None:
                groups = payload["groups"]
                for page in range(
                    2, min(20, (payload.get("totalCount", len(groups)) + 99) // 100) + 1
                ):
                    more, _ = await provider(store).fetch(url.replace("page=1&", f"page={page}&"))
                    groups.extend(more.json().get("groups", []))
                groups = list({g["no"]: g for g in groups}.values())
                result = {
                    "rows": [
                        {
                            "id": g["no"],
                            "name": g["name"],
                            "count": g["totalCount"],
                            "change": number(g["changeRate"]),
                        }
                        for g in groups
                    ],
                    "complete": len(groups) >= payload.get("totalCount", len(groups)),
                }
            else:
                stocks = payload.get("stocks", [])
                total = payload.get("totalCount", len(stocks))
                for page in range(2, min(10, (total + 99) // 100) + 1):
                    more, _ = await provider(store).fetch(url.replace("page=1&", f"page={page}&"))
                    stocks.extend(more.json().get("stocks", []))
                result = {
                    "codes": list(dict.fromkeys(s["itemCode"] for s in stocks)),
                    "description": payload.get("themeDescription"),
                    "name": payload.get("groupInfo", {}).get("name"),
                    "total": total,
                    "complete": len(stocks) >= total,
                }
            result.update(received_at=now(), source_trace=evidence, status="ready")
            store.put(key, result)
            return result
        except Exception:
            return {
                **(previous or {"rows": [], "codes": []}),
                "status": "stale" if previous else "unavailable",
            }
