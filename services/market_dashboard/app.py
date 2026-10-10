from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import httpx
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from .analysis import valuation_history, scenario, trailing_twelve_months
from .bars import aggregate_bars
from .valuation_series import valuation_series

from . import visuals
from .collection import SOURCES, collection_snapshot, worker_snapshot
from .exploration import compare_prices, screener_csv, sector_summary
from .metrics import METRICS
from .microstructure import public_depth
from .models import Query, apply_query
from .response_cache import InFlightResponses, RevisionReadBatcher, RevisionResponseCache
from .quote_hub import QuoteHub
from .quote_metrics import select_daily_measures
from . import request_timings as timings
from .trading import public_trading
from .trading_projection import (
    TradingProjectionUnavailable,
    dependencies as trading_dependencies,
    read_financing,
    read_flows,
    read_short_selling,
)
from .store import Store, now
from .toss_contract import INDICATORS, validate_ranking
from .toss_market import request_market, public_snapshot


@asynccontextmanager
async def lifespan(_app):
    # Build the read model before accepting the first user's request.
    await asyncio.to_thread(rows_with_details)
    hub = QuoteHub(rows_with_details, lambda: {code: public_depth(get_store().get("depth:"+code, {})) for code in get_store().active()})
    hub.publish(rows_with_details()[0])
    _app.state.quote_hub = hub
    hub.task = asyncio.create_task(hub.run())
    try:
        yield
    finally:
        await hub.close()


app = FastAPI(title="국내증시", version="2.0.0", lifespan=lifespan)
from .public_transport import PublicReadCors, public_socket_origin
app.add_middleware(PublicReadCors)
BASE = "/api/market/v1"
from .openalgo_bridge import router as internal_router, history_via_openalgo
app.include_router(internal_router)


@app.middleware('http')
async def public_validation_clock(request, call_next):
    started = time.perf_counter()
    path = request.url.path
    measured = request.method in ('GET', 'HEAD') and (path == BASE+'/search' or
        (re.fullmatch(BASE+r'/securities/[A-Z0-9]{6}', path) and request.query_params.get('view') == 'summary'))
    with timings.collect(measured) as recorded:
        response = await call_next(request)
    # Nginx and the edge preserve this clock. A second cache layer cannot
    # restart the one-second freshness budget after an origin cache hit.
    if response.status_code == 200 and (path == BASE+'/search' or
            (re.fullmatch(BASE+r'/securities/[A-Z0-9]{6}',path) and request.query_params.get('view')=='summary')):
        # Time to application response headers, excluding proxy and WAN transit.
        # This is not an end-to-end latency or a quote freshness timestamp.
        response.headers['Server-Timing'] = f'app;dur={(time.perf_counter()-started)*1000:.2f}'
        if recorded is not None:
            stages, counts = recorded.headers()
            if stages:
                response.headers['Server-Timing'] += ', ' + stages
            if counts:
                response.headers['X-Market-Work'] = counts
        current_ms = int(time.time()*1000)
        if 'X-Market-Validated-At' not in response.headers:
            response.headers['X-Market-Validated-At'] = str(current_ms)
        raw_validated = response.headers['X-Market-Validated-At']
        validated_ms = int(raw_validated) if re.fullmatch(r'[0-9]{1,16}', raw_validated) else 0
        if not 0 < validated_ms <= current_ms:
            # Do not repair an invalid/future clock by declaring this response new.
            # The edge's live no-store exception must also reject this response.
            response.headers['X-Accel-Expires'] = '0'
            response.headers['Cache-Control'] = 'private, no-store'
        else:
            # Nginx expires only when valid_sec < now (integer seconds). Using
            # the next second would allow almost TWO seconds of cache reuse.
            # This conservative floor never restarts the original clock; the
            # edge can use its remaining millisecond budget from validated_at.
            response.headers['X-Accel-Expires'] = '@' + str(validated_ms // 1000)
    return response


class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    growth: float = Field(gt=-100, le=100, allow_inf_nan=False)
    multiple: float = Field(gt=0, le=100, allow_inf_nan=False)
    years: int = Field(ge=1, le=5)


@app.post(BASE + "/analysis/scenario")
def calculate_scenario(inputs: ScenarioInput):
    return scenario(**inputs.model_dump())


@app.get(BASE + "/securities/{code}/analysis")
def security_analysis(code: str, metric: str = "EPS", basis: str = "CFS", multiple: float = 15, years: int = 10):
    valid_code(code)
    if metric not in ("EPS", "BPS") or basis not in ("CFS", "OFS") or years not in (3, 5, 10, 20):
        raise HTTPException(422, "Invalid analysis selection")
    store = get_store()
    if code not in store.listed_codes():
        raise HTTPException(404, "Unknown security")
    store.request(code)
    details = store.get("detail:" + code, {})
    prices = store.get("prices:" + code, {})
    try:
        history = valuation_history(details.get("financials", []), prices.get("rows", []), metric=metric, basis=basis, multiple=multiple, years=years)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    official = store.get("official:" + code, {})
    return {"code": code, "valuation": history, "ttm": trailing_twelve_months([f for f in official.get("period_facts", []) if f.get("basis") == basis])}

_rows_cache = {}
_rows_lock = threading.RLock()
_security_rows_lock = threading.Lock()
_universe_build_lock = threading.Lock()
_security_universe_cache = None
_security_rows = OrderedDict()
_body_cache = {}
_detail_responses = RevisionResponseCache()
_search_responses = RevisionResponseCache(max_bytes=4 * 1024**2, max_entries=256)
_search_flights = InFlightResponses()
_detail_flights = InFlightResponses()
_revision_reads = RevisionReadBatcher()


def encoded_response(content, request, validated_at=None):
    accepts = any(
        part.strip().split(";")[0].lower() == "gzip"
        and not re.search(r"q=0(?:\.0*)?(?:;|$)", part.replace(" ", ""))
        for part in request.headers.get("accept-encoding", "").split(",")
    )
    headers = {"Cache-Control": "no-store", "Vary": "Accept-Encoding"}
    if validated_at is not None:
        headers['X-Market-Validated-At'] = str(validated_at)
    if accepts:
        headers["Content-Encoding"] = "gzip"
    return Response(
        content if accepts else gzip.decompress(content),
        media_type="application/json",
        headers=headers,
    )


def cached_public(key, request):
    with _rows_lock:
        cached = _body_cache.get((id(get_store()), key))
        if cached and time.monotonic() - cached[0] < 1:
            return encoded_response(cached[1], request, cached[2])
    return None


def public_response(payload, key, request):
    """Cache serialized public snapshots for one second; never used for private state."""
    key = (id(get_store()), key)
    with _rows_lock:
        cached = _body_cache.get(key)
        if not cached or time.monotonic() - cached[0] > 1:
            if len(_body_cache) > 256:
                _body_cache.clear()
            cached = (
                time.monotonic(),
                gzip.compress(
                    json.dumps(
                        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                    ).encode("utf-8"),
                    compresslevel=1,
                ),
                int(time.time()*1000),
            )
            _body_cache[key] = cached
    return encoded_response(cached[1], request, cached[2])


@lru_cache
def get_store():
    return Store()


from .toss_sectors import attach_routes as attach_sector_routes
attach_sector_routes(app, BASE, lambda: get_store())
from .market_context import attach_routes as attach_context_routes
attach_context_routes(app, BASE, lambda: get_store())


def valid_code(code):
    if not re.fullmatch(r"[A-Z0-9]{6}", code):
        raise HTTPException(422, "Invalid security code")
    return code


def analysis_response(code, request, selection, build, dependencies=None, history_kind=None):
    valid_code(code)
    store = get_store()
    if code not in store.listed_codes():
        raise HTTPException(404, "Unknown security")
    store.request(code)
    dependencies = dependencies or ["prices:" + code, "detail:" + code, "official:" + code, "events:" + code]
    try:
        content = _detail_responses.response(store, (code, *selection), dependencies, lambda: build(store))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "Invalid analysis selection") from exc
    if history_kind:
        from .toss_backfill import request_history
        request_history(store, code, history_kind)
    etag = '"' + hashlib.sha256(content).hexdigest()[:24] + '"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=0, must-revalidate", "Vary": "Accept-Encoding"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response = encoded_response(content, request)
    response.headers.update(headers)
    return response


