# LUXON Decisions

Status: active architecture and product decisions
As of: 2026-08-16

This document records the current public implementation contract. Historical
experiments, private design identifiers, local paths, authenticated reference
captures, and superseded product names are intentionally excluded from the
public snapshot.

## Product boundary

- The product and canonical repository are named **LUXON Investment Terminal**
  and `pollmap/luxon-investment-terminal`.
- LUXON is a personal investment-research workspace, not an automated trading
  or investment-advice system.
- The beta operating target is a single-user, KR-first terminal. US and JP
  provider boundaries remain prepared but are not described as production-ready
  without source-backed evidence.
- Public source code does not imply a public hosted service. Local Windows plus
  Docker Compose is the default beta profile; Vercel, Render, and managed
  Postgres are optional protected profiles.

## Public repository and reference policy

- The public repository is a sanitized current snapshot. Full historical work
  remains in a separate private archive.
- FAST Graphs-style historical valuation workflow is the primary interaction
  reference. FnGuide-style Snapshot, Financials, Consensus, and Peers structure
  is the secondary information-architecture reference.
- Reference work is limited to manual review of public documentation and public
  pages. Authenticated automation, scraping, access-control bypass, protected
  DOM extraction, and repository storage of third-party screenshots are
  prohibited.
- LUXON uses its own brand, copy, code, components, tokens, data model,
  deterministic formulas, and source-trace model. It may reproduce analytical
  tasks and domain-standard chart grammar, not third-party trade dress or
  proprietary assets.
- The active frontend contract is
  `docs/CLAUDE_DESIGN_HANDOFF.md`. Its blocking validation companion is
  `docs/CLAUDE_DESIGN_QA_CHECKLIST.md`. Former Figma work is private archival
  input, not a public source of truth.
- The repository intentionally grants no open-source license. Public visibility
  is for source inspection and portfolio review; reuse requires separate
  permission.

## Financial-data integrity

- LLMs do not create, rank, repair, or silently fill financial numbers.
- Production values come from first-party filings, verified APIs,
  operator-supplied validated files, user-entered assumptions, or deterministic
  formulas.
- Raw payloads are append-only. Restatements are versioned rather than
  overwritten.
- Every stored value requires `source_trace`; point-in-time values require
  `source_trace.available_at`; derived values require `formula` and
  `input_fact_ids`.
- A missing value is not zero. Missing credentials, source contracts, upstream
  failures, stale values, and rate limits remain distinct statuses.
- Fixtures stay labeled `fixture_non_production` and cannot become research,
  training, production, or operational evidence.
- Production fixture fallback is fail-closed unless the operator explicitly
  enables it for a non-production environment.

## KR-first data plan

- The first production slice is the configured KR priority universe, with
  source-backed coverage from OpenDART and validated market-data inputs.
- Priority-universe order is not displayed as a live market-cap rank until
  source-backed price, shares, or market-cap rows are recomputed for the same
  as-of policy.
- The KR valuation-map may use a DB-free source-backed cache only when every row
  passes provenance and non-fixture validation. Otherwise it returns a
  source-required state.
- Consensus and peer data are fail-closed. FnGuide/DataGuide endpoints,
  credentials, schemas, and values are never guessed. Licensed operator exports
  must pass an explicit import contract before persistence.
- Consensus snapshots are point-in-time records. Analyst scorecards may compare
  estimates with actuals only where valid historical snapshots overlap.

## API and frontend contract

- Research responses use a shared envelope with `available`, `status`,
  `data_mode`, nullable `data`, and `source_trace`.
- Supported states include `ready`, `partial`, `stale`, `configured`,
  `fixture_non_production`, `missing_source`, `missing_contract`, `missing_key`,
  `rate_limited`, and `upstream_error`.
- A frontend may render financial values only when the envelope and trace state
  permit it. Unavailable states must not expose stale fixture numbers behind a
  positive badge.
- Historical Graph is the primary decision surface. Every displayed financial
  number, chart point, valuation line, and table cell must lead to Fact Audit.
- Company routes preserve ticker and workspace deep links. Redirect-only route
  wrappers are transitional and are a blocking item for the Claude Design pass.
- Workspace requests must have independent loading and error boundaries; one
  endpoint failure must not force an unrelated screen into a global fallback.

## Frontend information architecture

- The core loop is search -> Historical valuation -> Forecast scenario -> Fact
  Audit.
- Snapshot is a dense FnGuide-style company overview. The Ask/underwriting hub
  is a separate task surface, not a replacement for Snapshot.
- Historical-only metric, period, and chart settings do not persist across
  unrelated workspaces.
- Navigation must keep the active item stable. Lower-frequency research tools
  use an explicit overflow model instead of moving the active tab between rows.
- DOM order and visual order must match for major regions. CSS `order` is not a
  substitute for correct semantic structure.
- The canonical viewports are 1440x900, 1024x768, and 390x844. Page-level
  horizontal overflow is a release blocker.

## Operations and deployment

- Local ingestion runs outside web request paths through the operator CLI.
- Public CI and manual operator workflows contain no secret values. They use
  commit-SHA-pinned actions, read-only repository permission, disabled checkout
  credential persistence, and environment-mediated workflow inputs.
- Manual ingestion workflows reference GitHub secret names only. Dispatch
  remains limited to repository writers, while local operator CLI commands stay
  the default beta path.
- API credentials are injected through environment variables or a secret
  manager. Values are never committed, logged, echoed, or embedded in docs.
- Deployment readiness, provider authorization, live ingestion, and an actual
  end-to-end source-backed response are separate gates.
- Docker Compose model validation is not proof of an image build; an image build
  is not proof of a live provider-backed research flow.

## Current priority

1. Complete the source-backed KR priority-universe ingestion and audit loop.
2. Run the Claude Design handoff and close every P0/P1 item in the companion QA
   checklist.
3. Verify local Docker images and a source-backed KR E2E with operator-provided
   credentials.
4. Add optional protected cloud deployment only after the local beta gates are
   green.
