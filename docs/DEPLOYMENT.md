# Deployment Guide

## Scope

The MVP is designed as a Vercel-first protected early-access valuation product, while local execution remains the development path.

Protected access is a launch-stage control, not a personal-only product boundary. Production work should assume future multi-user operation, owner-scoped state, data-source licensing review, and stricter observability.

Production deployment must preserve the data principles:

- Do not generate financial numbers with an LLM.
- Do not treat fixture values as production data.
- Preserve `source_trace`, `method`, `policy`, `confidence`, `formula`, and `quality_status` in API responses.
- Keep ingestion and normalization jobs outside the hot request path when they need network, raw-document storage, or long-running parsing.

## Vercel Layout

| Surface | Path | Runtime role |
|---|---|---|
| Next.js app | `apps/web` | Protected valuation product UI |
| FastAPI entrypoint | `api/index.py` | `/api/*` Python function |
| API app | `services/api/main.py` | Read API and fixture-backed MVP endpoints |
| Ingestion CLI | `backend/normalize/cli.py`, `services/ingestion_worker/cli.py` | Manual or scheduled worker, not interactive request path |
| Database | Neon Postgres | Versioned normalized facts, market prices, dividends, raw-object metadata |
| Raw storage | Vercel Blob | Append-only source payloads queued from local/GitHub Actions ingestion |

`vercel.json` first lets Next.js serve filesystem routes such as `/api/auth/*` and `/api/pf/session`, then routes unmatched `/api/*` requests to `api/index.py`.
Do not add a `/(.*)` route to `/apps/web/$1`; Next App Router pages such as `/` must stay on the filesystem handler or Vercel will serve a platform 404.

## Private Access

Recommended early-access protection:

1. Enable Vercel Deployment Protection for preview and production deployments.
2. Keep the repository private.
3. Configure GitHub OAuth through Auth.js.
4. Set `AUTH_ALLOWED_EMAILS` to the owner email allowlist.
5. Keep FastAPI protected by the signed `pf_session` cookie.
6. Restrict CORS origins before production use.

Current code status:

- The UI bridges GitHub OAuth into a signed private `pf_session` cookie.
- FastAPI rejects protected `/api/*` routes when `API_AUTH_REQUIRED=true` and `API_AUTH_DISABLED=false`.
- FastAPI CORS uses explicit origins from `API_CORS_ORIGINS` and local development defaults.
- FastAPI adds security headers and can throttle per-client API requests with `API_RATE_LIMIT_ENABLED=true`.
- Fixture data is marked `fixture_non_production`.

## Environment

Required for the first KR live ingestion:

```powershell
$env:DATA_BACKEND = "postgres"
$env:DATABASE_URL = "postgresql://..."
$env:DART_API_KEY = "<OpenDART key or use OPENDART_API_KEY>"
```

Local development may use `.env.local` instead of shell exports. Copy
`.env.example` to `.env.local`, fill private values locally, and keep the file
out of git. API and ingestion worker processes load `.env.local` automatically
without overriding real environment variables from CI, Vercel, or GitHub
Actions.

On Windows, use the local secret helper instead of pasting keys into the shell:

```powershell
pnpm secrets:local
pnpm secrets:local -- --Overwrite
pnpm secrets:local -- --IncludeDatabase
```

The helper prompts with `Read-Host -AsSecureString`, writes only to ignored
`.env.local`, and does not print secret values. After OpenDART is configured,
run:

```powershell
pnpm collect:opendart:kr:005930:raw
pnpm inspect:raw:kr:005930
pnpm build:valuation-inputs:kr:005930
```

Recommended Vercel variables:

```txt
DATA_BACKEND=postgres
DATABASE_URL=postgresql://...
BLOB_READ_WRITE_TOKEN=
CHART_BLOB_QUEUE_ENABLED=false
SEC_USER_AGENT=
AUTH_REQUIRED=true
AUTH_SECRET=
AUTH_GITHUB_ID=
AUTH_GITHUB_SECRET=
AUTH_ALLOWED_EMAILS=owner@example.com
PF_COOKIE_SECRET=
API_AUTH_REQUIRED=true
API_AUTH_DISABLED=false
API_CORS_ORIGINS=https://your-private-preview.vercel.app,https://your-production-domain.example
API_RATE_LIMIT_ENABLED=true
API_RATE_LIMIT_REQUESTS=600
API_RATE_LIMIT_WINDOW_SECONDS=60
API_RATE_LIMIT_EXEMPT_PATHS=/api/health
API_ENABLE_HSTS=true
ALLOW_FIXTURE_FALLBACK=false
OPENDART_API_KEY=
DART_API_KEY=
EDINET_API_KEY=
JQUANTS_REFRESH_TOKEN=
```

Local development can keep `API_AUTH_DISABLED=true`, `API_RATE_LIMIT_ENABLED=false`,
and `DATA_BACKEND=fixture`.

Production fixture policy:

- `VERCEL_ENV=production` disables fixture fallback by default.
- Set `ALLOW_FIXTURE_FALLBACK=true` only for non-production preview/debug deployments.
- Set `DISABLE_FIXTURE_FALLBACK=true` to force source-backed mode in any environment.
- When fallback is disabled and source-backed rows are missing, fixture-backed API surfaces return `503 source_data_required` instead of silently showing sample data.

## Ingestion And Storage

Live collection is intentionally outside the Vercel request path:

The first production bootstrap target is the KR top-market-cap priority
universe. AAPL and the US pattern set remain regression fixtures and later US
coverage targets, but Vercel deployment gates now default to the KR priority
universe so the product can validate Korean equity workflows first.

```txt
KR priority tickers:
005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS
```

```powershell
python -m services.ingestion_worker.cli run-source-e2e --market KR --years 2020:2025 --persist --continue-on-error --dry-run
python -m services.ingestion_worker.cli run-source-e2e --market KR --years 2020:2025 --persist --continue-on-error
python -m services.ingestion_worker.cli normalize-us --ticker AAPL --years 2020:2025 --persist
python -m services.ingestion_worker.cli normalize-us-batch --tickers AAPL,NVDA,CRM,O,JPM --years 2020:2025 --persist --continue-on-error
python -m services.ingestion_worker.cli collect --market KR --ticker 005930.KS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect --market JP --ticker 7203.T --years 2024:2024 --persist
python -m services.ingestion_worker.cli collect-fred --series DGS10,DGS2,FEDFUNDS,CPIAUCSL,UNRATE,USREC,DEXKOUS,DEXJPUS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-ecos --series "<stat_code:cycle:item_code>" --years 2024:2024 --persist
python -m services.ingestion_worker.cli collect-kosis --tables "<orgId:tblId-or-userStatsId>" --years 2024:2024 --persist
python -m services.ingestion_worker.cli collect-estat --stats-data-ids "<statsDataId>" --years 2024:2024 --persist
python -m services.ingestion_worker.cli collect-stooq-prices --market US --tickers AAPL,NVDA,CRM,O,JPM --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-fdr-prices --market US --tickers AAPL,NVDA,CRM,O,JPM --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-pykrx-prices --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-marcap --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-jquants --tickers 7203.T,6758.T,6861.T,8306.T,7974.T --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-edinet --tickers 7203.T,6758.T,6861.T,8306.T,7974.T --years 2020:2025 --persist
python -m services.ingestion_worker.cli import-market-csv --path storage/imports/market_prices.csv --persist
python -m services.ingestion_worker.cli export-consensus-template --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --cases median --out storage/imports/consensus_estimates.csv
python -m services.ingestion_worker.cli import-consensus-csv --path storage/imports/consensus_estimates.csv --persist
python -m services.ingestion_worker.cli import-fnguide-export --path storage/imports/fnguide_dataguide.csv --persist
python -m services.ingestion_worker.cli data-lake-plan --markets US,KR,JP --years 2020:2025 --format markdown --out storage/ingestion_plans/data_lake_plan.json
python -m services.ingestion_worker.cli run-p1-e2e --years 2020:2025 --persist --continue-on-error
python -m services.ingestion_worker.cli secret-audit --strict
python -m services.ingestion_worker.cli preflight --markets KR --require-blob --strict
python -m services.ingestion_worker.cli deploy-gate --markets KR --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --require-blob --require-consensus-forecast --strict
python -m services.ingestion_worker.cli doctor --markets KR --require-blob --strict
pnpm doctor:kr
pnpm e2e:source:kr:check
pnpm preflight
pnpm preflight:deploy
pnpm e2e:source:kr:dry-run
pnpm e2e:source:kr
pnpm e2e:p1:dry-run
pnpm e2e:p1
pnpm ingest:us:mvp
pnpm data-lake:plan
pnpm collect:fred
pnpm collect:stooq:us
pnpm collect:fdr:us
pnpm collect:pykrx:kr
pnpm collect:marcap:kr
pnpm collect:jquants:jp
pnpm collect:edinet:jp
pnpm import:fnguide
pnpm secret:audit
pnpm deploy:gate
pnpm blob:sync:dry-run
pnpm blob:sync
```