@app.get(BASE + '/analysis/metrics')
def analysis_metric_definitions():
    from .analysis_metrics import definitions
    return definitions()


@app.get(BASE + '/securities/{code}/analysis/metrics')
def security_analysis_metrics(code: str, request: Request):
    def build(store):
        from .analysis_inputs import security_facts, metric_availability
        from .share_basis import valid_evidence, aligned_prices
        facts, official = security_facts(store, code)
        price = store.metadata(f'bars:{code}:통합:day', {}) or store.metadata('prices:'+code, {})
        evidence = store.get('share-basis:'+code,{})
        if valid_evidence(evidence):
            price = aligned_prices(store.metadata(f'bars:{code}:통합:raw',{}), evidence, code)
        return {'code':code, **metric_availability(facts, price, code,
            input_readiness=official.get('metric_input_readiness', []))}
    return analysis_response(code, request, ('metric-availability-v4',), build,
                             ['official:'+code,'detail:'+code,'prices:'+code,f'bars:{code}:통합:day',f'bars:{code}:통합:raw','share-basis:'+code,'dart-corporations'])


@app.get(BASE + "/securities/{code}/analysis/valuation")
def security_valuation(code: str, request: Request, metric: str = "EPS", basis: str = "auto", years: int = 5,
                       multiple: float | None = None, start: str | None = None, end: str | None = None,
                       mode: str = "as_known", frequency: str = "auto", interval: str = "week", venue: str = "auto"):
    if venue not in ("auto", "KRX", "NXT", "통합"):
        raise HTTPException(422, "Invalid venue")
    def build(store):
        from .analysis_inputs import security_facts, analysis_prices
        facts, official = security_facts(store, code)
        price, selected_venue = analysis_prices(store, code, venue, years, start, end)
        sessions = {}
        for session in store.list_prefix('market-session:').values():
            day = session.get('session',{})
            periods = day.get('integrated') or {}
            endings = [v.get('endTime') for k,v in periods.items() if v and v.get('endTime') and (selected_venue=='통합' or k=='regularMarket')]
            if endings and selected_venue in ('KRX','통합'):
                sessions[day['date']] = max(endings)
        result = valuation_series(facts, price, code=code, metric=metric, basis=basis, years=years, multiple=multiple,
                                  start=start, end=end, mode=mode, frequency=frequency, interval=interval, venue=selected_venue,session_closes=sessions)
        result["ttm"] = official.get('ttm',{}).get(result['basis'], {'rows':[],'status':'period_evidence_required'})
        return result
    return analysis_response(code, request, ("valuation-v3", metric, basis, years, multiple, start, end, mode, frequency, interval, venue), build,
                             ["prices:"+code, f"bars:{code}:{venue}:day", f"bars:{code}:KRX:day", f"bars:{code}:통합:day", f"bars:{code}:통합:raw", "detail:"+code, "official:"+code, 'share-basis:'+code, 'dart-corporations'])


@app.get(BASE + '/securities/{code}/financials/per-share')
def security_reported_eps(code: str, request: Request, basis: str = 'CFS', frequency: str = 'all'):
    if basis not in ('CFS','OFS') or frequency not in ('all','annual','quarterly','cumulative','ttm'):
        raise HTTPException(422, 'Invalid accounting selection')
    def build(store):
        from .official_views import read_view
        from .per_share import security_eps, latest_observations
        analysis = read_view(store,code,'analysis') or {}
        model = analysis.get('eps',{})
        calculated = analysis.get('ttm_eps',{})
        corporation = store.get('dart-corporations',{}).get('codes',{}).get(code)
        rows = latest_observations(security_eps(model,code,corporation,basis=basis,frequency=frequency))
        inputs = [r for r in calculated.get('inputs',[]) if r.get('basis')==basis and r.get('company_id')==corporation]
        ttm_rows = [r for r in calculated.get('rows',[]) if r.get('basis')==basis and r.get('company_id')==corporation]
        from .eps_calculation import ttm_readiness
        readiness=ttm_readiness(inputs,ttm_rows,security_eps(model,code,corporation,basis=basis))
        return {'code':code,'basis':basis,'rows':rows,'unit':'원/주','status':'ready' if rows else 'evidence_required',
                # This endpoint exposes filing observations, without a selected
                # price series. Eligibility belongs to analysis/valuation.
                'comparison_status':'reported_only',
                'ttm_status':readiness['status'],'ttm_readiness':readiness,
                'reconciled_inputs':[{k:r.get(k) for k in ('period_start','period_end','reported_eps','weighted_shares','profit','published_at','reconciliation_residual','reconciliation_tolerance')} for r in inputs],
                'rejected':{**model.get('rejected',{}),**calculated.get('rejected',{})}}
    return analysis_response(code,request,('reported-eps-v3',basis,frequency),build,
                             ['official:'+code,'share-basis:'+code,'dart-corporations'])


@app.get(BASE + "/securities/{code}/bars")
def security_bars(code: str, request: Request, interval: str = "day", start: str | None = None,
                  end: str | None = None, venue: str = "KRX", adjustment: str = "source"):
    if venue not in ("KRX", "NXT", "통합") or adjustment not in ("source", "raw", "split") or interval not in ("day", "week", "month", "5min"):
        raise HTTPException(422, "Invalid chart selection")
    def build(store):
        from datetime import date
        for bound in (start,end):
            if bound:
                date.fromisoformat(bound)
        if start and end and start > end:
            raise ValueError("Invalid date range")
        period = '5min' if interval == '5min' else 'raw' if adjustment == 'raw' else 'day'
        key = f"bars:{code}:{venue}:{period}"
        use_openalgo = venue == "통합" and (
            adjustment == "source" or (interval == "5min" and adjustment == "raw")
        )
        # The sidecar reads these same stored bars. Materialize them here only
        # when its response is unavailable, keeping the original fallback path.
        data = store.metadata(key, {}) if use_openalgo else store.get(key, {})
        if not data.get("rows") and interval != "5min" and venue == "KRX" and adjustment != "split":
            data = store.get(f"krx-bars:{code}:KRX:raw", {}) or data
        if not data and interval != "5min" and venue == "KRX" and adjustment == "source":
            data = store.get("prices:" + code, {})
        if adjustment != "source" and data.get("adjustment") != adjustment:
            data = {}
        transport = "stored"
        if use_openalgo:
            try:
                remote = history_via_openalgo(code, interval, None, None)
                if remote is not None:
                    if not remote:
                        if data.get("_series_ref"):
                            count = data.get("row_count")
                            stored_rows = count > 0 if type(count) is int and count >= 0 else bool(
                                store.time_series.rows(data["_series_ref"], limit=1)
                            )
                        else:
                            stored_rows = bool(data.get("rows"))
                        if stored_rows:
                            raise ValueError("OpenAlgo returned empty for stored history")
                    data = {**data, "rows":remote}
                    transport = "openalgo"
            except (httpx.HTTPError, ValueError, KeyError):
                transport = "stored_fallback"
            if transport != "openalgo":
                data = store.get(key, {})
                if adjustment != "source" and data.get("adjustment") != adjustment:
                    data = {}
        if interval == "5min":
            rows = [r for r in data.get("rows", []) if (not start or r["date"] >= start) and (not end or r["date"] <= end)]
            return {**data, "rows": rows, "interval": interval, "venue": venue, "transport":transport, "status": data.get("status", "not_collected")}
        return {**aggregate_bars(data.get("rows", []), interval=interval, start=start, end=end), "code": code,
                "venue": data.get("venue"), "adjustment": data.get("adjustment"), "source_trace": data.get("source_trace"), "transport":transport}
    return analysis_response(code, request, ("bars-v1", interval, start, end, venue, adjustment), build,
                             ["prices:"+code, f"bars:{code}:{venue}:day", f"bars:{code}:{venue}:raw", f"bars:{code}:{venue}:5min", f"krx-bars:{code}:KRX:raw"],
                             history_kind=("raw-history" if adjustment == "raw" else "history") if venue == "통합" and interval != "5min" else None)


