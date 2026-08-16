from fastapi.testclient import TestClient

from services.api.auth import owner_key_from_email, sign_pf_session, verify_pf_session
from services.api.main import app
from services.api.security import reset_rate_limit_state


def test_pf_session_signature_and_allowlist(monkeypatch):
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "owner@example.com")
    token = sign_pf_session("owner@example.com", "secret", now=100)

    session = verify_pf_session(token, secret="secret", now=101)
    assert session is not None
    assert session.email == "owner@example.com"

    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "other@example.com")
    assert verify_pf_session(token, secret="secret", now=101) is None
    assert verify_pf_session(token, secret="secret", now=100 + 8 * 60 * 60 + 1) is None


def test_owner_key_hashes_email_without_leaking_identity():
    owner_key = owner_key_from_email("Owner@Example.com")

    assert owner_key == owner_key_from_email("owner@example.com")
    assert owner_key.startswith("user_")
    assert "owner" not in owner_key
    assert "example" not in owner_key


def test_api_auth_middleware_protects_private_routes(monkeypatch):
    monkeypatch.delenv("API_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("PF_COOKIE_SECRET", "test-secret")
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "owner@example.com")
    client = TestClient(app)

    health = client.get("/api/health")
    assert health.status_code == 200

    blocked = client.get("/api/v1/securities/search?q=AAPL")
    assert blocked.status_code == 401

    token = sign_pf_session("owner@example.com", "test-secret")
    client.cookies.set("pf_session", token)
    allowed = client.get("/api/v1/securities/search?q=AAPL")
    assert allowed.status_code == 200
    assert allowed.json()["data"][0]["ticker"] == "AAPL"


def test_api_cors_allows_configured_local_origin():
    client = TestClient(app)

    response = client.options(
        "/api/v1/securities/search",
        headers={
            "Origin": "http://127.0.0.1:3100",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3100"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_api_security_headers_are_set():
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "private, no-store"


def test_api_rate_limit_returns_429(monkeypatch):
    reset_rate_limit_state()
    monkeypatch.setenv("API_AUTH_DISABLED", "true")
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("API_RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")
    client = TestClient(app)

    first = client.get("/api/v1/securities/search?q=AAPL")
    second = client.get("/api/v1/securities/search?q=AAPL")
    third = client.get("/api/v1/securities/search?q=AAPL")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"
    assert third.headers["retry-after"]
    assert third.headers["x-content-type-options"] == "nosniff"
    assert third.headers["cache-control"] == "private, no-store"
    reset_rate_limit_state()


def test_api_auth_middleware_rejects_tampered_cookie(monkeypatch):
    monkeypatch.delenv("API_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("PF_COOKIE_SECRET", "test-secret")
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "owner@example.com")
    client = TestClient(app)
    token = sign_pf_session("owner@example.com", "test-secret")
    client.cookies.set("pf_session", f"{token[:-1]}x")

    response = client.get("/api/v1/securities/search?q=AAPL")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_api_auth_middleware_rejects_disallowed_email(monkeypatch):
    monkeypatch.delenv("API_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("PF_COOKIE_SECRET", "test-secret")
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "owner@example.com")
    client = TestClient(app)
    client.cookies.set("pf_session", sign_pf_session("other@example.com", "test-secret"))

    response = client.get("/api/v1/securities/search?q=AAPL")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_api_auth_middleware_rejects_when_secret_missing(monkeypatch):
    monkeypatch.delenv("API_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.delenv("PF_COOKIE_SECRET", raising=False)
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "owner@example.com")
    client = TestClient(app)
    client.cookies.set("pf_session", sign_pf_session("owner@example.com", "test-secret"))

    response = client.get("/api/v1/securities/search?q=AAPL")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_authenticated_chart_layouts_use_session_owner(monkeypatch, tmp_path):
    monkeypatch.delenv("API_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("PF_COOKIE_SECRET", "test-secret")
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "owner@example.com")
    monkeypatch.setenv("CHART_LAYOUT_DIR", str(tmp_path / "chart-layouts"))
    client = TestClient(app)
    client.cookies.set("pf_session", sign_pf_session("owner@example.com", "test-secret"))

    saved = client.post(
        "/api/v1/chart-layouts",
        json={"name": "private layout", "owner_key": "attempted_override", "company_id": "AAPL"},
    )

    assert saved.status_code == 200
    layout = saved.json()["data"]
    assert layout["owner_key"] == owner_key_from_email("owner@example.com")
    assert layout["owner_key"] != "attempted_override"

    listed = client.get("/api/v1/chart-layouts?owner_key=attempted_override")
    assert listed.status_code == 200
    assert listed.json()["data"]["owner_key"] == owner_key_from_email("owner@example.com")
    assert listed.json()["data"]["items"][0]["name"] == "private layout"
