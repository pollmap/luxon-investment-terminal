# LUXON Claude Design QA and UX Contract

Status: required companion to `docs/CLAUDE_DESIGN_HANDOFF.md`
Audience: Claude Designer, frontend implementer, reviewer, and QA owner
Scope: `apps/web/**` only; backend contracts and financial calculations remain outside frontend ownership

## 1. Purpose

This document closes the UX requirements that are easy to lose when turning the
current LUXON shell into a FAST Graphs-familiar, FnGuide-dense investment
terminal. It is both:

1. an evidence-based audit of the current implementation; and
2. a blocking checklist for the next Claude Design implementation pass.

The goal is not a visual copy of FAST Graphs or FnGuide. Preserve the analytical
grammar—historical valuation first, dense company facts, rapid tab movement,
clear actual-versus-estimate boundaries, and evidence behind every number—while
using LUXON code, branding, assets, copy, tokens, components, formulas, and data
contracts.

## 2. Evidence used for this audit

Audit date: 2026-08-16
Local route: `http://127.0.0.1:3100/terminal?ticker=AAPL&tab=Historical`
Desktop viewport: 1440 x 900
Mobile viewport: 390 x 844

Accepted screenshots from the current run are stored outside the repository in
the operator's temporary directory:

- `<TEMP>/luxon-design-audit-2026-08-16/01-fastgraphs-reference.png`
- `<TEMP>/luxon-design-audit-2026-08-16/02-luxon-historical-default.png`
- `<TEMP>/luxon-design-audit-2026-08-16/03-luxon-metric-menu.png`
- `<TEMP>/luxon-design-audit-2026-08-16/04-luxon-period-4y.png`
- `<TEMP>/luxon-design-audit-2026-08-16/05-luxon-chart-settings.png`
- `<TEMP>/luxon-design-audit-2026-08-16/06-luxon-data-audit.png`
- `<TEMP>/luxon-design-audit-2026-08-16/07-luxon-consensus-missing-source.png`
- `<TEMP>/luxon-fastgraphs-comparison-current.png`

These temporary files are evidence from this audit run, not repository assets
or required inputs in another environment. If absent, recapture the public
reference and current implementation at the stated viewport. Do not treat a
missing prior temp file by itself as a blocker.

Code and contract evidence reviewed:

- `apps/web/app/page.tsx`
- `apps/web/app/styles.css`
- `apps/web/components/historical-controls-panel.tsx`
- `apps/web/components/historical-map-panel.tsx`
- `apps/web/components/data-audit-panel.tsx`
- `apps/web/components/research-contract-panels.tsx`
- `apps/web/components/search-overlay.tsx`
- `apps/web/lib/terminal-types.ts`
- `apps/web/lib/terminal-config.ts`
- route wrappers under `apps/web/app/company`, `terminal`, `screener`,
  `portfolio`, and `system`
- `docs/CLAUDE_DESIGN_HANDOFF.md`
- `docs/FAST_GRAPHS_PARITY_MATRIX.md`
- FAST Graphs Historical Graph public guide:
  <https://docs.fastgraphs.com/en/articles/9419962-historical-graph>
- FAST Graphs Forecasting public guide:
  <https://docs.fastgraphs.com/en/articles/13577168-forecasting-charts-user-guide>
- FnGuide Company Guide public help:
  <https://wcomp.fnguide.com/Help/Guide?cmp_cd=0101N0>

Evidence limits:

- This run used the labeled AAPL `fixture_non_production` path for visual and
  interaction inspection. It does not prove live KR data readiness.
- Loading and every upstream failure state could not all be forced from the
  visible app. Their requirements below come from the checked-in API contract
  and code paths, not a visual claim.
- Screenshots can identify likely accessibility risks but cannot prove WCAG
  conformance. Screen reader, zoom, forced-colors, contrast, and complete
  keyboard tests remain required.
- Browser console inspection showed no warnings or errors in the audited flow.

## 3. Current-flow audit

### Step 1 — Historical desktop: usable foundation, high-priority hierarchy work

Health: **needs targeted redesign**

Confirmed strengths:

- The chart is a real decision surface rather than a decorative dashboard tile.
- Metric, period, source readout, valuation layers, high/low strip, evidence
  rail, performance range selection, and chart exports exist.
- Chart year buttons expose Arrow Left/Right, Home, End, and Shift+Enter
  keyboard handling.
- The evidence rail visibly separates method, confidence, quality, formula, and
  source document context.
- A failed source contract is generally blocked instead of converted to zero.

Blocking issues:

- The screen still contains three competing navigation systems: left rail,
  top workflow navigation, and two rows of company tabs.
- Tab location changes when a secondary tab becomes active. A tab that was in
  the second row can move into the first row, so spatial memory is unstable.
- The Historical screen hides its source gate visually, while fixture values
  still show a green `source-backed` chip. `fixture_non_production` and
  `source-backed` must never appear as simultaneous status claims.
- The broad Ask/Underwriter hub is visually moved below the chart using CSS
  `order`, but remains before the chart in DOM and screen-reader order.
- The chart and rail are information-rich, but the opening hierarchy includes
  repeated status, readout, KPI, and audit sections. The most important
  comparison—price versus fundamentals and valuation—should require less
  vertical scanning.
- Range controls duplicate period buttons, a period dropdown, and a Choose
  Dates control without a defined desktop-versus-mobile priority.
- Chart settings is available in the company header, period row, and chart
  toolbar. Define one primary trigger and one contextual secondary trigger at
  most.

### Step 2 — Summary/Snapshot desktop: wrong primary user goal

Health: **structural mismatch**