The ingestion worker:

- writes raw documents to `storage/raw/**`,
- queues Vercel Blob uploads under `storage/blob_queue/**`,
- stores raw object metadata in `raw_objects`,
- stores normalized adjusted EPS in `adjusted_earnings`,
- stores SEC `companyfacts.zip` and `submissions.zip` bulk archives in `source_documents`/`raw_objects` for later warehouse parsing,
- stores selected SEC companyfacts rows in `financial_facts` and FY valuation-ready facts in `metric_values`,
- stores KR/JP market-standard EPS and statement metrics from OpenDART/J-Quants raw JSON in `adjusted_earnings` and `metric_values`,
- stores J-Quants JP daily quote raw JSON in `source_documents`/`raw_objects` and source-backed adjusted closes in `price_bars`,
- stores J-Quants JP dividend raw JSON in `source_documents`/`raw_objects` and per-share dividends in `dividends`,
- stores EDINET JP annual securities report metadata and XBRL-to-CSV/XBRL ZIP payloads in append-only raw storage,
- stores user-curated ECOS/KOSIS/e-Stat official statistics API responses in `source_documents`/`raw_objects` and parseable observations in `macro_series` and `industry_series`,
- stores point-in-time consensus snapshots in `consensus_estimate_snapshots`,
- rejects forecast rows without `source_url`, `source_document_id`, or `filing_id`,
- rejects template/fixture-only forecast quality statuses before persistence,
- stores imported prices/dividends in `price_bars` and `dividends`,
- stores pykrx KR daily OHLCV raw CSV in `source_documents`/`raw_objects` and source-backed close prices in `price_bars`,
- stores FinanceData marcap yearly parquet archives in `source_documents`/`raw_objects` and KR close price, market cap, listed shares, and rank evidence in `price_bars.source_trace`,
- stores FinanceDataReader wrapper-derived daily CSV in `source_documents`/`raw_objects` and fallback closes in `price_bars` with `quality_status=wrapper_derived_price`,
- stores user-supplied FnGuide/DataGuide CSV/XLSX exports in `source_documents`/`raw_objects` and canonical rows in `metric_values`,
- stores Stooq source-backed daily close prices in `price_bars`,
- stores FRED macro/rates/FX observations in `macro_series` and US recession bands in `recession_periods`,
- stores user-provided CSV portfolio trades in `portfolio_transactions`,
- stores owner watchlists in `watchlists` and `watchlist_items`,
- stores owner chart presets in `chart_layouts`.

`pnpm blob:sync:dry-run` validates queued Blob manifests without
`BLOB_READ_WRITE_TOKEN`. It reports missing local files and ignores previous
`.result.json` files so uploaded result manifests are not reprocessed. Actual
upload still requires `BLOB_READ_WRITE_TOKEN`.

