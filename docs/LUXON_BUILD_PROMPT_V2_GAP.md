# LUXON Build Prompt v2 Gap Map

This document maps the reference-enhanced v2 prompt into the current repository
state. It is a delivery checklist, not a source of financial data.

## Already Implemented

- Canonical `SourceTrace` contract in `packages/core/source_trace.py`.
- Point-in-time `available_at` field and storage gate.
- Storage-ready traces require `source_document_id`; ingestion repository paths
  backfill a logical source document id when a database source document FK is not
  available.
- AAPL P0 fixture contract labeled `fixture_non_production`.
- Vercel-first API and ingestion-worker split.
- SEC/OpenDART/EDINET/J-Quants/FRED connector surfaces.
- Historical Valuation Map, Forecasting, Performance, Financials, Screener,
  Portfolio, Watchlist, Data Audit, Research Report, Fun Graphs, Fiscal Fitness,
  Health Check, and Analyst Scorecard surfaces.
- Figma-first v3 blueprint frames and v4 implementation handoff frame.

## Added In This Increment

- Root `AGENTS.md` with build rules, data authenticity floor, and source trace
  contract.
- Root `SOUL.md` with product philosophy and research boundary.
- `packages/cli` P0 operator facade with `nexus ingest|backfill|value|screen|audit|score`
  dry-run commands.
- Quality validation now treats `available_at` as required provenance.
- Quality validation now imports the canonical `SourceTrace` contract, accepts
  legacy aliases such as `source_type`, and requires durable trace fields such
  as `source`, `source_document_id`, `method`, formula, and point-in-time
  availability before a valuation row can pass.
- Consensus CSV import pins each snapshot to `available_at=snapshot_date
  00:00:00Z`, and API forecast traces backfill `available_at` plus `method` for
  older rows.
- Derived surfaces now stamp their own trace method on audit rows, including
  Forecast, Fun Graphs, Fiscal Fitness, Health Check, Analyst Scorecard, Use of
  Cash, and Research Report outputs.
- Figma v4 implementation handoff frame documents the next web component split.

## v2 Backlog

| Area | v2 requirement | Current status | Next implementation |
| --- | --- | --- | --- |
| Valuation models | P/E, DCF 1-stage, DCF 2-stage, DDM, ROE capitalization, Residual Income, Graham Number | Partial | Add pure functions with model applicability guards and golden tests |
| Reverse DCF | Market-implied growth, margin, discount assumptions | Pending | Add deterministic reverse solver with bounded assumptions |
| CAPM | Risk-free rate, beta, ERP, terminal growth defaults and overrides | Pending | Use FRED and source-backed beta input; expose sensitivity grid |
| Scorecard | Past, Present, Future, Health, Dividend, Macro axes | Partial | Extend Health Check into six-axis scorecard with Data Audit rows |
| Piotroski | 9-point financial health score | Pending | Add quality module and source-traced score rows |
| World Bank | Country macro connector | Pending | Add connector contract after official API schema review |
| MCP audit log | JSONL scratchpad per query and loop guards | Partial | Extend local MCP server with tool-call audit events |
| CLI live wiring | `packages/cli` dispatch to ingestion/API workflows | Scaffolded | Keep live execution explicit and source-specific |

## Implementation Rule

New valuation or score models must return:

- value
- formula
- input fact ids
- method applicability
- confidence
- quality flags
- source trace

If any required source input is missing, the result must be unavailable or
flagged. It must not be filled by an LLM or placeholder value.