The route contract says Snapshot should be a dense company overview. The
current Summary screen is primarily an Ask/forecast workflow, seed-universe
launcher, workspace map, and visualization coverage view. That may remain as a
separate Home or Underwrite surface, but it is not the FnGuide-style Snapshot.

Blocking issues:

- `/company/[id]/snapshot` redirects to `tab=Summary`, but Summary does not
  provide a dense company fact hierarchy.
- Core company facts, valuation, profitability, growth, ownership, business
  mix, estimate revision context, and source freshness are not the dominant
  above-the-fold content.
- The source-gate body references OpenDART, pykrx, and marcap even on a US
  security. Market-specific remediation copy is required.
- An Ask input is shown before the investor can establish the basic company
  context. AI assistance must be secondary to source-backed facts.

### Step 3 — Financials desktop: content exists but starts below irrelevant UI

Health: **content foundation present, route shell wrong**

Confirmed strengths:

- The current component anticipates annual/quarterly/TTM, reported versus
  reconstructed, per-share/common-size, and cell-audit interactions.
- It states that unsupported modes must stay source-gated.

Blocking issues:

- Historical metric, forecast, period, and chart-settings controls remain above
  Financials even though they do not serve the Financials task.
- The actual statement workbench begins too far below the company header.
- The page needs a stable Income Statement / Balance Sheet / Cash Flow / Ratios
  sub-navigation and dense comparison table behavior.
- Unit, currency, fiscal calendar, actual/estimate, restatement, and source
  freshness must be visible at table scope, not repeated vaguely per page.

### Step 4 — Consensus desktop: safe empty state, insufficient recovery

Health: **safe but incomplete**

Confirmed strengths:

- The new contract validator rejects malformed, untraced, or fixture payloads.
- Manual assumptions are explicitly separated from analyst consensus.
- `missing_source` does not render a fabricated estimate table.

Blocking issues:

- All unavailable states collapse into one generic empty treatment. The screen
  does not teach the operator whether to add a contract, set an environment
  variable, wait for a rate limit, retry an upstream call, or import a validated
  CSV.
- No retry action, last-success time, retry-after time, accepted CSV schema
  link, or provider-settings route is exposed.
- The chart-only control band remains above Consensus.
- The ready state is only a flat case table. FnGuide-style consensus requires
  period alignment, analyst count, revision windows, estimate range, and
  actual-versus-estimate boundaries.
- The status badge itself is not a live region. Only the generic empty block has
  `role=status`.

### Step 5 — Historical mobile: navigable but not yet usable as a mobile product

Health: **major responsive work required**

Measured current state:

- The page itself does not horizontally overflow at 390 px.
- The primary tab strip is 701 px inside a 325 px scroll container.
- The secondary tab strip is 739 px inside a 325 px scroll container.
- The chart canvas is deliberately 760 px wide inside its pan container.
- Fixed evidence and bottom navigation bars occupy the bottom 136 px of the
  viewport.

Blocking issues:

- Two horizontally scrolling tab rows create hidden destinations and weak
  discoverability.
- The fixed evidence summary and bottom navigation cover too much of the core
  chart area while scrolling.
- Historical controls stack into a tall sequence before the chart, delaying the
  primary task.
- The broad Ask/Underwriter content is visually displaced but remains in the
  accessibility tree before the chart.
- The mobile UI still exposes desktop information architecture rather than the
  promised Snapshot, Watchlist, simplified chart, and Fact Audit flow.
- The chart can pan horizontally, but there is no visible cue explaining the
  current range, selected year, or that more chart exists off-screen.

## 4. Blocking product decisions for Claude

Claude must follow these decisions without reopening them:

- [ ] Historical valuation is the default company route and dominant desktop
  workspace.
- [ ] The Ask/Underwrite hub is a separate route or clearly secondary Home
  workspace. It is not the company Snapshot.
- [ ] Snapshot, Financials, Consensus, and Peers use FnGuide-like information
  density and hierarchy, not Historical chart controls.
- [ ] Navigation order is static. Active tabs never move between rows.
- [ ] Every route has its own data boundary, loading state, and error recovery.
  One failed endpoint must not blank unrelated routes.
- [ ] Fixture mode is visibly and persistently different from source-backed
  mode. A fixture can support software tests, never investment evidence.
- [ ] Every visible financial value is either source-traced, explicitly a user
  assumption, or absent with a reason.
- [ ] Mobile is a reduced workflow, not a squeezed desktop terminal.
- [ ] Claude may reorganize frontend code but must not calculate valuation,
  CAGR, target price, dividend return, quality score, or peer rank.
- [ ] Claude must not call external market-data providers from the browser.

## 5. Target information architecture

### 5.1 Global shell

Desktop shell:

1. compact LUXON wordmark and environment status;
2. global security search;
3. stable primary product navigation: Terminal, Screener, Portfolio, System;
4. compact company identity and quote strip on company routes;
5. stable company tabs: Graph, Snapshot, Financials, Forecast, Consensus,
   Peers, Performance, More;
6. route-specific content immediately after the company tabs.

Do not show chart controls outside Graph or a chart-containing Forecast view.
Do not repeat the same status in the top bar, company header, KPI strip, control
band, and content header.

Mobile shell:

1. compact wordmark and search;
2. company identity plus freshness/quality state;
3. bottom navigation for Snapshot, Graph, Watchlist, More, Audit;
4. More opens a sheet for Financials, Forecast, Consensus, Peers, Performance,
   Screener, Portfolio, and System;
5. one collapsible evidence summary, never two simultaneous fixed overlays.

### 5.2 Route behavior