@app.get(BASE + "/securities/{code}/events")
def security_events(code: str, request: Request):
    return analysis_response(code, request, ("events-v1",), lambda store: store.get("events:"+code,
        {"code": code, "rows": [], "status": "not_collected", "coverage": {"verified": False}}), ["events:"+code])


@app.get(BASE + "/securities/{code}/analysis/price-context")
def security_price_context(code: str, request: Request, years: int = 1,
                           start: str | None = None, end: str | None = None):
    from .price_context import price_context

    return analysis_response(code, request, ("price-context-v1", years, start, end),
        lambda store: price_context(store.get(f"bars:{code}:통합:day", {}) or store.get("prices:" + code, {}), code=code,
                                    years=years, start=start, end=end), ["prices:" + code,f"bars:{code}:통합:day"])


def rows_with_details(*, check_revision=False, known_revisions=None):
    from .enriched_view import read as read_enriched
    store = get_store()
    with timings.timed_lock(_rows_lock, 'rows_lock'):
        cached = _rows_cache.get(id(store))
        # Search already observed these heads for its response-cache check. Reuse
        # only the exact cached generation; a missing/different generation still
        # rechecks the DB. Do not extend the timed cache from an older observation.
        if cached and known_revisions is not None and cached[2] == known_revisions:
            return cached[1]
        if cached and not check_revision and time.monotonic() - cached[0] < 1:
            return cached[1]
        keys = ("universe", "enriched", "quotes")
        revisions = timings.read_revisions(store, keys)
        if cached and cached[2] == revisions:
            _rows_cache[id(store)] = (time.monotonic(), *cached[1:])
            return cached[1]
        defaults = (
            {"rows": [], "coverage": [], "unavailable_markets": ["KOSPI", "KOSDAQ", "KONEX"]},
            {},
            {},
        )
        previous_projection = getattr(store, '_enriched_view', None)
        projected_before = bool(
            cached and previous_projection and previous_projection[1] is cached[3][1]
            and previous_projection[0].get('enriched') == cached[2].get('enriched')
        )
        # Only these three shared inputs are retained, never one copy per user.
        with timings.stage('rows_inputs'):
            inputs = tuple(
                cached[3][index]
                if cached and cached[2].get(key) == revisions.get(key)
                else read_enriched(store, defaults[index]) if key == 'enriched'
                else universe_inputs(store, revisions.get('universe'))[0] if key == 'universe'
                else store.get(key, defaults[index], use_redis=False)
                for index, key in enumerate(keys)
            )
        projection = getattr(store, '_enriched_view', None)
        projected_now = bool(
            projection and projection[1] is inputs[1]
            and projection[0].get('enriched') == revisions.get('enriched')
        )
        # A validated projection owns decoded item objects: it replaces changed
        # items rather than mutating Store.get's dictionaries. Legacy snapshots
        # may be edited in place before put(), so never compare their old aliases.
        reusable = (projected_before and projected_now and all(
            cached[2].get(key) == revisions.get(key) for key in ('universe', 'quotes')
        ))
        result = assemble_rows(store, inputs, previous=(cached[3], cached[1][0]) if reusable else None)
        _rows_cache.clear()
        _rows_cache[id(store)] = (time.monotonic(), result, revisions, inputs)
        return result


@timings.timed('rows_build')
def assemble_rows(store, inputs=None, *, previous=None):
    universe = (
        inputs[0]
        if inputs
        else store.get(
            "universe",
            {"rows": [], "coverage": [], "unavailable_markets": ["KOSPI", "KOSDAQ", "KONEX"]},
        )
    )
    extras = inputs[1] if inputs else store.get("enriched", {})
    from .service_scope import universe_view
    universe=universe_view(universe)
    quotes = inputs[2] if inputs else store.get("quotes", {})
    old_inputs, old_rows = previous if previous is not None else (None, ())
    old_bases = {row['code']: row for row in old_inputs[0].get('rows', [])} if old_inputs else {}
    old_selected = {row['code']: row for row in old_rows}
    rows = []
    reused = 0
    for row in universe["rows"]:
        detail = extras.get(row["code"], {})
        quote = quotes.get(row["code"], {})
        code = row['code']
        # Enrichment changes one company at a time. Its shared revision must
        # not recreate thousands of unchanged fact dictionaries under the list
        # lock. Compare all three real inputs, including dates/source metadata;
        # query filtering and ranking still use the complete current row set.
        if old_inputs and code in old_selected and (
            row == old_bases.get(code)
            and detail == old_inputs[1].get(code, {})
            and quote == old_inputs[2].get(code, {})
        ):
            rows.append(old_selected[code])
            reused += 1
            continue
        # Public list as_of can be its collection clock. It must not supersede
        # the broker's actual last-trade timestamp merely because it was fetched later.
        broker_quote = quote.get("quote_trace", {}).get("source") in ("toss", "toss_websocket")
        if not broker_quote and quote.get("as_of") and row.get("as_of") and quote["as_of"] < row["as_of"]:
            quote = {}
        base_facts = {
            k: {
                "value": row.get(k),
                "period": row.get("as_of"),
                "unit": METRICS[k]["unit"],
                "source_trace": {**row["source_trace"], "unit": METRICS[k]["unit"]},
            }
            for k in ("price", "change", "volume", "value", "cap")
        }
        combined = {
            **row,
            **detail,
            **quote,
            "facts": {**base_facts, **row.get("facts", {}), **detail.get("facts", {})},
        }
        if quote:
            combined["facts"] = {
                **combined.get("facts", {}),
                **{
                    k: {
                        "value": quote[k],
                        "period": quote.get("as_of"),
                        "unit": METRICS[k]["unit"],
                        "source_trace": {
                            **quote["quote_trace"],
                            "unit": METRICS[k]["unit"],
                        },
                    }
                    for k in ("price", "change")
                    if k in quote
                },
            }
        select_daily_measures(combined, [quote.get('daily_facts', {}), detail.get('facts', {}), row.get('facts', {}), base_facts])
        rows.append(combined)
    timings.count('rows_built', len(rows) - reused)
    timings.count('rows_reused', reused)
    return rows, universe


