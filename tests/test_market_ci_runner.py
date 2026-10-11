"""fixture_non_production: CI boundaries, with no provider or database requests."""
import importlib.util
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

SPEC = importlib.util.spec_from_file_location(
    "market_fixture_runner", Path(__file__).parents[1] / "scripts/run_market_tests.py"
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_selection_never_collects_legacy_or_arbitrary_paths(tmp_path):
    tests = tmp_path / "tests"
    (tests / "api").mkdir(parents=True)
    (tests / "test_b.py").touch()
    (tests / "test_a.py").touch()
    (tests / "api/test_live.py").touch()
    assert [path.name for path in runner.fixture_files(tmp_path)] == ["test_a.py", "test_b.py"]
    assert [path.name for path in runner.fixture_files(tmp_path, ["test_b.py"])] == ["test_b.py"]
    with pytest.raises(ValueError, match="root"):
        runner.fixture_files(tmp_path, ["api/test_live.py"])


def test_environment_drops_live_credentials_and_integrations(tmp_path):
    environment = {
        "PATH": "preserved", "MARKET_FINANCIAL_TRACE_TEST_POSTGRES_URL": "private",
        "MARKET_DATABASE_URL": "private", "TOSS_CLIENT_SECRET": "private",
        "DART_API_KEY": "private", "DATABASE_URL": "private", "PGHOST": "private",
        "PYTEST_ADDOPTS": "--override-ini testpaths=tests/api",
    }
    result = runner.fixture_environment(environment, tmp_path)
    assert "private" not in result.values()
    assert result["PATH"] == "preserved"
    assert "MARKET_DATABASE_URL" not in result
    assert result["MARKET_STORAGE_DIR"] == str(tmp_path)
    assert "PYTEST_ADDOPTS" not in result


@pytest.mark.parametrize("host,port", [
    ("provider.example", 443), ("203.0.113.1", 80), ("localhost", 54420),
    ("127.0.0.1", 5432), ("::1", 6379), ("::ffff:203.0.113.1", 443),
])
def test_socket_guard_rejects_before_any_request(host, port):
    with pytest.raises(PermissionError):
        runner.network_audit("socket.connect", (None, (host, port)))
    with pytest.raises(PermissionError):
        runner.network_audit("socket.getaddrinfo", (host, port, 0, 0, 0))


def test_guard_allows_fixture_http_but_not_unix_service_socket():
    runner.network_audit("socket.connect", (None, ("127.0.0.1", 3219)))
    runner.network_audit("socket.getaddrinfo", ("localhost", 3219, 0, 0, 0))
    with pytest.raises(PermissionError):
        runner.network_audit("socket.connect", (None, "/run/postgresql/.s.PGSQL.5432"))


def test_static_server_clean_routes_no_proxy_listing_or_traversal(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    (root / "stocks.html").write_text("fixture_non_production", encoding="utf8")
    (root / "stocks").mkdir()  # Assets must not turn /stocks into a directory listing.
    (tmp_path / "outside.txt").write_text("must-not-serve", encoding="utf8")
    with runner.static_server(root, 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for path in ("/", "/stocks", "/stocks?code=fixture", "/stocks/"):
                with urlopen(base + path, timeout=3) as response:
                    assert response.read() == b"fixture_non_production"
            for path in ("/api/market/v1/search", "/internal/status", "/ops/settings",
                         "/%2e%2e/outside.txt", "/unknown"):
                with pytest.raises(HTTPError) as error:
                    urlopen(base + path, timeout=3)
                assert error.value.code == 404
        finally:
            server.shutdown()
            thread.join(timeout=3)