- [ ] Use real route shells for `/company/[id]/[view]`, not only redirects to a
  monolithic query-state page.
- [ ] Query parameters preserve metric, period, forecast case, peer kind,
  statement mode, and selected fact where sharing/replay matters.
- [ ] User navigation creates browser history. Use replacement only for initial
  default normalization; Back and Forward must replay prior ticker/tab state.
- [ ] Route title and heading identify the company and active workspace.
- [ ] A route refresh restores the same meaningful state without relying on
  prior in-memory state.
- [ ] Unknown ticker or view fails closed with search recovery and no stale
  previous-security data.
- [ ] Changing ticker shows an updating state attached to the new ticker. Old
  values must not appear under the new company header.
- [ ] Each route owns focused data queries. Do not run a 20-plus endpoint fanout
  before the user can view one route.
- [ ] Independent panels may load independently; a failed optional panel cannot
  fail the company snapshot or historical chart.

## 6. Global data-state contract

Frontend state has three separate dimensions. Never merge them into one vague
`status` string:

1. request phase: idle, loading, refreshing, loaded, request_error;
2. data state: ready, partial, stale, configured, fixture_non_production,
   missing_source, missing_contract, missing_key, rate_limited, upstream_error;
3. content cardinality: populated or empty for the active filters.

### 6.1 Required state behavior

| State | Display rule | Recovery | Accessibility |
| --- | --- | --- | --- |
| `loading` | Preserve final layout with labeled skeletons; display no placeholder numbers | Wait/cancel only where useful | `aria-busy=true`; one polite loading announcement |
| `refreshing` | Keep last valid values, add updating timestamp/status | Allow continued reading | Announce start and completion once |
| `ready` | Render source-backed values and current timestamp | Normal interaction | Status text available to assistive technology |
| `empty` | Explain that the query succeeded but found no records for the selected filters | Clear/reset filters or change period | Focusable recovery action; not an error alert |
| `partial` | Render valid subset; show exact missing periods, metrics, or peers | Open coverage details or System | Warning announced once; gaps exposed as text |
| `stale` | Render last-known source-backed values with as-of time and age | Refresh or accept stale view | Never use green/live-only styling |
| `configured` | Show configuration recognized, live reachability not verified | Run connection check | Say “configured, not verified” explicitly |
| `fixture_non_production` | Persistent non-dismissable test banner; values visibly watermarked | Switch to source-backed data | Never pair with “source-backed” or “live” badge |
| `missing_source` | Render no financial value; name missing source class and periods | Run/import the correct ingestion path | Error summary plus link to exact remediation |
| `missing_contract` | Render no licensed consensus/peer value | Add contract or use a permitted manual/CSV lane | Do not suggest scraping or imply a key is enough |
| `missing_key` | Render no provider value; show environment variable name only | Open System setup instructions | Never expose or echo the secret value |
| `rate_limited` | Keep last valid cache if present and label it; otherwise show unavailable | Retry after server-provided time | Announce retry availability; avoid countdown spam |
| `upstream_error` | Keep last valid cache if contract permits; otherwise block values | Retry and show incident/request ID if supplied | `role=alert` only for the actionable failure |
| `request_error` | Distinguish timeout, offline, auth, and malformed contract | Retry, sign in, or inspect System | Move focus to error summary only after user action |
| unknown status | Fail closed as contract mismatch | Open System/contract diagnostics | Never optimistically render data |

### 6.2 State invariants

- [ ] `null`, missing, `NaN`, empty string, and zero remain distinct.
- [ ] A dash means unavailable only when the adjacent label or tooltip explains
  why. Do not use `0` or `0.0%` for missing data.
- [ ] Partial data lists exact gaps: for example `FY2021–FY2022 price missing`,
  not only `partial`.
- [ ] Stale status includes `as_of`, `available_at`, latest successful sync, and
  timezone.
- [ ] Every recovery action is market/provider specific.
- [ ] KR remediation references OpenDART/KRX/pykrx/marcap only where applicable.
- [ ] US remediation references SEC/provider inputs only where applicable.
- [ ] JP remediation references its approved filings/market provider boundary.
- [ ] Last-good data and current error are allowed together only when the UI
  labels the values stale/cached.
- [ ] Fixture screens cannot export a production report without an explicit
  fixture watermark in the artifact.
- [ ] Status changes do not cause the whole page to jump or reorder.

## 7. Page-by-page UX contract

### 7.1 `/terminal` — search and resume

User goal: start or resume research in under ten seconds.

Required data:

- security search index;
- recent companies and recent route state;
- watchlist summary;
- source/provider health summary;
- optional saved chart layouts and saved screens.

Required UI:

- [ ] Search is the primary action and accepts ticker or company name.
- [ ] Results show company, ticker, market, currency, and coverage state.
- [ ] Keyboard: `/` and Ctrl/Cmd+K open search when focus is not in an input;
  Arrow keys move; Enter opens; Escape closes and restores focus.
- [ ] Recent research restores the route and meaningful query state.
- [ ] Watchlist and recent items never display untraced market values.
- [ ] Provider health is a compact warning, not a full operations dashboard.
- [ ] Empty first-use state explains how to search and where fixture data is
  permitted.
- [ ] Search failure preserves typed query and offers retry.

Current gap: `/terminal` currently renders the entire company workspace rather
than a focused search/resume entry.

### 7.2 `/company/[id]/graph` — Historical valuation map

User goal: decide how price relates to business fundamentals and valuation over
time, then verify the selected evidence.

Required data:

- reported valuation rows;
- source-backed price series and annual high/low;
- dividends;
- fair-value and normal-multiple outputs from backend formulas;
- point-in-time forecasts with evidence lane;
- recession/event bands;
- optional portfolio transactions;
- source trace for every displayed point and derived line.

