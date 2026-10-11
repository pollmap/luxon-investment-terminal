"""fixture_non_production: public static-origin transport boundaries."""
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from services.market_dashboard.public_transport import (
    EDGE_ORIGIN, PUBLIC_ORIGIN, PublicReadCors, public_socket_origin,
)


@pytest.fixture
def client():
    async def handler(request):
        return JSONResponse({"fixture_non_production": True}, headers={"Vary": "Accept-Encoding", "ETag": '"fixture"'})
    app = Starlette(routes=[Route("/{path:path}", handler, methods=["GET", "POST", "OPTIONS", "DELETE"])])
    return TestClient(PublicReadCors(app))


def test_static_origin_can_read_public_data_without_credentials(client):
    result = client.get("/api/market/v1/securities/005930?view=summary", headers={"Origin": EDGE_ORIGIN})
    assert result.status_code == 200
    assert result.headers["access-control-allow-origin"] == EDGE_ORIGIN
    assert "Origin" in result.headers["vary"] and "Accept-Encoding" in result.headers["vary"]
    assert "ETag" in result.headers["access-control-expose-headers"]
    assert "access-control-allow-credentials" not in result.headers


@pytest.mark.parametrize("origins", [[None, EDGE_ORIGIN], [EDGE_ORIGIN, None]])
def test_every_public_cache_variant_declares_origin_before_cache_prime(client, origins):
    for origin in origins:
        result = client.get("/api/market/v1/search?q=005930", headers={"Origin": origin} if origin else {})
        assert {part.strip() for part in result.headers["vary"].split(",")} == {"Origin", "Accept-Encoding"}
        assert result.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize("path,headers", [
    ("/api/market/v1/health", {"Origin": "https://example.com"}),
    ("/api/market/v1/health", {"Origin": EDGE_ORIGIN, "Authorization": "Bearer fixture"}),
    ("/api/market/v1/health", {"Origin": EDGE_ORIGIN, "Cookie": "fixture=test"}),
    ("/api/market/v1/internal/state", {"Origin": EDGE_ORIGIN}),
    ("/api/market/v1/account", {"Origin": EDGE_ORIGIN}),
    ("/internal/history", {"Origin": EDGE_ORIGIN}),
])
def test_private_or_other_origin_requests_do_not_gain_browser_access(client, path, headers):
    assert "access-control-allow-origin" not in client.get(path, headers=headers).headers


@pytest.mark.parametrize("path", ["screener", "screener/export", "analysis/scenario"])
def test_only_public_read_posts_allow_preflight(client, path):
    result = client.options("/api/market/v1/" + path, headers={"Origin": EDGE_ORIGIN,
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type, if-none-match"})
    assert result.status_code == 204
    assert result.headers["access-control-allow-origin"] == EDGE_ORIGIN
    assert "access-control-allow-credentials" not in result.headers


@pytest.mark.parametrize("method,path,headers", [
    ("DELETE", "screener", "content-type"),
    ("POST", "orders", "content-type"),
    ("GET", "securities/005930", "authorization"),
    ("GET", "securities/005930", "x-api-key"),
])
def test_preflight_rejects_writes_and_credential_headers(client, method, path, headers):
    result = client.options("/api/market/v1/" + path, headers={"Origin": EDGE_ORIGIN,
        "Access-Control-Request-Method": method, "Access-Control-Request-Headers": headers})
    assert result.status_code == 403
    assert "access-control-allow-origin" not in result.headers


def test_read_condition_and_websocket_origin_scope(client):
    result = client.options("/api/market/v1/securities/005930", headers={"Origin": EDGE_ORIGIN,
        "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "If-None-Match, cache-control"})
    assert result.status_code == 204
    assert public_socket_origin(EDGE_ORIGIN, PUBLIC_ORIGIN)
    assert public_socket_origin(PUBLIC_ORIGIN, PUBLIC_ORIGIN)
    assert not public_socket_origin("https://example.com", PUBLIC_ORIGIN)
    assert not public_socket_origin(None, PUBLIC_ORIGIN)
    assert not public_socket_origin(EDGE_ORIGIN, "https://private.example.com")
    assert public_socket_origin("http://127.0.0.1:8794", None)