`pnpm secret:audit` scans stored source metadata before Blob sync. It checks
`storage/blob_queue`, `storage/cache`, `storage/parse_failures`, and rendered
chart JSON metadata for unredacted query credentials or runtime secret values.
Findings are redacted in the audit output; the raw secret value is never
printed. `preflight` includes the same audit by default.

Chart rendering is server-side Matplotlib in the FastAPI route. SVG and PNG endpoints use a content-hash render cache and honor line-visibility options, including individual `hidden_scenario_lines`. Rendered charts include a compact source-trace caption, and chart-run records expose `evidence_summary` so cached exports can be audited without recalculating the valuation payload. Local and GitHub Actions runs write to `storage/rendered_charts` unless `CHART_CACHE_DIR` is set. Vercel functions use the platform temp directory. `POST /api/v1/charts/valuation-map/runs` snapshots the valuation payload and display settings into `chart_runs` when Postgres is enabled, or a local manifest under `CHART_RUN_DIR` for non-production development. Set `CHART_BLOB_QUEUE_ENABLED=true` outside the hot request path when rendered chart artifacts should be queued for `pnpm blob:sync`.

Private user state is scoped by the signed `pf_session` cookie when `API_AUTH_REQUIRED=true`. FastAPI converts the verified allowlisted email into a stable hashed owner key and uses that key for portfolio transactions, watchlists, and chart layouts. Raw email addresses are not used as owner keys.

`GET /api/v1/companies/{ticker}/valuation-map`, `/performance`, `/snapshot`, `/financials`, `/fun-graphs`, `/fiscal-fitness`, `/health-check`, `/research-report`, `/analyst-scorecard`, `/use-of-cash`, `/data-audit`, `GET /api/v1/screener`, `GET /api/v1/watchlist`, and `GET /api/v1/portfolio` read Postgres first when `DATA_BACKEND=postgres`; fixture fallback remains local-only and non-production.

`GET /api/v1/companies/{ticker}/fiscal-fitness` is a derived read API. It uses source-backed financial rows and metric-specific traces where available. Liquidity and interest coverage ratios remain `null` until the required balance-sheet and financing source facts are normalized.

`GET /api/v1/companies/{ticker}/health-check` is a derived read API. It computes an FG Score-style 0-100 score from Fiscal Fitness rows and forecast/scorecard evidence. It does not infer missing point-in-time consensus snapshots; predictability is flagged when the evidence is absent or fixture-only.

`GET /api/v1/companies/{ticker}/research-report` is a deterministic report assembly API. It derives section verdicts and report audit facts from valuation-map, Health Check, Fiscal Fitness, Forecast, and Use Of Cash facts. It does not use an LLM to generate financial numbers, and fixture-only report output is blocked in production unless fixture fallback is explicitly allowed.

`GET /api/v1/companies/{ticker}/exports/research-report.md`,
`/exports/research-report.json`, and `/exports/data-audit.csv` are read-only
download contracts for private review. They serialize the existing report and
Data Audit rows and do not add new financial calculations.

`GET /api/v1/companies/{ticker}/performance` is a derived read API. It computes hypothetical investment performance from source-traced historical valuation-map prices and dividends. Forecast rows are excluded.

`GET /api/v1/companies/{ticker}/fun-graphs` is a derived read API. It turns source-traced financial rows into Financial Underlying Numbers line series and keeps each point linked to the upstream source trace.

`GET /api/v1/companies/{ticker}/analyst-scorecard` is a derived read API. It exposes 1Y/2Y point-in-time estimate accuracy and keeps fixture proxy rows separated from production source-backed snapshots.

`GET /api/v1/companies/{ticker}/use-of-cash` is a derived read API. It does not infer missing OCF, Capex, dividends paid, share repurchases, debt repayment, acquisitions, or net cash use. Missing source facts remain `null` and are exposed as quality flags.