Required UI and behavior: see the full Historical contract in section 8.

### 7.3 `/company/[id]/snapshot` — FnGuide-style company overview

User goal: understand what the company is, how it is valued, how it is
performing, and what data can be trusted before opening a deeper tab.

Required data:

- identity, quote, market cap, shares, market/currency;
- business summary, sector/industry, fiscal year-end;
- valuation ratios with period/basis;
- growth, profitability, leverage, dividend facts;
- ownership/business mix when source-backed;
- latest annual/quarterly actuals;
- consensus summary only when a licensed snapshot exists;
- source freshness and quality for each module.

Required layout:

- [ ] Compact company/quote strip.
- [ ] Dense key-indicator grid grouped by Valuation, Growth, Profitability,
  Financial Stability, Dividend, and Ownership.
- [ ] Three-to-five-year annual trend table and recent-quarter strip.
- [ ] Business/segment mix with source period and no decorative chart when the
  relationship is clearer as a table.
- [ ] Consensus mini-panel is optional and cannot block Snapshot.
- [ ] Each module has independent loading/error/partial state.
- [ ] Each value or cell opens Fact Audit.
- [ ] Actuals and estimates use text/symbol distinction in addition to color.
- [ ] Ask/AI assistance is a secondary action, not the page hero.

### 7.4 `/company/[id]/financials` — statements and ratios

User goal: compare operating and financial trends across periods without losing
unit, basis, or provenance.

Required data:

- income statement, balance sheet, cash flow, ratios;
- annual, quarterly, and TTM period dimensions;
- reported/as-filed facts and approved normalized/derived values;
- restatement versions and `available_at`;
- unit, currency, scale, consolidation basis, actual/estimate basis;
- source trace and formula lineage for every cell.

Required UI:

- [ ] Stable sub-tabs: Income Statement, Balance Sheet, Cash Flow, Ratios.
- [ ] Toggles: Annual / Quarterly / TTM; Absolute / Per Share / Common Size;
  Reported / Normalized where supported.
- [ ] Unsupported modes are disabled with a reason; switching cannot fabricate
  values.
- [ ] Unit, currency, scale, and fiscal basis remain sticky above the table.
- [ ] First column and period header stay visible during table scrolling.
- [ ] Actual and estimate columns are visually and textually distinct.
- [ ] Restated rows expose version and prior filing without overwriting history.
- [ ] Clicking a cell opens Fact Audit without leaving the table context.
- [ ] Table loading skeleton preserves row and column geometry.
- [ ] Empty statement family does not blank other statement families.
- [ ] Historical chart controls never appear on this route.

### 7.5 `/company/[id]/forecast` — deterministic scenario workbench

User goal: compare source-backed consensus with explicit personal assumptions
and understand how each assumption changes forward returns.

Required data:

- reported base metric;
- external consensus snapshots when licensed;
- analyst count and point-in-time revision data;
- user assumptions stored separately;
- backend-calculated projected metric, target price, dividends, price return,
  total return, and CAGR;
- formula and input fact IDs for all derived outputs.

Required UI:

- [ ] Evidence lanes are explicit: Consensus, User Input, Deterministic Output,
  AI Commentary.
- [ ] Low/Base/High cases remain aligned across all forecast years.
- [ ] Missing consensus does not disable user-input or historical-CAGR modes.
- [ ] Inputs expose units, valid ranges, and validation near the field.
- [ ] Manual EPS is per year; blank means no assumption, not zero.
- [ ] Unsaved edits, reset-to-source, and copied-case behavior are explicit.
- [ ] Recalculation shows a local pending state and does not blank the chart.
- [ ] Output cards disclose formula, input fact IDs, and selected case.
- [ ] AI may explain risks but cannot populate any financial input.
- [ ] Scenario chart selection and source audit use the same keyboard model as
  Historical.

### 7.6 `/company/[id]/consensus` — estimates and revisions

User goal: assess estimate range, revisions, coverage, and freshness without
mistaking manual assumptions for analyst data.

Required data:

- metric and forecast period;
- low, median/mean, high estimates;
- analyst count;
- current, 1M, 3M, 6M, and 12M snapshots when available;
- revision direction and magnitude;
- provider, as-of time, available-at time, quality, and source trace.

Required UI:

- [ ] Annual/Quarterly selector only when both datasets exist.
- [ ] Estimate table includes period, low, median/mean, high, analyst count,
  revision, actual/estimate marker, and freshness.
- [ ] Revision chart has an accessible table alternative.
- [ ] Missing historical snapshots say `collection not started` or name the
  first available date; never backfill fake history.
- [ ] `missing_contract` links to provider-contract setup and permitted manual
  or validated-CSV alternatives.
- [ ] `missing_key` names only the required environment variable.
- [ ] `rate_limited` shows retry-after and preserves any last-good snapshot.
- [ ] `upstream_error` provides retry and System diagnostics.
- [ ] Ready rows open their individual source trace, not one JSON block for the
  entire table.
- [ ] Historical chart controls never appear on this route.

### 7.7 `/company/[id]/peers` — business and valuation comparables

User goal: understand why each peer is included and compare normalized metrics
on aligned periods.

Required data:

- separate approved business-peer and valuation-peer sets;
- inclusion reason and provenance;
- comparison period, fiscal alignment, unit, currency, and normalization rule;
- company and peer facts with source traces;
- peer median/percentile only if calculated by the backend.

Required UI:

- [ ] Business Peers / Valuation Peers segmented control preserves separate
  state and URL query.