def universe_inputs(store, revision):
    """Share one identity snapshot per revision, even above the decode LRU limit.

    Search and distinct-symbol cold reads reuse the same rows; the identity map
    references them rather than copying a second full universe. Only one store
    and its latest observed universe are retained, not a revision history.
    """
    from .service_scope import included
    global _security_universe_cache
    with _universe_build_lock:
        cached = _security_universe_cache
        if cached is not None and cached[0] is store and cached[1] == revision:
            return cached[2], cached[3]
        snapshot = store.get('universe', {
            'rows': [], 'coverage': [], 'unavailable_markets': ['KOSPI', 'KOSDAQ'],
        }, use_redis=False)
        identities = {row['code']: row for row in snapshot.get('rows', []) if included(row)}
        _security_universe_cache = (store, revision, snapshot, identities)
        return snapshot, identities


@timings.timed('selected_read')
def security_row(store, code, *, include_quote=True, revisions=None):
    """Reuse the identity index and assemble only the requested security."""
    from .enriched_view import read_item
    source_keys = ('universe', 'enriched') + (('quotes',) if include_quote else ())
    heads = revisions if revisions is not None else timings.read_revisions(store, source_keys)
    source_revision = tuple((key, heads.get(key)) for key in source_keys)
    identity = (store, code, include_quote)
    with _security_rows_lock:
        cached_row = _security_rows.get(identity)
        if cached_row and cached_row[0] == source_revision:
            _security_rows.move_to_end(identity)
            return cached_row[1]
    _, identities = universe_inputs(store, heads.get('universe'))
    row = identities.get(code)
    if row is None:
        raise HTTPException(404, '수집된 종목 목록에서 찾을 수 없습니다.')
    quote = store.get('quotes', {}, use_redis=False).get(code, {}) if include_quote else {}
    selected, _ = assemble_rows(store, ({'rows': [row]}, {code: read_item(store, code)}, {code: quote}))
    with _security_rows_lock:
        _security_rows[identity] = (source_revision, selected[0])
        _security_rows.move_to_end(identity)
        while len(_security_rows) > 256:
            _security_rows.popitem(last=False)
    return selected[0]


@app.get(BASE + "/health")
async def health():
    store = get_store()
    worker = store.get("worker", {"status": "not_started"})
    worker = {**worker_snapshot(worker), "listed_securities": len(store.listed_codes())}
    return {
        "status": "ok",
        "worker": worker,
        "database": store.engine.dialect.name,
        "mode": "source_observed",
        "release": os.getenv("MARKET_RELEASE", "local"),
    }


@app.get(BASE + "/metrics")
def metrics():
    return METRICS


@app.get(BASE + "/coverage")
def coverage():
    rows, universe = rows_with_details()
    result = collection_snapshot(get_store(), rows, get_store().get("worker", {}))
    result["market_lists"] = universe.get("coverage", [])
    result["unavailable_markets"] = universe.get("unavailable_markets", [])
    result["metric_coverage"] = {
        key: sum(row.get(key) is not None for row in rows) for key in METRICS
    }
    result["source_labels"] = SOURCES
    return result


@app.get(BASE + "/indices/{code}/chart")
async def index_chart(code: str, period: str = "day"):
    if code not in (*INDICATORS, "KPI200") or period not in (
        "day",
        "week",
        "month",
        "year",
    ):
        raise HTTPException(422, "Unsupported chart")
    store = get_store()
    interval = '1m' if period == 'day' and code in ('KOSPI','KOSDAQ') else '1d'
    snapshot = store.get(f'toss-index:{code}:{interval}', {})
    if code in INDICATORS:
        request_market(store,f'toss-index:{code}:{interval}','index_candles',code=code,interval=interval)
    rows = snapshot.get('rows',[])
    if rows:
        from datetime import datetime, timedelta
        last = rows[-1]['date']
        cutoff = last if period=='day' else (datetime.fromisoformat(last)-timedelta(days={'week':7,'month':31,'year':366}[period])).date().isoformat()
        chosen = [r for r in rows if r['date'] >= cutoff]
        points = [{'time':datetime.fromisoformat(r['time']).strftime('%Y%m%d%H%M%S' if interval=='1m' else '%Y%m%d'),'value':r['close']} for r in chosen]
        return {'points':points,'status':snapshot.get('status','ready'),'session_date':last,
            'unit':snapshot.get('unit'),'venue':'KRX','data_as_of':rows[-1]['time'],
            'baseline':chosen[0]['open'] if chosen else None}
    if code.startswith('KR_BOND_'):
        return {'points':[],'status':'pending','unit':'%'}
    return await visuals.chart(store, code, period)


@app.get(BASE + '/rankings')
def market_rankings(type: str = 'MARKET_TRADING_AMOUNT', duration: str = '1d', exclude_caution: bool = False):
    try:
        validate_ranking(type, duration)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    store = get_store()
    key = f'toss-ranking:{type}:{duration}:{int(exclude_caution)}'
    request_market(store,key,'ranking',kind=type,duration=duration,exclude=exclude_caution)
    result = {'rows':[], 'status':'pending', **public_snapshot(store,key)}
    # Historical cached rankings must respect the current product scope too.
    # Keep provider ranks rather than inventing consecutive replacement ranks.
    known = set(store.listed_codes())
    result['rows'] = [row for row in result['rows'] if row.get('code') in known]
    return result


@app.get(BASE + '/market-context')
def market_context(date: str | None = None, date_time: str | None = None, base: str = 'USD', quote: str = 'KRW'):
    from .market_selection import calendar_selection,exchange_selection
    try:
        calendars={c:calendar_selection(c,date) for c in ('KR','US')}
        exchange_key,at=exchange_selection(base,quote,date_time)
    except ValueError as exc:
        raise HTTPException(422,'Invalid market date or currency') from exc
    store = get_store()
    for country,key in calendars.items():
        request_market(store,key,'calendar',country=country,day=date)
    request_market(store,exchange_key,'exchange_rate',base=base,quote=quote,at=at)
    return {'indicators':public_snapshot(store,'toss-market-indicators'),
        'exchange_rate':{'status':'pending',**public_snapshot(store,exchange_key)},
        'calendars':{c:{'status':'pending',**public_snapshot(store,key)} for c,key in calendars.items()}}


@app.get(BASE + '/listings')
def market_listings(market: str='KOSPI', status: str='ACTIVE', security_type: str='STOCK', common_share: bool | None=None):
    from .market_selection import listing_selection
    try:
        key=listing_selection(market,status,security_type,common_share)
    except ValueError as exc:
        raise HTTPException(422,'Invalid listing selection') from exc
    store=get_store()
    request_market(store,key,'listings',market=market,status=status,security_type=security_type,common_share=common_share)
    result={'status':'pending','rows':[],**public_snapshot(store,key)}
    known=set(store.listed_codes())
    # Provider listings supplement the universe; they never remove stored issuers.
    result['rows']=[{**row,'openable':row['symbol'] in known} for row in result['rows']
        if row.get('securityType')==security_type and (common_share is None or row.get('isCommonShare') is common_share)]
    return result


@app.get(BASE + '/indices/{code}/investors')
def market_investors(code: str, interval: str = '1d', limit: int | None = None):
    if code not in ('KOSPI','KOSDAQ') or interval not in ('1d','1w','1mo','1y') or (limit is not None and not 1<=limit<=10000):
        raise HTTPException(422,'Unsupported market investor selection')
    store = get_store()
    key = f'toss-index-investors:{code}:{interval}'
    request_market(store,key,'index_investors',code=code,interval=interval)
    from .toss_market import institution_amounts
    result={'rows':[], 'status':'pending', 'unit':'원','venue':'KRX', **public_snapshot(store,key)}
    result['total_rows']=len(result['rows'])
    if limit is not None:
        result['rows']=result['rows'][-limit:]
    result['rows']=[{**row,'institutions':institution_amounts(row.get('institution_breakdown'))} for row in result['rows']]
    return result