`GET /api/v1/portfolio/sample` is demo-only. Production should use `POST /api/v1/portfolio/import` with user-provided CSV trades, then read `GET /api/v1/portfolio`.

`GET /api/v1/system/readiness` exposes source-readiness status for the private UI. It reports configuration presence, Postgres reachability, and source-backed row counts without printing secret values.

`GET /api/v1/system/priority-universe` exposes the current KR/US/JP Top 10
collection universe. Use `market=KR`, `US`, `JP`, or `ALL`. When source-backed
market-cap evidence exists in Postgres `price_bars`, the endpoint returns a
latest-market-cap rank. Otherwise it falls back to the product coverage
contract. Each row includes `source_trace` and `rank_policy` so the UI can
distinguish source-backed ranks from collection-order fallback rows.

`GET /api/v1/system/source-coverage` exposes per-ticker production data coverage.
It defaults to the KR Top 10 priority universe and supports `market=KR`, `US`,
`JP`, or `ALL` for the full 30-stock E2E gate. It reports whether each ticker has enough
source-backed adjusted earnings years, price years, valuation-ready
`metric_values`, source evidence from `source_documents`/`raw_objects`/
`financial_facts`, S1/S2/S4 method distribution, and 1Y-5Y consensus snapshot
coverage. Add `require_consensus_forecast=true` when the caller wants forecast
snapshots to be treated as required readiness evidence instead of optional
coverage context. Forecast readiness requires median/current
`adjusted_operating_eps` snapshots for the required years; low/high scenario
rows alone do not satisfy the gate. The response summary includes
`missing_by_requirement` so the
operator can see whether to rerun adjusted EPS normalization, price ingestion,
metric promotion, source archive collection, or consensus snapshot import.
The response also includes `remediation.next_actions`, a priority-ordered list
of concrete ingestion worker commands and GitHub Actions inputs. Use this as
the operational runbook for closing source coverage gaps. It never recommends
synthetic numbers; consensus gaps point to user-verified CSV import, while US
financial metric gaps point to SEC bulk archive collection and warehouse load.
When the missing core coverage spans more than one priority market, the first
remediation action is `run_priority_e2e` so operators can run the KR, US, then
JP Top 10 bootstrap before targeted repair commands. Single-market and
consensus-only gaps still keep the more specific repair actions.
For KR price gaps, it recommends both `collect_pykrx_prices` for OHLCV evidence
and `collect_marcap` for yearly parquet market cap, listed shares, rank, and
close-price evidence.

For the KR Top 10 transition, run the local operator summary before dispatching
the protected Vercel smoke:

```powershell
pnpm readiness:kr:top10
```

This wraps local KR valuation-cache coverage and production Postgres source
coverage into a compact report. `ready_for_protected_smoke` means the local
source-backed valuation cache is ready but Neon/Postgres coverage still needs
loading. `local_warehouse_ready` means DuckDB/Parquet source-backed rows are
available for local API proof, but protected deployment still requires
Neon/Postgres promotion. In `--summary-only` output, `source_coverage_status`
can be `ready` while `production_status` remains `local_warehouse_only`;
`production_ready` is the only final production status.

## GitHub Actions Manual Worker

`.github/workflows/kr-e2e.yml` is the dedicated first-run workflow for the KR
Top 10 production slice. It runs KR doctor, `run-source-e2e --market KR`,
KR valuation input build, Postgres valuation promotion, source coverage, secret
audit, optional Blob sync, and an optional strict KR deploy gate without
exposing US/JP paths. When `run_api_smoke=true`, it runs `pnpm smoke:api:kr`
against the HTTPS `KR_SMOKE_BASE_URL` repository variable using the
`PF_SESSION_COOKIE` repository secret. The optional `preview_base_url` input
must match that trusted variable exactly and cannot redirect the cookie to
another host.

Windows operators can dispatch that protected smoke path without opening the
GitHub Actions form manually:

```powershell
pnpm workflow:kr:smoke -- -BaseUrl https://your-private-preview.vercel.app -PreflightOnly
pnpm workflow:kr:smoke -- -BaseUrl https://your-private-preview.vercel.app -PartialAudit -PartialTickers 005930.KS -Watch
pnpm workflow:kr:smoke -- -BaseUrl https://your-private-preview.vercel.app -Persist -ContinueOnError -RequireConsensusForecast -SyncBlob -RunDeployGate -Watch
pnpm workflow:kr:smoke -- -BaseUrl https://your-private-preview.vercel.app -RunLabel kr-smoke-001 -Watch
```

Use `-RunLabel` for concurrent operator runs. When omitted, the helper generates
a timestamped `kr-smoke-*` marker, dispatches it to `KR Top 10 E2E`, and
`-Watch` follows only the GitHub Actions run whose display title contains that
marker.
Use `-PartialAudit` for the transition state where selected KR Top10 tickers are
`partial_source_backed` and must expose Data Audit-resolvable gap refs before the
full `pnpm smoke:api:kr` gate can pass.

`.github/workflows/ingestion-worker.yml` provides the broader
`workflow_dispatch` control plane for long-running ingestion outside Vercel
Functions.

Supported commands:

- `preflight`: run the full Vercel-first production gate, including static repo checks, migration head checks, private auth variables, and `doctor` runtime checks.
- `deploy_gate`: run strict preflight and source coverage together. By default the workflow also requires 1Y-5Y consensus forecast snapshots.
- `doctor`: report missing DB, market connector, and Blob configuration without printing secret values.
- `migrate`: run `alembic -c db/alembic.ini upgrade head`.
- `run_priority_e2e`: run the KR, US, then JP Top 10 source-backed E2E paths in one operator command. Blank ticker overrides are intentional; each market uses its priority universe.
- `run_source_e2e`: run one market's source-backed E2E path. `market=KR` defaults to the KR Top 10 priority universe and runs OpenDART, pykrx, marcap, KR valuation input build, Postgres valuation promotion, then source coverage. `market=US` runs SEC bulk, US adjusted earnings normalization, Stooq prices, then source coverage. `market=JP` runs J-Quants, EDINET, Stooq prices, then source coverage.
- `normalize_us`: collect SEC source documents and persist US adjusted earnings.
- `normalize_us_batch`: run SEC collection and adjusted EPS normalization for the US MVP ticker list in one worker run.
- `collect_market`: collect source documents for `US`, `KR`, or `JP`.
- `collect_sec_bulk`: collect SEC official `companyfacts.zip` and `submissions.zip` raw archives.
- `load_sec_bulk_warehouse`: parse local SEC bulk ZIP archives into `financial_facts` and `metric_values`.
- `collect_fred`: collect source-backed FRED macro/rates/FX/recession series into raw storage and macro tables.
- `collect_stooq_prices`: collect free Stooq daily CSV prices and persist source-backed close prices.
- `collect_fdr_prices`: collect FinanceDataReader wrapper-derived daily CSV prices and persist fallback close prices.
- `collect_marcap`: collect FinanceData marcap yearly KRX parquet archives and persist close price, market cap, listed shares, and rank evidence.
- `import_market_csv`: import source-backed price/dividend CSV data.
- `consensus_workpaper`: create a Markdown operator checklist for forecast evidence requirements before any 1Y-5Y forecast CSV is filled or imported.
- `export_consensus_template`: create a blank 1Y-5Y forecast snapshot CSV that still requires source-backed EPS and source fields before import.
- `validate_consensus_csv`: validate the filled 1Y-5Y forecast CSV for required ticker-year coverage, accepted cases, trace anchors, and blocked template quality statuses.
- `import_consensus_csv`: import point-in-time consensus estimate snapshots.
- `source_coverage`: run the source-backed coverage gate. The workflow defaults to `source_coverage_market=KR`, which checks the KR Top 10 priority universe first. Set `source_coverage_market` to `US`, `JP`, or `ALL` only when expanding beyond the first KR production slice, or set `source_coverage_tickers` for an explicit override. The `require_consensus_forecast` workflow input controls whether 1Y-5Y forecast snapshots are required.
- `secret_audit`: scan stored source metadata for unredacted API keys, tokens, and runtime secrets before Blob upload.
- `data_lake_plan`: generate a market/year/source partitioned ingestion manifest without downloading data.
- `blob_sync`: validate queued manifests with `pnpm blob:sync:dry-run`, then upload queued raw payloads to Vercel Blob.