- [ ] Current company is pinned and clearly distinguished.
- [ ] Each row includes relationship/inclusion reason.
- [ ] Columns are sortable with `aria-sort`; sort never changes peer membership.
- [ ] Period and currency mismatch warnings appear at cell or column scope.
- [ ] Missing comparable fact stays blank with reason and does not remove the
  peer row.
- [ ] Partial peer set states exact missing companies or metrics.
- [ ] On mobile, compare a selected metric across peer cards or a controlled
  horizontal table; do not squeeze the desktop table.
- [ ] Every peer membership decision and fact opens source evidence.

### 7.8 `/screener` — source-backed discovery

User goal: find securities that pass explicit, auditable conditions.

Required data:

- filter schema and supported operators;
- source-backed result facts;
- coverage denominator and excluded/missing counts;
- saved-screen state.

Required UI:

- [ ] Separate General, Historical, Estimated, and Company-Relative filter
  groups.
- [ ] Unsupported estimated filters remain unavailable without consensus.
- [ ] Each filter chip shows metric, operator, value, unit, period, and basis.
- [ ] Results distinguish failed, missing-data excluded, and source-rejected.
- [ ] Coverage summary states `N of M securities evaluated`.
- [ ] Sortable columns use `aria-sort` and keep the selected row stable.
- [ ] Clicking a number opens Fact Audit; clicking the company opens Snapshot.
- [ ] Empty result state offers Clear filters without deleting a saved screen.
- [ ] Partial source coverage cannot be described as a full-universe result.

### 7.9 `/portfolio` — user records plus market evidence

User goal: connect personal transactions to valuation history and return
measurement while preserving the boundary between user input and sourced data.

Required data:

- imported transactions and import trace;
- holdings and cost basis;
- market prices, dividends, FX, and valuation series;
- XIRR/return outputs from backend calculations;
- source/user-input trace for every field.

Required UI:

- [ ] Empty first-use state offers CSV template and explains supported columns.
- [ ] Import is preview -> validate -> confirm; never mutate holdings on file
  selection alone.
- [ ] Validation reports row, column, invalid value, and repair guidance.
- [ ] User-entered price is never relabeled as a market price.
- [ ] Stale FX and stale quote warnings appear at holding and portfolio scope.
- [ ] Transactions can open their user-input trace; market facts open source
  trace.
- [ ] Buy/sell chart markers exist only for imported records.
- [ ] Currency conversion basis and as-of time are visible.
- [ ] Mobile favors holdings summary and recent transactions over dense charts.

### 7.10 `/system` — local operations and provider health

User goal: understand why data is missing and take the correct operational
action without exposing secrets.

Required data:

- provider contract/configuration state;
- connection verification and last successful call;
- ingestion/sync runs and freshness;
- market/universe coverage;
- local backup status;
- safe environment variable names;
- retry-after, incident, or request ID where available.

Required UI:

- [ ] Provider rows separate Contract, Configured, Reachable, Last success,
  Coverage, and Required settings.
- [ ] `configured` never means live or source-backed.
- [ ] Secret values are never displayed, copied into support text, or returned
  by the client.
- [ ] Connection test is an explicit user action with progress and result.
- [ ] Ingestion failures link to safe local commands or runbooks.
- [ ] Coverage is summarized by market and security, with exact gaps.
- [ ] Backup status includes last backup time and destination class, not secret
  paths or credentials.
- [ ] System errors do not leak stack traces in the product UI.

### 7.11 Secondary current tabs

Performance, Analyst Scorecard, Fun Graphs, Fiscal Fitness, Health Check,
Research Report, Use of Cash, and Watchlist already exist in the current shell.
Claude must not restyle them blindly. Before material changes, each needs the
same route contract:

- [ ] user goal;
- [ ] required data and formula owner;
- [ ] actual/estimate boundary;
- [ ] all global data states;
- [ ] source-trace targets;
- [ ] empty and partial behavior;
- [ ] desktop, tablet, and mobile behavior;
- [ ] keyboard model;
- [ ] screenshot comparison and acceptance tests.

## 8. Historical valuation UX contract

### 8.1 Above-the-fold anatomy at 1440 px

The first viewport must show, in order:

1. compact product/search bar;
2. compact company identity and stable company tabs;
3. one control band;
4. chart title plus only the three-to-five decision KPIs relevant to the
   selected metric and period;
5. annual high/low strip;
6. at least the upper 60% of the main chart and the right facts/evidence rail.

The broad Ask hub, workspace map, ingestion operations, complete layer audit,
and long raw tables belong below the core chart or in dedicated routes.

### 8.2 Control priority

Always visible:

- metric selector;
- period presets;
- custom-date trigger;
- chart-settings trigger;
- current data state/freshness.

Inside Chart Settings:

- estimate visibility and forecast years;
- dividend, payout, yield, recession, and transaction layers;
- normal-multiple window;
- current/custom valuation line;
- display scale and currency options when supported;
- saved layout name/apply/save.

Do not put forecast case, user growth, or target multiple in the Historical
control band unless the selected forecast overlay explicitly needs them.

### 8.3 Chart visual grammar

- [ ] Price: black/dark high-contrast line with non-color cue.
- [ ] Fundamental metric: green area, labeled with the active metric.
- [ ] Fair value: orange line plus labeled legend cue.
- [ ] Normal multiple: blue line plus labeled legend cue.
- [ ] Forecast area: visibly lighter region and every year marked `E`.
- [ ] Recession/event bands: neutral shade that does not reduce line contrast.
- [ ] Portfolio transactions: shape plus BUY/SELL text in tooltip and table.
- [ ] Hidden series remain represented in Graph Key as off, not silently
  removed.
