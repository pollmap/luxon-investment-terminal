"""Nginx second-boundary semantics; source timestamps are never rewritten."""

import json

import pytest
from starlette.requests import Request
from starlette.responses import Response

from services.market_dashboard import app as market


async def clock_response(monkeypatch, current_ms, validated=None):
    monkeypatch.setattr(market.time, "time", lambda: current_ms / 1000)
    request = Request({
        "type": "http", "method": "GET", "path": market.BASE + "/search",
        "query_string": b"q=005930", "headers": [],
    })
    headers = {"X-Market-Validated-At": validated} if validated is not None else {}
    original = {"as_of": "2026-10-02T15:30:00+09:00", "price": 100,
                "source": "fixture_non_production"}

    async def upstream(_request):
        return Response(json.dumps(original), headers=headers, media_type="application/json")

    response = await market.public_validation_clock(request, upstream)
    assert response.status_code == 200
    assert json.loads(response.body) == original
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [0, 1, 999])
async def test_nginx_integer_second_comparison_never_extends_one_second(monkeypatch, offset):
    validated = 1791558604000 + offset
    response = await clock_response(monkeypatch, validated)
    assert response.headers["X-Market-Validated-At"] == str(validated)
    valid_sec = int(response.headers["X-Accel-Expires"].removeprefix("@"))
    # ngx_http_file_cache_read: expired iff valid_sec < ngx_time(). A timestamp
    # equal to the current integer second remains a cache HIT until the next one.
    assert valid_sec == validated // 1000
    assert valid_sec >= validated // 1000
    assert 0 < (valid_sec + 1) * 1000 - validated <= 1000
    for elapsed in (1000, 1001, 1999, 2000):
        assert valid_sec < (validated + elapsed) // 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("age_ms", [1, 500, 999, 1000, 2000, 60_000])
async def test_upstream_age_is_preserved_not_restarted(monkeypatch, age_ms):
    validated = 1791558604001
    current = validated + age_ms
    response = await clock_response(monkeypatch, current, str(validated))
    assert response.headers["X-Market-Validated-At"] == str(validated)
    assert response.headers["X-Accel-Expires"] == "@1791558604"
    valid_sec = 1791558604
    if age_ms >= 1000:
        assert valid_sec < current // 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    "", "invalid", "nan", "Infinity", "1e12", "1791558604000.5",
    "-1", "0", "+1791558604000", " 1791558604000", "9" * 5000,
    "1791558604001", "1791558604999", "1791558605000", "9999999999999999",
])
async def test_invalid_or_future_clock_disables_both_shared_caches(monkeypatch, invalid):
    response = await clock_response(monkeypatch, 1791558604000, invalid)
    assert response.headers["X-Market-Validated-At"] == invalid
    assert response.headers["X-Accel-Expires"] == "0"
    assert response.headers["Cache-Control"] == "private, no-store"