@app.get(BASE + '/securities/{code}/warnings')
def security_warnings(code: str):
    valid_code(code)
    store = get_store()
    if code not in store.listed_codes():
        raise HTTPException(404,'Unknown security')
    store.request(code)
    request_market(store,'toss-warnings:'+code,'warnings',code=code)
    return {'rows':[], 'status':'pending', **public_snapshot(store,'toss-warnings:'+code),
        'limits':public_snapshot(store,'toss-limits:'+code),
        'stock':public_snapshot(store,'toss-stock:'+code).get('stock')}


@app.get(BASE + "/securities/{code}/chart")
async def security_chart(code: str, period: str = "day"):
    if not re.fullmatch(r"[0-9A-Z]{6}", code) or period not in ("day", "week", "month", "year"):
        raise HTTPException(422, "Unsupported chart")
    store = get_store()
    toss = store.get('toss-chart:'+code, {}) if period == 'day' else {}
    data = toss if toss.get('points') else await visuals.chart(store, code, period, "item")
    quote = store.get('quotes', {}).get(code, {})
    return visuals.chart_context(data, period, quote.get('as_of'))


@app.get(BASE + "/themes")
async def theme_list():
    return await visuals.themes(get_store())


@app.get(BASE + "/themes/{theme_id}")
async def theme_members(theme_id: int):
    groups = await visuals.themes(get_store())
    if not any(g["id"] == theme_id for g in groups.get("rows", [])):
        raise HTTPException(404, "Unknown theme")
    result = await visuals.themes(get_store(), theme_id)
    codes = set(result.get("codes", []))
    rows, _ = rows_with_details()
    return {
        **result,
        "rows": sorted(
            [r for r in rows if r["code"] in codes], key=lambda r: r.get("value") or 0, reverse=True
        ),
    }


@app.get(BASE + "/overview")
def overview():
    from .filing_scope import public_filings
    rows, universe = rows_with_details()
    known = [r for r in rows if r.get("change") is not None]
    sectors = {}
    for row in known:
        if not row.get("sector"):
            continue
        entry = sectors.setdefault(
            row["sector"], {"name": row["sector"], "count": 0, "sum": 0, "value": 0}
        )
        entry["count"] += 1
        entry["sum"] += row["change"]
        entry["value"] += row.get("value") or 0
    sector_rows = [{**s, "change": s["sum"] / s["count"]} for s in sectors.values()]
    return {
        "indices": get_store().get("indices", {}).get("rows", []),
        "sectors": sorted(sector_rows, key=lambda s: s["value"], reverse=True)[:8],
        "breadth": {
            "up": sum(r["change"] > 0 for r in known),
            "down": sum(r["change"] < 0 for r in known),
            "flat": sum(r["change"] == 0 for r in known),
            "covered": len(known),
        },
        "value": sum(r.get("value") or 0 for r in rows),
        "total": len(rows),
        "coverage": universe.get("coverage", []),
        "unavailable_markets": universe.get("unavailable_markets", []),
        "received_at": universe.get("received_at"),
        "mode": "source_observed",
        "release": os.getenv("MARKET_RELEASE", "local"),
        "scope": "수집된 주식 종목 합계 · ETF/ETN 제외",
        "worker": get_store().get("worker", {}),
        "filings": public_filings(get_store(), get_store().get(
            "market-filings",
            {
                "rows": [],
                "status": "unavailable",
                "scope": "OpenDART 최근 공시를 불러오는 중입니다.",
            },
        )),
    }


@app.get(BASE + "/sectors")
def sectors(market: str = "ALL"):
    if market not in ("ALL", "KOSPI", "KOSDAQ"):
        raise HTTPException(422, "Unsupported market")
    rows, _ = rows_with_details()
    selected = [r for r in rows if market == "ALL" or r["market"] == market]
    groups = sector_summary(selected)
    return {"rows": groups, "total": len(groups)}


@app.post(BASE + "/screener/export")
def export_screener(query: Query):
    rows, _ = rows_with_details()
    query = query.model_copy(update={"offset": 0, "limit": max(1, len(rows))})
    selected = apply_query(rows, query)["rows"]
    return Response(
        screener_csv(selected),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="stocks.csv"',
            "Cache-Control": "no-store",
        },
    )


@app.get(BASE + "/compare")
def compare(codes: str, period: str = "year"):
    periods = {"month": 1, "quarter": 3, "year": 12, "five_years": 60}
    selected = list(dict.fromkeys(codes.split(",")))
    if not 2 <= len(selected) <= 4 or period not in periods:
        raise HTTPException(422, "Choose 2–4 securities and a supported period")
    for code in selected:
        valid_code(code)
    rows, _ = rows_with_details()
    by_code = {r["code"]: r for r in rows}
    if any(code not in by_code for code in selected):
        raise HTTPException(404, "Unknown security")
    store = get_store()
    for code in selected:
        store.request(code)
    return compare_prices(
        [by_code[c] for c in selected],
        {c: store.get("prices:" + c) for c in selected},
        periods[period],
    )


@app.get(BASE + "/securities/{code}/quote")
def security_quote(code: str, background_tasks: BackgroundTasks):
    valid_code(code)
    store = get_store()
    row = security_row(store, code)
    background_tasks.add_task(store.request, code)
    return row


@app.post(BASE + "/screener")
def screener(query: Query):
    rows, universe = rows_with_details()
    result = apply_query(rows, query)
    return {
        **result,
        "received_at": universe.get("received_at"),
        "coverage": universe.get("coverage", []),
        "metric_coverage": {key: sum(r.get(key) is not None for r in rows) for key in METRICS},
    }