- [ ] Axis unit, currency, scale, and metric are always visible.
- [ ] Color is never the only way to distinguish a series; combine color with
  line style, marker, label, or pattern.
- [ ] Do not reproduce commercial logos, exact proprietary chrome, screenshots,
  or trade dress.

### 8.4 Chart interactions

Pointer/touch:

- [ ] Hover or focus shows the same crosshair and point card.
- [ ] Single click/tap selects and persists a year.
- [ ] Selecting a year synchronizes chart, high/low strip, fiscal table,
  evidence rail, and URL fact/year state.
- [ ] Selecting two valid historical years produces the backend-calculated
  performance result and names the dividend-reinvestment assumption.
- [ ] Touch selection never depends on hover.
- [ ] Pan is contained within the chart and never moves the whole page
  horizontally.
- [ ] A visible cue indicates more chart content off-screen on mobile.

Keyboard:

- [ ] Tab enters the selected or latest chart point, not every point by default.
- [ ] Arrow Left/Right moves one point; Home/End moves first/last.
- [ ] Enter selects the point.
- [ ] Shift+Enter opens Fact Audit for the selected point.
- [ ] Escape clears a transient tooltip or closes the topmost audit/settings
  surface.
- [ ] Focus remains visible and the focused point scrolls into view.
- [ ] The year announcement includes Reported/Estimate, metric, price, fair
  value, normal multiple, dividend, quality, and source status.

State synchronization:

- [ ] Changing metric updates area, valuation lines, table label/value, Graph
  Key, source trace, and export URL as one transaction.
- [ ] Changing period recalculates range-dependent backend values and updates
  the high/low strip, chart, navigator, and return selection.
- [ ] If the selected year leaves the range, selection moves to the latest
  visible reported year and announces the change.
- [ ] Toggling a line updates legend state and axis domain without losing the
  selected point.
- [ ] Saved layouts include metric, period, line visibility, and supported chart
  settings, but never financial data snapshots.
- [ ] Loading a layout reports unsupported or missing settings instead of
  silently dropping them.

### 8.5 Accessible chart structure

- [ ] Use `<figure>` with a concise chart caption and a separate interactive
  point list/table. Do not place interactive buttons inside an element exposed
  solely as `role=img`.
- [ ] Provide an accessible fiscal-year table containing the same displayed
  facts.
- [ ] Tooltip content is not the only source of information.
- [ ] Announce point changes through one polite live region; do not announce on
  every pointer movement.
- [ ] Graph Key toggles are native buttons/switches with accessible names and
  pressed state.
- [ ] A dual-thumb range control has two named inputs or an equivalent pair of
  Start/End selects.
- [ ] Target size is at least 24 x 24 CSS px, with 44 x 44 preferred on touch.

## 9. Fact Audit and `source_trace` drilldown

### 9.1 Opening behavior

- [ ] Every visible number, chart point, table cell, peer metric, forecast
  output, screen result, and portfolio market value exposes an Audit action.
- [ ] The default action opens a side sheet/drawer without navigating away.
- [ ] `Open full Data Audit` is a secondary action for the complete workspace.
- [ ] URL can deep-link selected company, route, fact ID, fact family, and year.
- [ ] Close with Escape, close button, or browser Back when the drawer state was
  pushed.
- [ ] Closing restores focus to the exact number or chart point that opened it.

### 9.2 Required drawer content

Summary first:

- display label and value;
- unit and currency;
- fiscal period and actual/estimate basis;
- as-of and available-at time;
- source and document title/type;
- quality status, flags, confidence, and version/restatement state.

Lineage second:

- method and formula;
- deterministic/user/consensus evidence lane;
- immutable `input_fact_ids`;
- clickable input facts with breadcrumb/back behavior;
- adjustments and normalization policy;
- filing/document IDs and approved source links.

Raw evidence last:

- collapsed by default;
- structured and syntax-safe;
- never the primary user experience;
- secret/private fields removed by backend contract before rendering.

### 9.3 Drawer accessibility

- [ ] If modal, use `role=dialog`, `aria-modal=true`, a visible heading, focus
  trap, and focus restoration.
- [ ] If non-modal, use a labeled complementary region and keep a clear focus
  path between origin and drawer.
- [ ] State changes and source-load failures are announced once.
- [ ] Source document links explain file type and whether they open a new tab.
- [ ] Long IDs can be copied with an accessible button, but the UI never copies
  secrets.

## 10. Keyboard and accessibility contract

### 10.1 Document and navigation

- [ ] Add a visible-on-focus Skip to content link.
- [ ] DOM order matches visual order. Never use CSS `order` to move major
  workflows ahead of earlier screen-reader content.
- [ ] One primary `<main>` and one route-level `<h1>` per page.
- [ ] Company navigation uses `role=tablist`, `role=tab`, `role=tabpanel`,
  `aria-selected`, `aria-controls`, and roving `tabIndex`, or standard links if
  each tab is a true route. Do not imitate tabs with only `aria-pressed`.
- [ ] Arrow Left/Right moves tabs when using tab semantics; Enter/Space behavior
  follows the chosen automatic/manual activation model.
- [ ] Hidden mobile/desktop navigation is removed from both visual and
  accessibility trees.
- [ ] Active destination is exposed with `aria-current=page` for route links.

### 10.2 Focus and overlays

- [ ] Search, product tour, chart settings, More sheet, and audit drawer define
  initial focus, Tab containment where modal, Escape, and focus restoration.
- [ ] Opening one overlay closes or layers correctly above another; no two focus
  traps.
