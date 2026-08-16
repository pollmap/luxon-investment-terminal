import json

from mcp.valuation_server import handle_request


def test_mcp_lists_tools():
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tool_names = {tool["name"] for tool in response["result"]["tools"]}
    assert {"securities_search", "valuation_map", "data_audit", "data_audit_fact"} <= tool_names


def test_mcp_calls_valuation_map_with_source_trace():
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "valuation_map", "arguments": {"ticker": "AAPL", "forecast_years": 1}},
        }
    )
    content = response["result"]["content"][0]["text"]
    payload = json.loads(content)
    assert payload["data"]
    assert payload["data"][0]["source_trace"]


def test_mcp_calls_data_audit_with_trace_sections():
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "data_audit",
                "arguments": {
                    "ticker": "AAPL",
                    "forecast_mode": "custom",
                    "target_multiple": 21,
                },
            },
        }
    )
    content = response["result"]["content"][0]["text"]
    payload = json.loads(content)
    chart_key = next(
        row for row in payload["data"] if row["fact_name"] == "chart_key.custom_multiple"
    )
    assert chart_key["value"] == "21.00"
    assert chart_key["source_trace"]["source_document_id"]
    assert {section["title"] for section in chart_key["trace_sections"]} >= {
        "Source evidence",
        "Calculation",
        "Quality",
    }


def test_mcp_calls_data_audit_fact_with_input_trace_sections():
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "data_audit_fact",
                "arguments": {
                    "fact_id": "AAPL-2029-forecast_scenario.21x.target_price",
                    "forecast_mode": "custom",
                    "target_multiple": 21,
                },
            },
        }
    )
    content = response["result"]["content"][0]["text"]
    payload = json.loads(content)
    fact = payload["data"]
    assert fact["fact_name"] == "forecast_scenario.21x.target_price"
    sections = {section["title"]: section for section in fact["trace_sections"]}
    assert {"Source evidence", "Calculation", "Quality", "Input traces"} <= set(sections)
    assert any(row["key"] == "forecast_metric_trace" for row in sections["Input traces"]["rows"])
