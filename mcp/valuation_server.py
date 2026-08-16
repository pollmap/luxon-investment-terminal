from __future__ import annotations

import json
import sys
from decimal import Decimal
from typing import Any

from backend.normalize.api import get_adjusted_series, get_adjusted_waterfall
from packages.valuation.exports import audit_row_with_trace_sections, audit_rows_with_trace_sections
from services.api.main import (
    _ticker_from_fact_id,
    company_financials,
    company_snapshot,
    data_audit,
    search_securities,
    valuation_map,
)


SERVER_INFO = {"name": "personal-fastgraphs-mcp", "version": "0.1.0"}


TOOLS = [
    {
        "name": "securities_search",
        "description": "Search the fixture-backed security universe.",
        "inputSchema": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "company_snapshot",
        "description": "Return source-traced company terminal snapshot metrics.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "valuation_map",
        "description": "Return valuation map series for a ticker and metric.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "metric": {"type": "string"},
                "forecast_years": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "adjusted_earnings",
        "description": "Return adjusted earnings series with waterfall source trace.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "financials",
        "description": "Return financial trend rows for Revenue, EPS, FCF, margins, ROE, ROIC, and debt.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "data_audit",
        "description": "Return source-traced data audit rows for a ticker, including structured trace sections.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "metric": {"type": "string"},
                "forecast_mode": {"type": "string"},
                "forecast_case": {"type": "string"},
                "forecast_years": {"type": "integer", "minimum": 1, "maximum": 5},
                "start_year": {"type": "integer"},
                "end_year": {"type": "integer"},
                "normal_multiple_years": {"type": "integer", "minimum": 1},
                "user_growth_rate": {"type": "number"},
                "target_multiple": {"type": "number"},
                "manual_eps_values": {"type": "string"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "data_audit_fact",
        "description": "Return one data audit fact with source evidence, calculation, quality, and input trace sections.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "string"},
                "metric": {"type": "string"},
                "forecast_mode": {"type": "string"},
                "forecast_case": {"type": "string"},
                "forecast_years": {"type": "integer", "minimum": 1, "maximum": 5},
                "start_year": {"type": "integer"},
                "end_year": {"type": "integer"},
                "normal_multiple_years": {"type": "integer", "minimum": 1},
                "user_growth_rate": {"type": "number"},
                "target_multiple": {"type": "number"},
                "manual_eps_values": {"type": "string"},
            },
            "required": ["fact_id"],
        },
    },
]


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        return _result(request_id, _call_tool(params.get("name"), params.get("arguments") or {}))
    return _error(request_id, -32601, f"Unsupported method: {method}")


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "securities_search":
        payload = search_securities(arguments.get("q", ""))
    elif name == "company_snapshot":
        payload = company_snapshot(_ticker(arguments))
    elif name == "valuation_map":
        payload = valuation_map(
            _ticker(arguments),
            metric=arguments.get("metric", "adjusted_operating"),
            forecast_years=int(arguments.get("forecast_years", 5)),
        )
    elif name == "adjusted_earnings":
        payload = get_adjusted_series(_ticker(arguments))
    elif name == "financials":
        payload = company_financials(_ticker(arguments))
    elif name == "data_audit":
        payload = _data_audit_payload(arguments)
    elif name == "data_audit_fact":
        payload = _data_audit_fact_payload(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
            }
        ],
        "isError": False,
    }


def _ticker(arguments: dict[str, Any]) -> str:
    ticker = arguments.get("ticker")
    if not ticker:
        raise ValueError("ticker is required")
    return str(ticker)


def _data_audit_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    payload = data_audit(_ticker(arguments), **_data_audit_options(arguments))
    return {
        **payload,
        "data": audit_rows_with_trace_sections(payload.get("data", [])),
    }


def _data_audit_fact_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    fact_id = arguments.get("fact_id")
    if not fact_id:
        raise ValueError("fact_id is required")
    ticker = _ticker_from_fact_id(str(fact_id))
    payload = data_audit(ticker, **_data_audit_options(arguments))
    for row in payload.get("data", []):
        if row.get("fact_id") == fact_id:
            return {"data": audit_row_with_trace_sections(row)}
    raise ValueError(f"data audit fact not found: {fact_id}")


def _data_audit_options(arguments: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key in [
        "metric",
        "forecast_mode",
        "forecast_case",
        "forecast_years",
        "start_year",
        "end_year",
        "normal_multiple_years",
        "manual_eps_values",
    ]:
        if arguments.get(key) is not None:
            options[key] = arguments[key]
    for key in ["user_growth_rate", "target_multiple"]:
        if arguments.get(key) is not None:
            options[key] = Decimal(str(arguments[key]))
    return options


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = handle_request(json.loads(line.lstrip("\ufeff")))
        except Exception as exc:
            response = _error(None, -32000, str(exc))
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