- [ ] Focus rings meet contrast requirements and are not clipped by horizontal
  scrollers or sticky headers.
- [ ] Route change moves focus to the route heading or a deliberate preserved
  location and announces the new workspace.

### 10.3 Forms and controls

- [ ] Every select/input has a visible label, not only `aria-label`.
- [ ] Numeric assumptions show unit, allowed range, step, and error text.
- [ ] Use `inputMode=decimal` or numeric keypad hints on mobile where useful.
- [ ] Disabled controls explain why through adjacent persistent text or an
  accessible description.
- [ ] Toggle state is exposed semantically and not only through color.
- [ ] Destructive actions such as removing portfolio transactions require a
  scoped confirmation and never share styling with normal navigation.

### 10.4 Status and error communication

- [ ] Loading containers use `aria-busy`.
- [ ] Polite live region: loading completion, refresh completion, selected chart
  year, saved layout.
- [ ] Assertive alert: user-triggered import failure, sign-in loss, unrecoverable
  contract error.
- [ ] Do not assign `role=alert` to continuously updating prices or countdowns.
- [ ] Errors identify the affected module and preserve valid neighboring data.

### 10.5 Tables and charts

- [ ] Tables have captions or accessible names, `scope` on headers, and
  `aria-sort` on sortable columns.
- [ ] Sticky headers/columns retain readable background and focus outline.
- [ ] Table horizontal scrolling is contained and keyboard reachable.
- [ ] Abbreviations such as PER, ROE, ROIC, FCF, TTM, and YoY have a glossary or
  accessible expansion.
- [ ] Series pass contrast checks in light, dark, high-contrast, and
  forced-colors modes.
- [ ] Respect `prefers-reduced-motion`; selection and data changes never depend
  on animation.
- [ ] Verify 200% and 400% zoom without loss of controls or evidence.

## 11. Responsive contract

### 11.1 Required test widths

- 1440 x 900 desktop
- 1280 x 800 compact desktop
- 1024 x 768 tablet landscape
- 768 x 1024 tablet portrait
- 390 x 844 common mobile
- 320 x 568 narrow mobile stress test

### 11.2 Desktop acceptance

- [ ] No page-level horizontal scrollbar at 1280 or 1440.
- [ ] Chart remains dominant and right rail visible at 1440.
- [ ] At 1024, control wrapping does not cover plot or hide selected state.
- [ ] Dense tables may scroll inside their own region; sticky first column and
  period header remain usable.
- [ ] Navigation does not become a third tab row.

### 11.3 Mobile acceptance

- [ ] Primary destinations are visible without horizontally scrolling two tab
  rows.
- [ ] `/company/[id]` resolves to Graph at every viewport. On mobile, Snapshot
  is the first prominent bottom-navigation destination and recommended quick
  overview, but the app does not silently change routes by viewport.
- [ ] Historical shows compact metric, period, selected-year summary, and
  simplified/pannable chart before secondary controls.
- [ ] One evidence affordance expands into a bottom sheet; it does not sit as a
  permanent 60 px overlay above a permanent 60 px bottom nav.
- [ ] Use `env(safe-area-inset-bottom)` for fixed mobile navigation.
- [ ] Bottom navigation never covers the final row, chart point, CTA, or drawer
  controls.
- [ ] When a table must scroll horizontally, the page stays fixed and the user
  receives a scroll cue.
- [ ] Touch targets are 44 x 44 CSS px where practical.
- [ ] Landscape mobile remains usable and does not trap the audit sheet.
- [ ] On-screen keyboard does not hide search results, forecast validation, or
  import confirmation.

## 12. Visual-density rules

- [ ] Use LUXON tokens and existing primitives; no one-off hardcoded palette.
- [ ] Prefer 2–6 px radii, compact dividers, and dense row rhythm for terminal
  surfaces. Do not turn every group into a large floating card.
- [ ] Reserve elevated cards for decisions, warnings, or overlays—not every
  metric.
- [ ] Avoid glassmorphism, decorative gradients, oversized hero copy, emoji,
  fake icons, and excessive whitespace.
- [ ] Use tabular numerals and right alignment for comparable financial values.
- [ ] Keep units in headers or labels so cells remain scannable.
- [ ] Hierarchy comes from typography, alignment, grouping, and dividers before
  color or shadow.
- [ ] Warning colors are reserved for data quality and operational risk, not
  decorative emphasis.
- [ ] FAST Graphs familiarity comes from analytical placement and interaction,
  not copied branding, exact colors, commercial copy, screenshots, or assets.
- [ ] FnGuide familiarity comes from dense grouped facts, statement hierarchy,
  period clarity, and quick tab switching—not a literal visual clone.

## 13. Common Claude implementation failures to prevent

Claude must explicitly check that it did not:

- [ ] copy a commercial logo, screenshot, icon, phrase, exact chrome, or trade
  dress;
- [ ] build a generic card dashboard instead of a dense research terminal;
- [ ] place a hero/AI prompt above the Historical chart or company Snapshot;
- [ ] show Historical controls on Financials, Consensus, Peers, or System;
- [ ] make active tabs move position between navigation rows;
- [ ] use CSS `order` to create a visual order different from DOM order;
- [ ] keep all routes in one 4,000-line client page after route extraction;
- [ ] fetch every product endpoint before showing one route;
- [ ] replace URL navigation with local state that breaks refresh/share/back;
- [ ] call DART, KRX, SEC, consensus, or market providers directly from the
  browser;
- [ ] calculate or round financial outputs in the frontend beyond display
  formatting approved by the contract;
- [ ] turn `null` into zero, a fixture into production, configured into live, or
  stale into current;
