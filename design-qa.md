# LUXON Design QA

Status: pre-Claude-design baseline
Audit date: 2026-08-16

This is the measured baseline for the current implementation. It is intentionally
`blocked`: the backend/data contracts and working terminal foundation are ready,
but the requested FAST Graphs-familiar, FnGuide-dense frontend redesign has not
yet passed the Claude Design implementation contract.

## Scope and routes

Audited routes and states:

- `/terminal?ticker=AAPL&tab=Historical`
- Historical metric menu, 4Y period, chart settings, and Data Audit transition
- Consensus `missing_source` state
- desktop 1440 x 900
- mobile 390 x 844

Canonical target routes for the next implementation pass:

- `/terminal` for search and resume
- `/company/[id]/graph` for Historical Graph
- `/company/[id]/snapshot`
- `/company/[id]/financials`
- `/company/[id]/forecast`
- `/company/[id]/consensus`
- `/company/[id]/peers`

## Reference evidence

Public references:

- FAST Graphs Historical Graph:
  <https://docs.fastgraphs.com/en/articles/9419962-historical-graph>
- FAST Graphs Forecasting Charts User Guide:
  <https://docs.fastgraphs.com/en/articles/13577168-forecasting-charts-user-guide>
- FnGuide Company Guide information architecture:
  <https://wcomp.fnguide.com/Help/Guide?cmp_cd=0101N0>

The public Historical reference and current LUXON implementation were captured
at the same viewport and combined for visual comparison. Audit captures stay
outside the repository under `<TEMP>/luxon-design-audit-2026-08-16/`; they are
not product assets and must not be committed.

## Viewports and states

Checked directly:

- desktop Historical default
- metric menu open
- 4Y range selected
- chart settings expanded
- Data Audit navigation
- Consensus missing-source state
- mobile Historical layout

The AAPL path was explicitly `fixture_non_production`. This visual audit does not
prove live KR data readiness.

## Typography

Current text is legible and appropriately dense in the main chart surface.
However, the number of stacked headers, tab rows, controls, and status labels
reduces scan speed. The Claude pass must preserve readable body text while
compressing structural overhead.

## Spacing and alignment

Passed foundation:

- Historical metric, period, and chart-settings controls now share a compact
  desktop band.
- The chart, range strip, and evidence rail use a coherent grid.

Blocking gaps:

- The plot receives too little horizontal share relative to the facts rail.
  Target comparison starts at plot 78-82% and rail 18-22% at 1440px.
- Non-Historical workspaces inherit Historical controls and vertical overhead.
- Major content is visually reordered with CSS while DOM order stays different.

## Color and chart semantics

Passed foundation:

- price, fundamental, fair-value, normal-multiple, and forecast layers have
  distinct semantics;
- metric menu clearly marks locked/source-required values;
- missing Consensus does not invent numbers.

Blocking gap:

- `fixture_non_production` can coexist with a positive green source-backed
  signal in the current shell. These states must be mutually exclusive across
  badges, legends, exports, and assistive text.

## Borders, radii, elevation

The current flat bordered surfaces are compatible with a dense terminal. The
next pass should reduce redundant card shells and reserve elevation for actual
drawers, menus, and overlays. This is P1 polish after the state and IA blockers.

## Assets and brand/IP boundary

Passed:

- LUXON uses its own brand assets, code, chart renderer, tokens, copy, data
  model, and deterministic formulas;
- no FAST Graphs/FnGuide assets are shipped;
- authenticated commercial-surface automation and scraping tooling were removed;
- reference capture is limited to manual public-document review outside Git.

## Interactions

Passed directly:

- metric menu opens and exposes availability state;
- period selection updates the displayed range;
- chart settings open;
- Data Audit navigation changes the route state;
- Consensus fails closed;
- browser console showed no warning or error during the audited flow.

Blocking gaps:

- active navigation can move between two tab rows;
- chart settings appear in more than one surface;
- redirect-only company routes are not meaningful route shells;
- `/terminal` is still the monolithic company workspace instead of focused
  search/resume;
- one broad endpoint fanout can turn a single core request failure into a global
  fallback.

## Data states and source_trace

Passed foundation:

- shared response states include ready, partial, stale, configured,
  fixture_non_production, missing_source, missing_contract, missing_key,
  rate_limited, and upstream_error;
- Consensus and Peers fail closed;
- provider credentials are represented as configuration state, not guessed;
- visible research contracts include source-trace details and recovery copy.

Remaining visual gate:

- every workspace must render its own loading, empty, stale, and recovery state
  independently;
- positive source language must be impossible when the actual envelope is
  fixture or unavailable.

## Accessibility

Passed foundation:

- key controls have accessible names;
- selected control state is exposed;
- chart-year keyboard handling includes Arrow keys, Home, End, and Shift+Enter.

Blocking gaps:

- DOM and visual order differ for major regions;
- two-row tab overflow harms keyboard location stability;
- a complete screen-reader, zoom, forced-colors, contrast, and focus-order pass
  remains required.

## Responsive behavior

Blocking findings at 390 x 844:

- both tab strips require large horizontal scrolling;
- fixed evidence and bottom navigation consume excessive chart height;
- desktop information architecture is compressed rather than reduced;
- the chart lacks a strong visible cue for off-screen range and selection.

The canonical company default remains Graph at every viewport. Snapshot may be
the first prominent mobile navigation destination, but viewport changes must not
silently change the route.

## Automated verification

Current implementation verification:

- Python: 377 passed
- focused API/Ruff checks: passed
- TypeScript type-check: passed
- Next.js production build: passed, 10 routes generated
- ESLint: 0 errors, 10 pre-existing warnings
- Playwright: 10 passed
- production dependency audit: 0 known vulnerabilities
- Docker Compose model validation: passed with a validation-only password
- PowerShell parser validation: 12 scripts passed
- GitHub workflow guard: all actions SHA-pinned, read-only permission, no direct
  input/secret interpolation in run blocks
- public snapshot secret/PII scan: no real secret, personal path, private Figma
  identifier, or authenticated-capture reference found

## Known external gates

- operator API keys are not configured in the repository;
- a live source-backed KR E2E was not run in this audit;
- Docker Desktop was unavailable for an actual image build/run;
- protected Render/Vercel/managed-Postgres deployment was not performed;
- Claude Design has not yet implemented and re-audited the P0/P1 frontend pass.

## Residual P2 items

Only after the P0/P1 gates close:

- minor typography rhythm;
- secondary transition timing;
- decorative chart/export polish;
- optional dark-theme refinement outside changed components.

## Blocking acceptance list

1. Enforce fixture/source-backed state mutual exclusion.
2. Make DOM order equal visual order for major regions.
3. Replace moving two-row navigation with the fixed Graph/Snapshot/Financials/
   Forecast/Consensus/Peers/Performance/More model.
4. Give each route independent data loading and recovery boundaries.
5. Replace redirect-only company paths with meaningful server route shells.
6. Keep Historical controls on Graph and route-specific chart controls only.
7. Provide one canonical chart-settings surface.
8. Remove page-level horizontal overflow at 1440, 1024, and 390px.
9. Complete same-viewport visual comparison after fixes.
10. Complete keyboard, screen-reader, zoom, contrast, and mobile checks.

final result: blocked
blocker: Claude Design must implement and verify the ten blocking acceptance items above before the requested frontend can be called complete.