Required repository secrets:

- `DATABASE_URL` for Neon/Postgres persistence.
- `SEC_USER_AGENT` for US SEC collection.
- `BLOB_READ_WRITE_TOKEN` for Blob sync.
- `OPENDART_API_KEY` or `DART_API_KEY` for Korea collection.
- `JQUANTS_REFRESH_TOKEN` or `JQUANTS_EMAIL` plus `JQUANTS_PASSWORD` for Japan collection.
- `EDINET_API_KEY` for Japan EDINET filing evidence collection.
- `FRED_API_KEY` for rates, FX, inflation, unemployment, and recession-band macro overlays.
- `PF_SESSION_COOKIE` for protected Vercel API smoke checks in the dedicated KR workflow.
- `KR_SMOKE_BASE_URL` repository variable containing the one trusted HTTPS deployment origin for that cookie.

Recommended production sequence:

```txt
1. Run the dedicated `KR Top 10 E2E` workflow for the first production slice, or run command=preflight and `python -m services.ingestion_worker.cli doctor --markets KR --strict` until required KR secrets and static checks pass.
2. On Windows, run `./scripts/run-kr-e2e.ps1 -Persist -Strict -ContinueOnError` to verify the same plan locally without live collection.
3. Run command=data_lake_plan to create a partitioned ingestion manifest for the target market/year range.
4. Run command=migrate.
5. Run command=run_source_e2e with `market=KR` to load the KR Top 10 first. Run command=run_priority_e2e with `priority_e2e_markets=KR,US,JP` only after the KR slice is green.
6. If one market fails and `continue_on_error` was disabled, rerun command=run_source_e2e for that market after fixing its prerequisite or source gap.
7. Run command=run_source_e2e only for targeted repair runs or one-market refreshes.
8. Run command=collect_fred for rates, FX, inflation, unemployment, and recession-band overlays.
9. Run command=collect_fdr_prices only as a wrapper-derived fallback when official/Stooq coverage is incomplete.
10. Run command=import_market_csv for source-backed market price/dividend data.
11. Run command=consensus_workpaper to create a source-evidence checklist when a standardized 1Y-5Y forecast CSV shell is needed.
12. Run command=export_consensus_template to create the CSV shell.
13. Fill `estimate_eps` and `source` from traceable consensus, company guidance, or explicit manual assumptions, then run command=validate_consensus_csv and command=import_consensus_csv.
14. If the Postgres promotion did not already run through `run_source_e2e`, run `python -m services.ingestion_worker.cli load-kr-valuation-postgres --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --strict` to promote KR valuation-cache rows into Neon/Postgres.
15. Run `python -m services.ingestion_worker.cli source-coverage --market KR --require-consensus-forecast --strict` and fix missing KR coverage before enabling production fixture blocking.
16. Run `python -m services.ingestion_worker.cli secret-audit --strict` before syncing queued raw payloads to Blob.
17. Run `python -m services.ingestion_worker.cli deploy-gate --markets KR --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --require-blob --require-consensus-forecast --strict` before the first KR deploy.
18. Set `KR_SMOKE_BASE_URL` to the protected HTTPS origin, then run the dedicated `KR Top 10 E2E` workflow with `run_api_smoke=true`. If supplied, `preview_base_url` must match the trusted variable exactly.
19. Keep sync_blob=true except for planning and migration-only runs. The worker validates source metadata secrets and the Blob queue before each Blob upload.
```

## Build And Smoke Checks

Local checks:

