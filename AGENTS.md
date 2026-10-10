# 주식담 Agent Rules

## Mission

Maintain 주식담, a public Korean-stock information service with no login.
Support KOSPI/KOSDAQ common and preferred shares on PC and mobile. Toss is the
only brokerage connection; DART/XBRL, KIND, company IR and verified public
sources complement its market data. Preserve existing links, watchlists,
settings, original evidence and restatements.

Exclude KONEX, ETF/ETN/listed trusts, foreign shares, orders/accounts,
community, proprietary investment scores and dividend reinvestment.
The repository name and legacy terminal code do not expand this scope.
Use the selected brand-3 design and existing common components. FAST Graphs
and other products are workflow references, not sources of generated numbers.
The user's latest request takes precedence over historical project documents.

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
- Use the current market app design tokens and existing components. Do not hardcode one-off
  colors, spacing, radius, or typography when a token/component exists.
- Prefer narrow, verified increments over broad rewrites.
- Run tests after every implementation slice.
- Update `DECISIONS.md` when architecture or data contracts change.
- Current delivery uses VPS API/DB/collectors and Cloudflare static/edge
  delivery. Preserve both existing public addresses and other VPS services.
  Verify current runtime and rollback references before deployment or cleanup.
  No global Docker pruning, raw-data deletion or paid infrastructure change.
- Live ingestion runs in shared collectors, outside browser requests.
- Block or label outputs when source evidence is missing instead of filling gaps
  with synthetic values.
- Prioritize visible stocks and current data without abandoning the scoped
  whole-market history. Record implementation, actual data, browser validation
  and public deployment separately. Subscription approval is not live-tick proof.
- AAPL/US and legacy terminal fixtures are regression-only, not product scope.
- Before reporting data cleanup or recovery complete, verify archive integrity,
  independent restoration and the current data delta. A dated backup does not
  authorize deleting or replacing newer source tables.

## Research Boundary

주식담 presents observed facts and deterministic comparisons. User assumptions,
company forecasts and reported actuals remain distinct. Do not add trading,
account operations or investment scores to complete an unrelated task.
