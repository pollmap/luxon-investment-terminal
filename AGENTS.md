# LUXON Agent Rules

## Mission

Build LUXON Investment Terminal as a product-grade, source-traced personal
investment research workspace. The initial operating target is a private,
single-user Korean-equity beta with prepared US-provider boundaries. The product may use
FAST Graph-style analytical grammar as a
workflow reference, but it uses its own code, UI, brand, data model, and
calculation engine.

## Non-Negotiables

- LLMs never create financial numbers.
- Production values come only from first-party filings, verified APIs, CSVs
  supplied by the operator, or deterministic formulas.
- Raw source payloads are append-only.
- Restatements are versioned, not overwritten.
- Stored values require `source_trace`.
- Point-in-time calculations require `source_trace.available_at`.
- Derived values require `formula` and `input_fact_ids`.
- Fixtures must be labeled `fixture_non_production` and must not be promoted to
  research, production, training, or operational evidence.
- Secrets are never committed, logged, echoed, or copied into docs. Use env var
  names and secret manager references only.

## Source Trace Contract

Minimum storage-ready fields:

- `source`
- `filing_id`
- `period`
- `available_at`
- `unit`
- `currency`
- `method`
- `formula`

Recommended audit fields:

- `source_document_id`
- `source_type`
- `form`
- `source_url`
- `filing_url`
- `input_fact_ids`
- `adjustments`
- `confidence`
- `quality_flags`
- `quality_status`
- `version`

## Build Rules

- Reference-first is mandatory for production UI. Before material UI work,
  capture and inspect the matching FAST Graph-style public reference and the
  existing implementation, then document route, user goal, data dependencies,
  interactions, empty/loading/error states, source_trace click targets,
  keyboard shortcuts, and acceptance criteria. Existing Figma work is archival
  input, not the active source of truth; Claude-compatible code handoff is the
  preferred design workflow.
- Reference research is limited to manual observation of public documentation
  and public pages. Do not automate authenticated commercial surfaces, bypass
  access controls, scrape protected DOM/content, or commit third-party captures.
- Use LUXON design tokens and existing components. Do not hardcode one-off
  colors, spacing, radius, or typography when a token/component exists.
- Prefer narrow, verified increments over broad rewrites.
- Run tests after every implementation slice.
- Update `DECISIONS.md` when architecture or data contracts change.
- Local Windows + Docker Compose operation is the beta default. Vercel, Render,
  and Neon remain optional protected cloud profiles and are not beta gates.
- Live ingestion runs outside Vercel request paths through CLI or GitHub Actions.
- Block or label outputs when source evidence is missing instead of filling gaps
  with synthetic values.
- Current production bootstrap priority is KR top-market-cap coverage first.
  AAPL/US fixture paths remain regression coverage unless explicitly promoted
  with source-backed data.

## Research Boundary

LUXON is research infrastructure, not an automated trading or investment advice
system. Forecasting, scorecards, and investor-lens commentary explain
assumptions and risks; they do not issue trade instructions.
