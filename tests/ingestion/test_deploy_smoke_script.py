import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
KR_TOP10_TICKERS = [
    "005930.KS",
    "000660.KS",
    "402340.KS",
    "005380.KS",
    "028260.KS",
    "032830.KS",
    "373220.KS",
    "207940.KS",
    "329180.KS",
    "009155.KS",
]
KR_PARTIAL_MARKET_GAP_FACT_ID = (
    "005930.KS-2022-data_quality.kr_market_gap.source_no_rows_before_first_trade"
)


class SmokeHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if self.path == "/api/health":
            self._json({"status": "ok"})
            return
        if self.path == "/api/v1/system/readiness":
            self._json({"status": "fixture_only", "data_mode": "fixture_non_production"})
            return
        if parsed.path == "/api/v1/system/kr-valuation-cache-coverage":
            self._json(
                {
                    "market": "KR",
                    "data_backend": "kr_valuation_input_cache",
                    "data_mode": "source_backed_cache",
                    "coverage_status": "complete",
                    "quality_status": "source_backed_cache_complete",
                    "summary": {
                        "tickers_expected": len(KR_TOP10_TICKERS),
                        "cache_files_found": len(KR_TOP10_TICKERS),
                        "valuation_ready": len(KR_TOP10_TICKERS),
                        "complete": len(KR_TOP10_TICKERS),
                        "partial_source_backed": 0,
                        "missing": 0,
                        "full_coverage_ready": len(KR_TOP10_TICKERS),
                        "financial_numbers_allowed": len(KR_TOP10_TICKERS),
                    },
                    "source_trace": {
                        "source_document_id": "kr-valuation-cache-universe-summary",
                    },
                    "rows": [
                        {
                            "ticker": ticker,
                            "cache_found": True,
                            "valuation_ready": True,
                            "financial_numbers_allowed": True,
                            "coverage_status": "complete",
                        }
                        for ticker in KR_TOP10_TICKERS
                    ],
                }
            )
            return
        if parsed.path == "/api/v1/system/source-coverage":
            require_consensus = "require_consensus_forecast=true" in self.path
            is_kr_gate = query.get("market", [""])[0] == "KR"
            if is_kr_gate:
                self._json(
                    {
                        "status": "ready",
                        "data_mode": "source_backed",
                        "postgres": {"reachable": True, "error": None},
                        "summary": {
                            "tickers_expected": len(KR_TOP10_TICKERS),
                            "core_ready": len(KR_TOP10_TICKERS),
                            "consensus_forecast_ready": len(KR_TOP10_TICKERS),
                            "missing_core": [],
                            "missing_consensus_forecast": [],
                        },
                        "tickers": [
                            {
                                "ticker": ticker,
                                "counts": {
                                    "adjusted_years": 3,
                                    "price_years": 3,
                                    "consensus_valuation_years": 5,
                                },
                                "method_counts": {"s1": 0, "s2": 0, "s4": 0},
                                "core_ready": True,
                                "consensus_forecast_ready": True,
                                "missing_required": [],
                            }
                            for ticker in KR_TOP10_TICKERS
                        ],
                    }
                )
                return
            self._json(
                {
                    "status": "ok" if require_consensus else "missing",
                    "data_mode": "source_backed" if require_consensus else "source_backed_required",
                    "summary": {
                        "tickers_expected": 1,
                        "core_ready": 1 if require_consensus else 0,
                        "consensus_forecast_ready": 1 if require_consensus else 0,
                        "missing_core": [] if require_consensus else ["AAPL"],
                        "missing_consensus_forecast": [] if require_consensus else ["AAPL"],
                    },
                    "tickers": [
                        {
                            "ticker": "AAPL",
                            "counts": {"adjusted_years": 0, "price_years": 0},
                            "method_counts": {"s1": 0, "s2": 0, "s4": 0},
                            "consensus_forecast_ready": require_consensus,
                        }
                    ],
                }
            )
            return
        if self.path.startswith("/api/v1/industry-series"):
            self._json(
                {
                    "data": [],
                    "meta": {
                        "data_mode": "source_backed_required",
                        "quality_status": "missing_source_backed_data",
                    },
                }
            )
            return
        if self.path.startswith("/api/v1/macro-series"):
            self._json(
                {
                    "data": [],
                    "meta": {
                        "data_mode": "source_backed_required",
                        "quality_status": "missing_source_backed_data",
                    },
                }
            )
            return
        if self.path.startswith("/api/v1/securities/search"):
            self._json({"data": [{"ticker": "AAPL"}]})
            return
        if self.path.startswith("/api/v1/companies/AAPL/valuation-map"):
            self._json(
                {
                    "data": [
                        {
                            "fiscal_year": 2024,
                            "forecast_flag": False,
                            "source_trace": {"source": "fixture"},
                        },
                        {
                            "fiscal_year": 2025,
                            "forecast_flag": True,
                            "source_trace": {
                                "source": "forecast",
                                "quality_status": "source_backed_consensus_snapshots",
                            },
                        },
                    ],
                    "meta": {
                        "forecast": {
                            "source": "consensus_snapshot",
                            "formula": "point-in-time consensus EPS snapshots",
                            "consensus": {
                                "quality_status": "source_backed_consensus_snapshots",
                            },
                        }
                    },
                }
            )
            return
        if self.path.startswith("/api/v1/companies/AAPL/forecast-snapshots"):
            self._json(
                {
                    "data": {
                        "cases": [
                            {
                                "case": "median",
                                "growth_rate_pct": "8.00",
                                "estimate_eps": "10.00",
                            }
                        ],
                        "revisions": [
                            {
                                "as_of_label": "current",
                                "estimate_eps": "10.00",
                                "analyst_count": 12,
                            }
                        ],
                        "sentiment": {"label": "neutral"},
                        "scorecard": {
                            "summary": {
                                "required_source": "point_in_time_consensus_snapshots",
                            }
                        },
                        "source_trace": {
                            "quality_status": "source_backed_consensus_snapshots",
                        },
                        "meta": {"data_mode": "source_backed"},
                    }
                }
            )
            return
        if self.path.startswith("/api/v1/charts/valuation-map/AAPL.svg"):
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.end_headers()
            self.wfile.write(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
            return
        if self.path.startswith("/api/v1/charts/valuation-map/AAPL.png"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"\x89PNG\r\n\x1a\n")
            return
        self.send_error(404)

    def log_message(self, *_args):
        return

    def _json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PartialAuditSmokeHandler(SmokeHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/system/kr-valuation-cache-coverage":
            self._json(_partial_kr_cache_coverage_payload())
            return
        if parsed.path.startswith("/api/data-audit/"):
            fact_id = unquote(parsed.path.rsplit("/", 1)[-1])
            self._json(_data_audit_fact_payload(fact_id))
            return
        super().do_GET()


class MissingPartialAuditRefsSmokeHandler(SmokeHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/system/kr-valuation-cache-coverage":
            self._json(_partial_kr_cache_coverage_payload(include_gap_refs=False))
            return
        super().do_GET()


class MissingPartialAuditFactSmokeHandler(SmokeHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/system/kr-valuation-cache-coverage":
            self._json(_partial_kr_cache_coverage_payload())
            return
        super().do_GET()


def _partial_kr_cache_coverage_payload(include_gap_refs=True):
    rows = []
    for ticker in KR_TOP10_TICKERS:
        is_partial = ticker == "005930.KS"
        rows.append(
            {
                "ticker": ticker,
                "cache_found": True,
                "valuation_ready": True,
                "financial_numbers_allowed": True,
                "coverage_status": "partial_source_backed" if is_partial else "complete",
                "gap_audit_refs": [
                    {
                        "scope": "market",
                        "fiscal_year": 2022,
                        "status": "source_no_rows_before_first_trade",
                        "fact_name": "data_quality.kr_market_gap.source_no_rows_before_first_trade",
                        "fact_id": KR_PARTIAL_MARKET_GAP_FACT_ID,
                        "label": "Market FY2022",
                        "source_document_id": (
                            "kr-cache:005930.KS:2022:market-gap:"
                            "source_no_rows_before_first_trade"
                        ),
                        "source_type": "kr_cache_market_gap_diagnostic",
                        "method": "KR_CACHE_MARKET_GAP_DIAGNOSTIC",
                        "quality_status": "source_no_rows_before_first_trade",
                        "reason": "source_no_rows_before_first_trade",
                        "next_action": "Add alternate market source evidence or keep partial early-history coverage.",
                    }
                ]
                if is_partial and include_gap_refs
                else [],
            }
        )
    return {
        "market": "KR",
        "data_backend": "kr_valuation_input_cache",
        "data_mode": "source_backed_cache",
        "coverage_status": "partial_source_backed",
        "quality_status": "source_backed_cache_partial",
        "summary": {
            "tickers_expected": len(KR_TOP10_TICKERS),
            "cache_files_found": len(KR_TOP10_TICKERS),
            "valuation_ready": len(KR_TOP10_TICKERS),
            "complete": len(KR_TOP10_TICKERS) - 1,
            "partial_source_backed": 1,
            "missing": 0,
            "full_coverage_ready": len(KR_TOP10_TICKERS) - 1,
            "financial_numbers_allowed": len(KR_TOP10_TICKERS),
        },
        "source_trace": {
            "source_document_id": "kr-valuation-cache-universe-summary",
        },
        "rows": rows,
    }


def _data_audit_fact_payload(fact_id):
    return {
        "data": {
            "fact_id": fact_id,
            "fact_name": "data_quality.kr_market_gap.source_no_rows_before_first_trade",
            "source_trace": {
                "source_document_id": (
                    "kr-cache:005930.KS:2022:market-gap:"
                    "source_no_rows_before_first_trade"
                ),
                "source_type": "kr_cache_market_gap_diagnostic",
                "method": "KR_CACHE_MARKET_GAP_DIAGNOSTIC",
                "quality_status": "source_no_rows_before_first_trade",
            },
        }
    }


def test_deploy_smoke_script_checks_core_api_and_chart_contracts():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SmokeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(
            ["node", "scripts/deploy-smoke.mjs", "--base-url", base_url, "--ticker", "AAPL"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    summary = json.loads(result.stdout)
    assert summary["status"] == "ok"
    assert {check["name"] for check in summary["checks"]} == {
        "api_health",
        "source_readiness",
        "source_coverage",
        "industry_series",
        "macro_series",
        "security_search",
        "valuation_map_adjusted_forecast",
        "forecast_snapshots",
        "chart_svg",
        "chart_png",
    }
    assert all(check["ok"] for check in summary["checks"])


def test_deploy_smoke_script_can_require_source_backed_consensus_forecast():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SmokeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(
            [
                "node",
                "scripts/deploy-smoke.mjs",
                "--base-url",
                base_url,
                "--ticker",
                "AAPL",
                "--require-consensus-forecast",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    summary = json.loads(result.stdout)
    checks = {check["name"]: check for check in summary["checks"]}
    assert summary["status"] == "ok"
    assert checks["source_coverage"]["ok"] is True
    assert checks["forecast_snapshots"]["ok"] is True
    assert checks["valuation_map_adjusted_forecast"]["ok"] is True


def test_deploy_smoke_script_can_require_kr_top10_production_gate():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SmokeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(
            [
                "node",
                "scripts/deploy-smoke.mjs",
                "--base-url",
                base_url,
                "--ticker",
                "AAPL",
                "--require-consensus-forecast",
                "--require-kr-top10-production-gate",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    summary = json.loads(result.stdout)
    checks = {check["name"]: check for check in summary["checks"]}
    assert summary["status"] == "ok"
    assert checks["kr_top10_valuation_cache"]["ok"] is True
    assert checks["kr_top10_production_source_coverage"]["ok"] is True


def test_deploy_smoke_script_can_require_kr_top10_partial_gap_audit_refs():
    server = ThreadingHTTPServer(("127.0.0.1", 0), PartialAuditSmokeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(
            [
                "node",
                "scripts/deploy-smoke.mjs",
                "--base-url",
                base_url,
                "--ticker",
                "AAPL",
                "--require-kr-top10-partial-audit",
                "--expect-kr-top10-partial-tickers",
                "005930.KS",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    summary = json.loads(result.stdout)
    checks = {check["name"]: check for check in summary["checks"]}
    assert summary["status"] == "ok"
    assert checks["kr_top10_valuation_cache"]["ok"] is True
    assert "kr_top10_production_source_coverage" not in checks


def test_deploy_smoke_script_explains_missing_kr_top10_partial_gap_audit_refs():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MissingPartialAuditRefsSmokeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(
            [
                "node",
                "scripts/deploy-smoke.mjs",
                "--base-url",
                base_url,
                "--ticker",
                "AAPL",
                "--require-kr-top10-partial-audit",
                "--expect-kr-top10-partial-tickers",
                "005930.KS",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    summary = json.loads(result.stdout)
    checks = {check["name"]: check for check in summary["checks"]}
    assert result.returncode == 1
    assert summary["status"] == "failed"
    assert checks["kr_top10_valuation_cache"]["ok"] is False
    assert "kr_partial_audit_failed" in checks["kr_top10_valuation_cache"]["detail"]
    assert "expected=005930.KS" in checks["kr_top10_valuation_cache"]["detail"]
    assert "invalid_or_missing=005930.KS" in checks["kr_top10_valuation_cache"]["detail"]


def test_deploy_smoke_script_explains_unresolved_kr_top10_partial_gap_audit_fact():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MissingPartialAuditFactSmokeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(
            [
                "node",
                "scripts/deploy-smoke.mjs",
                "--base-url",
                base_url,
                "--ticker",
                "AAPL",
                "--require-kr-top10-partial-audit",
                "--expect-kr-top10-partial-tickers",
                "005930.KS",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    summary = json.loads(result.stdout)
    checks = {check["name"]: check for check in summary["checks"]}
    assert result.returncode == 1
    assert summary["status"] == "failed"
    assert checks["kr_top10_valuation_cache"]["ok"] is False
    assert "kr_partial_audit_fact_unresolved" in checks["kr_top10_valuation_cache"]["detail"]
    assert KR_PARTIAL_MARKET_GAP_FACT_ID in checks["kr_top10_valuation_cache"]["detail"]
    assert "http_404" in checks["kr_top10_valuation_cache"]["detail"]