```powershell
pnpm preflight
python -m pytest
pnpm build
pnpm --filter @personal-fastgraphs/web test
pnpm smoke:api -- --base-url http://127.0.0.1:8000 --ticker AAPL
pnpm smoke:api -- --base-url http://127.0.0.1:8000 --ticker AAPL --require-consensus-forecast
```

Vercel checks after project link:

```powershell
vercel pull --yes --environment preview
vercel build
vercel deploy --prebuilt
pnpm smoke:api -- --base-url https://your-private-preview.vercel.app --ticker AAPL
pnpm smoke:api -- --base-url https://your-private-preview.vercel.app --ticker AAPL --require-consensus-forecast
```

Protected preview smoke:

- Load `/`.
- Run `pnpm smoke:api` with `PF_SESSION_COOKIE` or `--cookie` when API auth is enabled.
- Confirm `pnpm smoke:api` reports `source_coverage` as passed.
- Confirm strict smoke with `--require-consensus-forecast` reports source-backed forecast snapshots before treating forecast coverage as production-ready.
- Confirm `pnpm smoke:api` reports `macro_series` as passed.
- Confirm `pnpm smoke:api` reports `industry_series` as passed.
- Confirm the status pill says `API live`.
- Open Historical, Performance, Forecasting, Research Report, Financials, Fun Graphs, Fiscal Fitness, Health Check, Analyst Scorecard, Watchlist, Portfolio, and Data Audit.
- Confirm the Historical right panel shows `MVP Source Coverage` for the KR Top 10 priority universe.
- Confirm `005930.KS`, `000660.KS`, `402340.KS`, `005380.KS`, `028260.KS`, `032830.KS`, `373220.KS`, `207940.KS`, `329180.KS`, and `009155.KS` valuation maps load from source-backed rows or report missing source coverage explicitly.
- Confirm forecast rows include `source_trace`.
- Confirm the Forecasting consensus case matrix shows low/median/high estimate EPS rows and that each estimate EPS opens a `forecast_snapshot.{case}.estimate_eps` Data Audit URL.
- Confirm the Forecasting return calculator shows FY1-FY5 target price, price CAGR, and dividend-included CAGR, and that target price/CAGR values open `forecast.*` Data Audit URLs.
- Confirm forecast return Data Audit rows include return-specific formulas and `source_trace.calculation_inputs`; they should not reuse the EPS extraction formula.
- Confirm Research Report shows section evidence, method, confidence/quality status, and report audit facts in Data Audit.
- Confirm Research Report export links return Markdown, JSON, and Data Audit CSV with source trace fields.
- Confirm Watchlist add/remove controls update the private list.
- Confirm Fun Graphs renders line series and Data Audit includes `fun_graphs.*` rows.
- Confirm Analyst Scorecard shows 1Y/2Y hit rates and Data Audit includes `analyst_scorecard.*` rows.
- Confirm Data Audit shows Macro Series and Industry Series source ledgers.
- Confirm Screener controls update `max_per`, `min_roe`, `min_eps_cagr`, `max_debt_to_equity`, `relative_discount_pct`, and `require_roe_gt_roic` filters.
- Confirm Portfolio CSV import updates holdings and shows `import_trace`.
- Confirm `/api/v1/charts/valuation-map/AAPL.svg` and `.png` return chart images with `X-Chart-Cache-Key` and an SVG source-trace caption.
- Create a chart run and confirm `/api/v1/charts/valuation-map/runs/{chart_run_id}.svg` and `.png` replay the same settings, while the manifest includes `evidence_summary`.
- Confirm the Data Audit table shows source document, filing id, period, unit, currency, formula, and quality status.

## Runtime Boundary

Vercel Python Functions should serve read-optimized API responses. Long-running collection and parsing should run from:

- local CLI,
- GitHub Actions manual workflow,
- scheduled worker,
- or a separate ingestion service.

The ingestion worker should write append-only raw payloads and versioned normalized facts, then the API should read the normalized dataset.