@app.get(BASE + '/search-index')
async def search_index(request: Request):
    from .models import _INITIALS
    store = get_store()
    def build():
        from .service_scope import universe_view
        rows = universe_view(store.get('universe', {}))['rows']
        result = []
        for row in sorted(rows, key=lambda r: (-(r.get('cap') or 0), r['code'])):
            aliases = row.get('aliases') or []
            if isinstance(aliases, str):
                aliases = [aliases]
            tokens = []
            for name in [row['code'], row['name'], *aliases]:
                compact = ''.join(str(name).casefold().split())
                tokens.extend((compact, ''.join(_INITIALS[(ord(c)-0xAC00)//588] if 0xAC00<=ord(c)<=0xD7A3 else c for c in compact)))
            result.append({**{k: row.get(k) for k in ('code','name','market','logo_url')},
                           'tokens': list(dict.fromkeys(tokens))})
        return {'rows': result}
    content = await _search_flights.run((store, 'search-index'),
        lambda: _search_responses.response(store, 'search-index', ('universe',), build))
    response = encoded_response(content, request)
    response.headers['Cache-Control'] = 'public, max-age=300'
    return response


@app.get(BASE + "/search")
async def search(request: Request, q: str = "", limit: int = 12):
    if len(q) > 100:
        raise HTTPException(422, "Query too long")
    normalized_q = q.strip()
    bounded_limit = max(1, min(30, limit))
    cache_key = ("search", normalized_q.casefold(), bounded_limit)
    # Merge concurrent cache misses and invalidate by data revision. A time-only
    # cache used to repeat the same full-universe search when its one second
    # deadline expired during a burst of mobile search requests.
    store = get_store()
    # One revision read per overlapping query group, not one serialized DB read
    # per client. Once the read ends, the next group rechecks actual DB revisions.
    source_keys = ("universe", "enriched", "quotes")
    heads = await _revision_reads.read(store, source_keys)
    content = await _search_flights.run(
        (store, cache_key),
        lambda: _search_responses.response(
            store, cache_key, source_keys,
            lambda: search_payload(normalized_q, bounded_limit, known_revisions=heads),
            initial_revision=heads,
        ),
    )
    return encoded_response(content, request)


def search_payload(query, limit, *, known_revisions=None):
    rows, _ = rows_with_details(check_revision=True, known_revisions=known_revisions)
    result = apply_query(rows, Query(q=query, limit=limit))
    compact_rows = []
    for row in result["rows"]:
        compact = {
            key: row[key]
            for key in (
                "logo_url",
                "code",
                "name",
                "market",
                "sector",
                "price",
                "change",
                "as_of",
                "received_at",
                "market_status",
                "source_trace",
            )
            if key in row
        }
        facts = row.get("facts", {})
        compact["facts"] = {key: facts[key] for key in ("price", "change") if key in facts}
        compact_rows.append(compact)
    return {**result, "rows": compact_rows}


@app.get(BASE + "/securities/{code}")
async def security(code: str, request: Request, background_tasks: BackgroundTasks, view: str = "full"):
    valid_code(code)
    if view not in ("full", "summary", "activity", "fundamentals", "company", "filings"):
        raise HTTPException(422, "Invalid detail view")
    store = get_store()
    kinds = {"full": ("detail", "prices", "collection", "filings", "official"),
             "summary": (), "activity": ("detail", "collection"),
             "fundamentals": ("detail", "official", "collection"),
             "company": ("detail", "official", "collection"), "filings": ("detail", "filings", "collection")}[view]
    dependencies = ((*trading_dependencies(code, 'flows'), f'naver:investor-trading:{code}') if view in ('full','activity') else ()) + (("detail-summary:"+code,) if view=='summary' else ()) + tuple(
        f"{kind}:{code}" for kind in kinds
    )
    dependencies = tuple(dict.fromkeys(dependencies))
    if view=='company':
        from .issuer_documents import report_dependencies
        dependencies=tuple(dict.fromkeys((*dependencies,*report_dependencies(store,code))))
    # Different symbols share the same source heads; collect only requests
    # already queued in this loop turn. No TTL or retained validation clock.
    initial_heads = (await _revision_reads.read(store, (*dependencies, 'universe', 'enriched', 'quotes'))
                     if view == 'summary' else None)
    def read():
        observed = {}
        first_heads = initial_heads
        def selected_revisions():
            nonlocal first_heads
            include_quote = view in ('full', 'summary')
            source_keys = ('universe', 'enriched') + (('quotes',) if include_quote else ())
            heads = first_heads if first_heads is not None else timings.read_revisions(store, (*dependencies, *source_keys))
            first_heads = None
            row = security_row(store, code, include_quote=include_quote, revisions=heads)
            observed['row'] = row
            selected = row if view == 'full' else {
                'security': summary_security(row) if view == 'summary' else {k: row.get(k) for k in ('code', 'name', 'market')},
                'company': row.get('company', {}),
            }
            signature = hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
            return {**{key: heads[key] for key in dependencies if key in heads}, 'selected-security': signature}
        return _detail_responses.response(store, (code, view), selected_revisions,
            lambda: detail_snapshot(store, code, view, row=observed['row']))
    body = await _detail_flights.run((store, code, view), read)
    # ASGI sends the body before persisting the best-effort refresh intent.
    # Store.request coalesces repeated symbols and preserves SQLite protection.
    background_tasks.add_task(store.request, code)
    # A weak validator identifies the same JSON across gzip/identity encodings.
    etag = 'W/"' + hashlib.sha256(body).hexdigest() + '"'
    validators = request.headers.get("if-none-match", "").split(",")
    if any(tag.strip().removeprefix("W/") in ("*", etag[2:]) for tag in validators):
        return Response(status_code=304, headers={
            "ETag": etag, "Cache-Control": "no-store", "Vary": "Accept-Encoding",
        })
    response = encoded_response(body, request)
    response.headers["ETag"] = etag
    return response


def section_official(official, view):
    from .official_views import project
    return project(official, view)


@app.get(BASE + '/securities/{code}/peers')
def security_peers(code: str):
    valid_code(code)
    rows, _ = rows_with_details(check_revision=True)
    selected = next((r for r in rows if r['code'] == code), None)
    if selected is None:
        raise HTTPException(404, 'Unknown security')
    sector = selected.get('sector')
    peers = sorted((r for r in rows if sector and r.get('sector') == sector and r['code'] != code),
                   key=lambda r: (-(r.get('cap') or 0), r['code']))[:8]
    fields = ('code', 'name', 'market', 'price', 'change', 'cap', 'as_of', 'logo_url')
    return {'sector': sector, 'status': 'ready' if peers else 'no_data',
            'rows': [{key: r.get(key) for key in fields} for r in [selected, *peers]],
            'comparison_basis': 'same_sector_market_snapshot',
            'source_trace': selected.get('source_trace')}


def detail_snapshot(store, code, view="full", *, row=None):
    from .official_views import read_view
    if row is None:
        row = security_row(store, code, include_quote=view in ('full', 'summary'))
    # Bypass the two-second Redis replica: dependencies are checked against the DB.
    def read(key, default=None):
        return store.get(key, default, use_redis=False)

    summary = read('detail-summary:'+code) if view=='summary' else None
    details = dict(summary['detail']) if summary is not None else dict(read("detail:" + code, {}))
    has_details = summary['present'] if summary is not None else bool(details)
    if view in ('full','activity'):
        try:
            flows = read_flows(store, code)
            if not flows or all(row.get('source_trace', {}).get('source') == 'naver' for row in flows):
                # A separately identified fallback must not fill holes in, or
                # replace values from, Toss's different market aggregation.
                from .providers import read_naver_flows_fallback
                try:
                    fallback = read_naver_flows_fallback(store, code)
                except ValueError:
                    fallback = {'rows': [], 'status': 'error'}
                if fallback['rows']:
                    flows = fallback['rows']
                    details['flows_status'] = fallback['status']
                    details['flows_source'] = fallback['source']
                    details['flows_scope'] = fallback['scope']
                elif fallback['status'] == 'error':
                    details['flows_status'] = 'error'
            if flows or 'flows' in details:
                details['flows'] = flows
        except TradingProjectionUnavailable:
            # A corrupt/missing trading source must not erase the quote or
            # silently fall back to a different provider's observations.
            details['flows'] = []
            details['flows_status'] = 'error'
    if view != "full":
        fields = {"summary": ("company",), "activity": ("flows", "flows_status", "flows_source", "flows_scope", "brokers", "brokers_status", "facts"),
                  "fundamentals": ("financials",), "company": ("company", "shareholders", "consensus", "research"), "filings": ("research",)}[view]
        selected = {key: details[key] for key in fields if key in details}
        if view == "summary":
            company = selected.get("company")
            if isinstance(company, dict):
                selected = ({"company": {"description": company["description"]}}
                            if "description" in company else {})
            elif company is not None:
                selected = {"company": company}
            else:
                selected = {}
        identity = summary_security(row) if view == "summary" else {key: row.get(key) for key in ("code", "name", "market")}
        company = row.get("company", {})
        if view == "summary" and isinstance(company, dict):
            company = {key: company[key] for key in ("products", "sector", "listed_at") if key in company}
        result = {"security": identity, "detail": selected, "prices": None,
                "status": "ready" if has_details else "collecting",
                "company": company,
                "official": read_view(store, code, view) if view in ("company", "fundamentals") else None,
                "filings": read("filings:" + code, {"rows": []}) if view == "filings" else {"rows": []}}
        if view != "summary":
            result["collection"] = read("collection:" + code, {})
        return result
    return (
        {
            "security": row,
            "detail": details,
            "prices": read("prices:" + code),
            "status": "ready" if details else "collecting",
            "collection": read(
                "collection:" + code,
                {
                    "code": code,
                    "attempts": 0,
                    "last_attempt_at": None,
                    "sources": {key: {"status": "pending", "source": None} for key in SOURCES},
                },
            ),
            "filings": read("filings:" + code, {"rows": [], "status": "공시 데이터 연결 전"}),
            "official": read("official:" + code),
            "company": row.get("company", {}),
        }
    )


SUMMARY_SECURITY_FIELDS = (
    "code", "name", "market", "sector", "logo_url", "price", "change", "as_of",
    "received_at", "market_status", "volume", "value", "cap", "foreign", "per",
    "pbr", "roe", "dividend",
)
SUMMARY_FACT_FIELDS = ("volume", "value", "cap", "foreign", "per", "pbr", "roe", "dividend")


def summary_security(row):
    """Keep first-screen values; full provenance and financial history load on demand."""
    result = {key: row[key] for key in SUMMARY_SECURITY_FIELDS if key in row}
    facts = row.get("facts", {})
    if isinstance(facts, dict):
        result["facts"] = {
            key: {field: fact[field] for field in ("value", "period", "unit") if field in fact}
            for key, fact in facts.items()
            if key in SUMMARY_FACT_FIELDS and isinstance(fact, dict)
        }
    return result


@app.get(BASE + "/securities/{code}/price-window")
def security_price_window(code: str, request: Request, range: int = 252, interval: str = 'day', view: str = 'chart'):
    from .price_window import price_window
    return analysis_response(code,request,('price-window-v1',range,interval,view),
        lambda store:price_window(store,code,range_id=range,interval=interval,view=view),
        [f'bars:{code}:통합:day','prices:'+code],history_kind='history')


@app.get(BASE + "/securities/{code}/prices")
def prices(code: str, period: str = "all"):
    valid_code(code)
    if period not in ("week", "month", "quarter", "year", "five_years", "all"):
        raise HTTPException(422, "Invalid price period")
    store = get_store()
    if code not in store.listed_codes():
        raise HTTPException(404, "Unknown security")
    store.request(code)
    from .toss_backfill import request_history
    request_history(store, code, 'history')
    snapshot = store.metadata(f"bars:{code}:통합:day", {}) or store.metadata("prices:" + code, {"rows": [], "status": "collecting"})
    reference = snapshot.get('_series_ref')
    reverse = snapshot.get('_series_reverse', False)
    if period == 'all':
        return {**snapshot, 'rows': store.time_series.rows(reference, reverse)} if reference else snapshot
    if reference:
        # The manifest is committed with observations. Older manifests can
        # recover their final date with an indexed bounds query.
        last = snapshot.get('last_time') or store.time_series.bounds(reference)[1]
    else:
        last = snapshot.get('rows', [{}])[-1].get('date') if snapshot.get('rows') else None
    if not last:
        return {**snapshot, 'rows': []} if reference else snapshot
    from datetime import date, timedelta
    import calendar
    end = date.fromisoformat(last[:10])
    if period == "week":
        cutoff = (end - timedelta(days=7)).isoformat()
    else:
        month_index = end.year * 12 + end.month - 1 - {"month": 1, "quarter": 3, "year": 12, "five_years": 60}[period]
        year, month = divmod(month_index, 12)
        month += 1
        cutoff = end.replace(year=year, month=month, day=min(end.day, calendar.monthrange(year, month)[1])).isoformat()
    rows = (store.time_series.rows(reference, reverse, start=cutoff) if reference
            else [row for row in snapshot['rows'] if row['date'] >= cutoff])
    return {**snapshot, 'rows': rows, 'period': period}


@app.get(BASE + "/securities/{code}/depth")
def security_depth(code: str):
    valid_code(code)
    store = get_store()
    if code not in store.listed_codes():
        raise HTTPException(404, "Unknown security")
    previous = store.get("depth-request:" + code, {})
    from datetime import UTC, datetime
    if not previous.get("at") or (datetime.now(UTC) - datetime.fromisoformat(previous["at"])).total_seconds() >= 10:
        store.put("depth-request:" + code, {"at": now()})
    snapshot = store.get("depth:" + code, {})
    provider = store.get("provider-depth", {})
    if not snapshot and provider.get("status") == "credentials_missing":
        snapshot = {"book_status": "credentials_missing", "trades_status": "credentials_missing"}
    return public_depth(snapshot)


@app.get(BASE + "/securities/{code}/trading")
def security_trading(code: str, request: Request):
    return analysis_response(code, request, ("trading-v1",), lambda store: public_trading(store.get("trading:"+code, {})), ["trading:"+code])


@app.get(BASE + '/securities/{code}/news')
def security_news(code: str, request: Request):
    def build(store):
        snapshot = store.get('news:'+code, {})
        return {key:snapshot.get(key, default) for key,default in
                [('rows',[]),('status','pending'),('received_at',None)]}
    return analysis_response(code, request, ('news-v1',), build, ['news:'+code])


@app.get(BASE + "/securities/{code}/trading/history")
def security_trading_history(code: str, request: Request, kind: str='investor-trading',
                             metric: str='netBuyVolume', start: str | None=None, end: str | None=None):
    from .trading_history import trading_history, read_history
    try:
        trading_history({},kind,metric,start,end)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    valid_code(code)
    store=get_store()
    if code not in store.listed_codes(): raise HTTPException(404,'Unknown security')
    store.request(code)
    from .toss_backfill import request_history
    request_history(store, code, kind)
    source=f'toss:{kind}:{code}'
    return analysis_response(code,request,('trading-history-v1',kind,metric,start,end),
        lambda store:trading_history(read_history(store,source,start,end),kind,metric,start,end),[source])


def request_trading_refresh(store, code, kinds):
    """Persist refresh interest after the response; preserve usable reads on failure."""
    from .toss_backfill import request_history
    for kind in (None, *kinds):
        try:
            store.request(code) if kind is None else request_history(store, code, kind)
        except Exception as exc:
            logging.getLogger('market-refresh').warning(
                'Trading refresh intent failed: %s', type(exc).__name__
            )


@app.get(BASE + "/securities/{code}/short-selling")
def short_selling(code: str, background_tasks: BackgroundTasks, period: str = "quarter"):
    valid_code(code)
    if period not in ("month", "quarter", "year", "three_years", "five_years", "all"):
        raise HTTPException(422, "Invalid short selling period")
    store = get_store()
    if code not in store.listed_codes():
        raise HTTPException(404, "종목을 찾을 수 없습니다.")
    background_tasks.add_task(request_trading_refresh, store, code, ('short-selling',))
    try:
        return read_short_selling(store, code, period)
    except TradingProjectionUnavailable as exc:
        raise HTTPException(503, '자료를 불러오지 못했어요. 다시 시도해 주세요.') from exc


@app.get(BASE + "/securities/{code}/financing")
def financing(code: str, background_tasks: BackgroundTasks, period: str = "quarter"):
    valid_code(code)
    if period not in ("month", "quarter", "year", "three_years", "five_years", "all"):
        raise HTTPException(422, "Invalid financing period")
    store = get_store()
    if code not in store.listed_codes():
        raise HTTPException(404, "종목을 찾을 수 없습니다.")
    background_tasks.add_task(request_trading_refresh, store, code, ('credit-trades', 'securities-lending'))
    try:
        return read_financing(store, code, period)
    except TradingProjectionUnavailable as exc:
        raise HTTPException(503, '자료를 불러오지 못했어요. 다시 시도해 주세요.') from exc


@app.get(BASE + "/securities/{code}/financials/ttm")
def security_ttm(code: str, request: Request, basis: str = 'CFS'):
    from .official_views import read_view
    from .issuer_documents import company_link, report_dependencies
    if basis not in ('CFS', 'OFS'):
        raise HTTPException(422, 'Invalid accounting basis')
    store=get_store()
    link=company_link(store,code)
    issuer=link['issuer_security_code'] if link else code
    def build(store):
        result=(read_view(store,issuer,'analysis') or {}).get('ttm',{}).get(basis,
            {'basis':basis,'rows':[],'status':'period_evidence_required'})
        return {**result,'issuer_link':{**link,'scope':'company_financial_totals'}} if link else result
    return analysis_response(code, request, ('ttm-summary-v3', basis),build,
        report_dependencies(store,code)+['share-basis:'+issuer])


@app.get(BASE + "/securities/{code}/financials/timeline")
def security_financial_timeline(code: str, request: Request, basis: str='CFS'):
    from .official_views import read_view
    from .financial_timeline import annual_timeline
    from .issuer_documents import report_dependencies
    if basis not in ('CFS','OFS'): raise HTTPException(422,'Invalid accounting basis')
    return analysis_response(code,request,('financial-timeline-v1',basis),
        lambda store:annual_timeline(store,(read_view(store,code,'reports') or {}).get('financial_reports',{}),basis),report_dependencies(get_store(),code))


@app.get(BASE + "/securities/{code}/financials")
def security_financials(
    code: str,
    request: Request,
    background_tasks: BackgroundTasks,
    year: str | None = None,
    report_code: str | None = None,
    basis: str | None = None,
    category: str | None = None,
):
    """Return a compact report index or one lazily loaded DART financial dataset."""
    valid_code(code)
    store = get_store()
    if code not in store.listed_codes():
        raise HTTPException(404, "Unknown security")
    cached = cached_public(("financials", code, year, report_code, basis, category), request)
    if cached is not None:
        return cached

    background_tasks.add_task(store.request, code)
    from .official_views import read_view
    official = read_view(store,code,'reports') or {}
    reports = official.get("financial_reports") or {}
    indicators = official.get("financial_indicators") or {}
    issuer_link=official.get('issuer_link')
    if year is None and report_code is None and basis is None and category is None:
        from .authenticated_sources import dart_financial_task_plan
        index = {
            "available_periods": [{"year":t["year"],"report_code":t["report_code"],"report_name":{"11011":"연간","11012":"반기","11013":"1분기","11014":"3분기"}[t["report_code"]]} for t in dart_financial_task_plan() if t["kind"]=="statement" and t["basis"]=="CFS"],
            "reports": [
                {
                    "year": row.get("year"),
                    "report_code": row.get("report_code"),
                    "report_name": row.get("report_name"),
                    "basis": row.get("basis"),
                    "status": row.get("status"),
                    "row_count": row.get("row_count", 0),
                }
                for row in reports.values()
            ],
            "indicators": [
                {
                    "year": row.get("year"),
                    "report_code": row.get("report_code"),
                    "report_name": row.get("report_name"),
                    "category_code": row.get("category_code"),
                    "status": row.get("status"),
                    "row_count": row.get("row_count", 0),
                }
                for row in indicators.values()
            ],
        }
        if issuer_link:
            index['issuer_link']={**issuer_link,'scope':'company_financial_totals'}
        return public_response(
            index, ("financials", code, year, report_code, basis, category), request
        )

    if (
        not year
        or not re.fullmatch(r"20\d{2}", year)
        or report_code not in {"11011", "11012", "11013", "11014"}
        or (basis is None) == (category is None)
        or (basis is not None and basis not in {"CFS", "OFS"})
        or (category is not None and category not in {"M210000", "M220000", "M230000", "M240000"})
    ):
        raise HTTPException(422, "Provide a valid year, report code, and exactly one data type")

    selector = basis if basis else category
    identity = f"{year}:{report_code}:{selector}"
    index_row = (reports if basis else indicators).get(identity)
    if not index_row:
        from .authenticated_sources import dart_financial_task_plan
        query_key = f"{'statement' if basis else 'indicator'}:{identity}"
        if query_key not in {t['query_key'] for t in dart_financial_task_plan()}:
            raise HTTPException(422, 'Reporting period is outside the available range')
        target=issuer_link['issuer_security_code'] if issuer_link else code
        store.put('financial-request:' + target, {'query_key':query_key})
        result = {"status": "pending", "rows": []}
    elif index_row.get("status") != "ready":
        result = {"status": index_row.get("status", "pending"), "rows": []}
    else:
        snapshot_key = index_row.get("snapshot_key")
        snapshot = store.get(snapshot_key) if snapshot_key else None
        if not snapshot:
            raise HTTPException(503, "Financial data is being refreshed")
        from .issuer_documents import company_report
        result = company_report(snapshot,issuer_link)
    return public_response(
        result, ("financials", code, year, report_code, basis, category), request
    )


@app.websocket(BASE + "/stream")
async def stream(ws: WebSocket):
    expected = os.getenv("MARKET_PUBLIC_ORIGIN")
    if not public_socket_origin(ws.headers.get("origin"), expected):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        message = json.loads(raw)
        if not isinstance(message, dict):
            await ws.close(code=1008)
            return
        codes = message.get("codes", [])
        channels = message.get("channels", ["quotes"])
        if not isinstance(channels, list) or any(c not in ("quotes", "depth") for c in channels):
            await ws.close(code=1008)
            return
        if (
            not isinstance(codes, list)
            or len(codes) > 40
            or any(not isinstance(c, str) or not re.fullmatch(r"[A-Z0-9]{6}", c) for c in codes)
        ):
            await ws.close(code=1008)
            return
        last = None
        last_depth = {}
        known = set(get_store().listed_codes())
        codes = list(dict.fromkeys(c for c in codes if c in known))
        hub = ws.app.state.quote_hub
        version = -1
        last_sent = 0
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            for code in codes:
                get_store().request(code)
            version = await hub.wait(version)
            selected = [hub.rows[c] for c in codes if c in hub.rows]
            sequences = {c: hub.sequences.get(c, 0) for c in codes}
            if "quotes" in channels and sequences != last:
                await ws.send_json({"type": "snapshot", "rows": selected,
                                    "epoch": hub.epoch, "sequences": sequences, "sent_at": now()})
                last = sequences
                last_sent = time.monotonic()
            if "depth" in channels:
                for code in codes:
                    current = hub.trading.get(code)
                    if current is not None and last_depth.get(code) != current:
                        await ws.send_json({"type":"depth", "code":code, "data":current, "epoch":hub.epoch, "version":version, "sent_at":now()})
                        last_depth[code] = current
                        last_sent = time.monotonic()
            if time.monotonic() - last_sent >= 10:
                await ws.send_json({"type": "heartbeat", "sent_at": now()})
                last_sent = time.monotonic()
        await ws.close(code=1000)
    except (WebSocketDisconnect, TimeoutError, json.JSONDecodeError):
        return
