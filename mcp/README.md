# Local MCP Server

This directory contains a minimal local MCP-compatible JSON-RPC server for the valuation terminal.

Run:

```powershell
python -m mcp.valuation_server
```

Supported tools:

- `securities_search`
- `company_snapshot`
- `valuation_map`
- `adjusted_earnings`
- `financials`
- `data_audit`
- `data_audit_fact`

The server exposes the same fixture-backed, source-traced API data used by FastAPI. It does not generate financial numbers.

`data_audit` returns fact rows with structured `trace_sections` so local agents can inspect source evidence, calculation logic, quality flags, and input traces without reconstructing UI-specific detail payloads. It accepts the same valuation/forecast context as the web audit surface, including `metric`, `forecast_mode`, `forecast_case`, `forecast_years`, `normal_multiple_years`, `user_growth_rate`, `target_multiple`, and `manual_eps_values`.

`data_audit_fact` returns a single fact by `fact_id` with the same structured trace sections. Use it when an agent needs to verify one chart/table number before writing analysis.
