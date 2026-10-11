"""Run current market fixtures without provider credentials or external sockets.

CI additionally isolates the entire process tree in a loopback-only network
namespace. The Python audit hook is a supplemental local guard, not an OS sandbox.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_DATABASE_PORTS = {5432, 5433, 54420, 6379}


def fixture_files(root: Path = ROOT, selected: list[str] | None = None) -> list[Path]:
    """Only the flat current-app suite; legacy nested tests are a separate gate."""
    files = sorted((root / "tests").glob("test_*.py"))
    if not selected:
        return files
    names = set(selected)
    available = {path.name for path in files}
    if names - available:
        raise ValueError("--test accepts existing root test_*.py filenames only")
    return [path for path in files if path.name in names]


def fixture_environment(environment: dict[str, str], storage: Path) -> dict[str, str]:
    prefixes = ("MARKET_", "TOSS_", "DART_", "OPENDART_", "KIWOOM_", "KIS_", "KRX_", "PG")
    removed = {"DATABASE_URL", "REDIS_URL", "PYTEST_ADDOPTS"}
    result = {
        key: value for key, value in environment.items()
        if key not in removed and not key.startswith(prefixes)
    }
    # Leave the URL unset: Store(root=tmp_path) must still create its own file DB,
    # including the real reopen and multi-connection fixture contracts.
    result.update(MARKET_STORAGE_DIR=str(storage), MARKET_CI_FIXTURES="1",
                  PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return result


def network_audit(event: str, args: tuple) -> None:
    if event == "socket.getaddrinfo":
        host, port = args[:2]
    elif event in {"socket.connect", "socket.sendto"}:
        address = args[1]
        if not isinstance(address, tuple) or len(address) < 2:
            raise PermissionError("Market fixtures cannot connect to local service sockets")
        host, port = address[:2]
    else:
        return
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        loopback = False
    try:
        blocked_port = int(port) in BLOCKED_DATABASE_PORTS
    except (ValueError, TypeError):
        blocked_port = True
    if not loopback or blocked_port:
        # Do not echo hostnames, URLs, credentials or database addresses.
        raise PermissionError("Market fixtures allow only loopback non-database connections")


class StaticFixtureHandler(SimpleHTTPRequestHandler):
    """Read-only static export server: clean routes, no backend proxy or listing."""

    def do_GET(self):
        self._serve(head=False)

    def do_HEAD(self):
        self._serve(head=True)

    def _serve(self, *, head: bool):
        route = unquote(urlsplit(self.path).path)
        if route.startswith(("/api/", "/internal/", "/ops/")):
            self.send_error(404, "API fixture required")
            return
        root = Path(self.directory).resolve()
        target = (root / route.lstrip("/")).resolve()
        if route in {"/", "/stocks", "/stocks/"}:
            target = root / "stocks.html"
        elif target.suffix == "" and not target.is_file():
            target = target.with_suffix(".html")
        target = target.resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self.send_error(404)
            return
        self.path = "/" + target.relative_to(root).as_posix()
        try:
            super().do_HEAD() if head else super().do_GET()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # A cancelled navigation is expected in request-efficiency tests.

    def log_message(self, _format, *args):
        pass  # Never record query strings or user-entered stock searches.


def static_server(directory: Path, port: int) -> ThreadingHTTPServer:
    if not (directory / "stocks.html").is_file():
        raise ValueError("Build the static candidate before serving fixtures")
    return ThreadingHTTPServer(
        ("127.0.0.1", port), partial(StaticFixtureHandler, directory=str(directory.resolve()))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="append", help="One root test filename; repeat to select")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--junitxml", type=Path)
    parser.add_argument("--serve-static", action="store_true")
    parser.add_argument("--port", type=int, default=3219)
    args = parser.parse_args()
    if args.serve_static:
        with static_server(ROOT / "apps/web/out", args.port) as server:
            print(f"Fixture static server: http://127.0.0.1:{server.server_port}", flush=True)
            server.serve_forever()
        return 0
    try:
        files = fixture_files(selected=args.test)
    except ValueError as error:
        parser.error(str(error))
    # Windows Python's optional WMI platform probe can hang during pytest import.
    if sys.platform == "win32":
        sys.modules["_wmi"] = None
    with tempfile.TemporaryDirectory(prefix="market-fixtures-") as temporary:
        clean = fixture_environment(dict(os.environ), Path(temporary))
        os.environ.clear()
        os.environ.update(clean)
        sys.addaudithook(network_audit)
        sys.path.insert(0, str(ROOT))
        import pytest

        options = [str(path) for path in files]
        options += ["-p", "pytest_asyncio.plugin", "-m", "not network and not integration"]
        if args.collect_only:
            options.append("--collect-only")
        if args.junitxml:
            options.extend(["--junitxml", str(args.junitxml)])
        return pytest.main(options)


if __name__ == "__main__":
    raise SystemExit(main())