- [ ] invent analyst estimates, peer sets, ranks, source traces, or missing
  company facts;
- [ ] hide a warning because the layout looks cleaner without it;
- [ ] use a canvas/chart tooltip with no accessible table or keyboard model;
- [ ] rely on hover for touch or keyboard users;
- [ ] use color alone for actual/estimate, status, or chart-series distinction;
- [ ] dump raw JSON as the primary Fact Audit experience;
- [ ] expose secret values while trying to make provider setup convenient;
- [ ] make missing-contract, missing-key, rate-limit, and upstream-error states
  look identical;
- [ ] allow fixed mobile navigation/evidence UI to cover primary content;
- [ ] accept a screenshot that is loading, cropped, horizontally broken, or on
  the wrong state;
- [ ] claim WCAG compliance from screenshots alone;
- [ ] claim production readiness while only fixture data is visible.

## 14. Component and fixture inventory required before final styling

Build Storybook, a local state gallery, or deterministic test routes for:

Core components:

- [ ] product shell and company header;
- [ ] route tabs and More sheet;
- [ ] global search dialog;
- [ ] metric selector;
- [ ] period selector and custom date range;
- [ ] data-state badge/banner;
- [ ] chart legend toggle;
- [ ] chart point tooltip/selection card;
- [ ] Fact Audit drawer and full audit workspace;
- [ ] dense KPI group;
- [ ] financial statement table;
- [ ] consensus revision table/chart;
- [ ] peer comparison table;
- [ ] screener filter builder/results;
- [ ] portfolio import preview/errors;
- [ ] provider/system row;
- [ ] mobile bottom navigation and More sheet;
- [ ] mobile source-evidence sheet.

Every data component must be reviewed in:

- [ ] loading;
- [ ] ready;
- [ ] empty;
- [ ] partial;
- [ ] stale;
- [ ] configured;
- [ ] fixture_non_production;
- [ ] missing_source;
- [ ] missing_contract;
- [ ] missing_key;
- [ ] rate_limited;
- [ ] upstream_error;
- [ ] malformed/unknown contract;
- [ ] very long company name;
- [ ] KRW, USD, and JPY formatting;
- [ ] negative, zero, and very large legitimate values;
- [ ] long source-document IDs and quality flags.

Fixtures must be realistic but remain explicitly non-production. Never reuse
fixture values in production screenshots, reports, analytics, or training
evidence without the fixture label.

## 15. Verification plan

### 15.1 Visual comparison

- [ ] Capture the public reference and LUXON implementation at the same viewport
  and comparable state.
- [ ] Put the two accepted images in the same comparison input.
- [ ] Compare information hierarchy, chart dominance, control density, tab
  position, whitespace, typography, borders, and responsive behavior.
- [ ] Fix P0/P1 differences and compare again.
- [ ] Use public references only for analytical grammar and workflow.
- [ ] Record results in root `design-qa.md` with final result `passed` or a named
  blocker.

### 15.2 Interaction checks

- [ ] Search by ticker and company name.
- [ ] Open every primary route, refresh it, share its URL, then test Back and
  Forward.
- [ ] Change ticker during an in-flight request and verify no old-ticker values
  appear under the new header.
- [ ] Change metric and verify chart, table, Graph Key, source trace, and export
  state update together.
- [ ] Change period and verify all range-dependent values update.
- [ ] Select chart points by mouse, touch, and keyboard.
- [ ] Open and close Fact Audit from chart, table, Snapshot, Consensus, Peers,
  Screener, and Portfolio.
- [ ] Verify focus restoration after every overlay.
- [ ] Test each global data state through deterministic fixtures.
- [ ] Test imports, validation failures, retries, and provider setup links.

### 15.3 Automated checks

- [ ] TypeScript typecheck passes.
- [ ] ESLint passes with no new errors.
- [ ] Production build passes.
- [ ] Existing Playwright product tests pass.
- [ ] Add tests for route history, state differentiation, stale ticker race,
  tab semantics, audit focus return, and mobile overlay non-overlap.
- [ ] Add an automated accessibility scan as a signal, then complete manual
  keyboard and screen-reader checks.
- [ ] Browser console has no uncaught errors or hydration warnings.
- [ ] No page-level overflow at required viewports.

## 16. Definition of done

Claude Design work is complete only when all of the following are true:

- [ ] Historical is recognizably FAST Graphs-familiar in analytical workflow
  while remaining visually and legally LUXON-owned.
- [ ] Snapshot, Financials, Consensus, and Peers are dense and period-aware in
  the FnGuide tradition without being literal clones.
- [ ] Each route begins with its own user task rather than shared irrelevant
  controls.
- [ ] All required routes are refreshable, shareable, and Back/Forward-safe.
- [ ] All data states are visibly distinct and fail closed.
- [ ] Fixture mode cannot be mistaken for source-backed/live mode.
- [ ] A visible financial value always reaches structured Fact Audit.
- [ ] Chart and core navigation work with pointer, keyboard, and touch.
- [ ] Mobile exposes a deliberate reduced workflow without covered content or
  hidden double-scroll navigation.
- [ ] DOM order, focus order, and visual order match.
- [ ] Reference-versus-implementation comparison has no unresolved P0/P1 visual
  or interaction defects.
- [ ] Tests, typecheck, lint, build, responsive review, browser console review,
  and manual accessibility checks are recorded.
- [ ] External API keys, live ingestion, and provider contracts are still
  reported as external gates when not actually connected.

Final acceptance statement must name what is source-backed, what is fixture,
what is implemented but unconnected, and what remains blocked. Do not use
`production-ready` as a synonym for a polished fixture UI.
