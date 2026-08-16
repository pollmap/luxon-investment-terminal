# LUXON Investment Terminal

개인 투자 판단을 위한 **KR-first, source-audited 주식 펀더멘털 터미널**입니다. FAST Graphs의 시계열 밸류에이션 문법을 중심에 두고, FnGuide형 기업 스냅샷·재무·컨센서스·비교기업 흐름을 결합합니다. 상용 서비스의 코드, 브랜딩, 이미지, 고유 UI 자산은 복제하지 않고 자체 UI와 계산 엔진으로 구현합니다.

현재 canonical 저장소는 `pollmap/luxon-investment-terminal`입니다. 공개 저장소에는 개인정보와 제3자 인증 캡처 흔적을 제외한 검수된 현재 스냅샷만 두며, 이전 프로젝트의 전체 이력은 별도 비공개 저장소에 보존합니다. 프론트엔드 설계 기준은 [Claude Design handoff](docs/CLAUDE_DESIGN_HANDOFF.md), UI/UX 합격 기준은 [Claude Design QA checklist](docs/CLAUDE_DESIGN_QA_CHECKLIST.md), 현재 화면의 측정된 기준선은 [design QA](design-qa.md), 로컬 운영 기준은 [LOCAL_OPERATIONS](docs/LOCAL_OPERATIONS.md), 기존 저장소 이관 근거는 [MIGRATION_LEDGER](docs/MIGRATION_LEDGER.md)에 기록합니다.

이 프로젝트는 FUND Stack의 `U`, 즉 **Underwriting Terminal**입니다. 전체 체계는 [docs/FUND_STACK.md](docs/FUND_STACK.md)에 정리되어 있습니다.

제품 포지셔닝은 [docs/PRODUCT_POSITIONING.md](docs/PRODUCT_POSITIONING.md)에 정리되어 있습니다. 현재 기본 운영 방식은 Windows + Docker Compose 기반의 개인용 로컬 베타이며, Vercel/Render/Neon은 선택적 확장 경로입니다.

현재 실행 우선순위는 **한국 Top 10 source-backed E2E**입니다. 첫 생산 슬라이스는 OpenDART, pykrx, FinanceData marcap, consensus CSV, source coverage를 통해 `005930.KS`, `000660.KS`, `402340.KS`, `005380.KS`, `028260.KS`, `032830.KS`, `373220.KS`, `207940.KS`, `329180.KS`, `009155.KS`를 먼저 완성합니다. US/JP와 adjusted earnings 고난도 엔진은 이 한국 슬라이스가 green이 된 뒤 확장합니다.

Current completion plan: [docs/KR_TOP10_COMPLETION_PLAN.md](docs/KR_TOP10_COMPLETION_PLAN.md)

## 원칙

- LLM은 금융 숫자를 생성하지 않습니다.
- 모든 숫자는 공시/API/XBRL/CSV/fixture 또는 deterministic formula에서만 나옵니다.
- Fixture는 `fixture_non_production`으로 명시하며 실제 투자 데이터로 취급하지 않습니다.
- Production에서는 `ALLOW_FIXTURE_FALLBACK=true`를 명시하지 않으면 fixture-backed API 응답을 차단합니다.
- 각 datapoint는 `method`, `policy`, `confidence`, `source_trace`, `quality_status`, `flags`, `formula`를 가집니다.
- 원천 데이터는 append-only raw storage, 정규화 데이터는 warehouse, 앱 상태는 SQLite/Postgres 계열로 분리합니다.

## 구조

```txt
apps/web/                 Next.js + TypeScript terminal UI
services/api/             FastAPI + Pydantic API
backend/normalize/        S1/S2/S3/S4 adjusted earnings engine
packages/valuation/       valuation map, forecast, portfolio calculation
packages/connectors/      SEC, OpenDART, EDINET, J-Quants, FRED connectors
packages/quality/         validation helpers
data/raw/                 append-only raw source payloads
data/warehouse/           DuckDB / Parquet outputs
mcp/                       local MCP JSON-RPC tool server
db/                       Alembic migration assets
storage/                  cache, parse failures, rendered charts
docs/                     formula book and data dictionary
tests/                    pytest and Playwright tests
```

## 실행

가장 짧은 로컬 실행 경로:

```powershell
Copy-Item .env.example .env.local
# .env.local의 POSTGRES_PASSWORD만 먼저 설정
.\scripts\windows\start-local.ps1
.\scripts\windows\status-local.ps1
```

브라우저에서 `http://127.0.0.1:3100/terminal?ticker=005930.KS&tab=Historical`을 엽니다. API 키가 없어도 서비스와 명시적 결측 상태는 확인할 수 있지만, 실제 KR 수집에는 `OPENDART_API_KEY`가 필요합니다.

직접 개발 환경을 구성할 때:

```powershell
pnpm install --frozen-lockfile
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
pnpm preflight
.venv\Scripts\python.exe -m pytest
pnpm build
pnpm --filter @personal-fastgraphs/web test
```

개발 서버:

```powershell
python -m uvicorn services.api.main:app --reload --port 8000
pnpm --filter @personal-fastgraphs/web dev
```

Windows 통합 실행:

```powershell
./scripts/dev.ps1
```

현재 PowerShell에서 GNU Make가 없으면 repo shim을 사용합니다. `cmd.exe`에서는 아래 명령을 그대로 실행할 수 있습니다.

```powershell
cmd /c make dev
cmd /c make stop-dev
```

PowerShell 세션에서 문자 그대로 `make dev`를 쓰고 싶으면 alias를 활성화합니다.

```powershell
. ./scripts/enable-make.ps1
make dev
make stop-dev
```

## 주요 기능

- Company Terminal: 검색, 시장, 통화, 현재가, P/E, 배당수익률, EPS CAGR, ROE, Debt/Equity
- Historical Valuation Map: 가격선, EPS/metric area, fair value, normal multiple, dividend floor, forecast area
- Performance: source-traced price return, dividend return, total return, and CAGR table
- Forecasting: Estimates, Normal Multiple, LT Growth, Historical CAGR, Custom, AI Review
- Forecast return calculator: FY1-FY5 EPS/metric, target price, price CAGR, dividend-included CAGR, dividend, and source rows with direct Data Audit links
- Consensus case matrix: low/median/high estimate cases with growth, estimate EPS, quality labels, and `forecast_snapshot.{case}.estimate_eps` audit links
- 사용자 입력: 1년부터 5년까지 직접 EPS 입력, growth rate, target P/E
- Scenario lines: 11개 valuation calculation line 표시 및 on/off
- Adjusted EPS Audit: GAAP EPS, adjusted EPS, method, confidence, source, waterfall
- Research Report: deterministic source-audited report assembled from valuation, forecast, quality, and cash-use facts
- Financials: Revenue, EPS, FCF, margins, ROE, ROIC, debt trend
- Fun Graphs: source-traced Financial Underlying Numbers line series with user-toggleable metrics
- Fiscal Fitness: source-traced profitability, cash generation, growth, solvency, and liquidity checks
- Health Check: FG Score-style 0-100 quality score with five source-traced axes
- Analyst Scorecard: source-traced 1Y/2Y estimate accuracy table, separated from forecast snapshots
- Screener: user-tunable metric-to-value, metric-to-metric, company-relative filters
- Portfolio: CSV 거래내역 import, holdings, XIRR, sector weights, buy/sell overlay
- Data Audit: fact id, source document, filing id, period, unit, currency, formula, quality status, macro/industry source ledger, and structured detail `trace_sections`

## API

- `GET /api/securities/search`
- `GET /api/company/{id}/snapshot`
- `GET /api/company/{id}/valuation-map`
- `GET /api/v1/companies/{id}/performance`
- `GET /api/company/{id}/financials`
- `GET /api/v1/companies/{id}/fun-graphs`
- `GET /api/v1/companies/{id}/research-report`
- `GET /api/v1/companies/{id}/exports/research-report.md`
- `GET /api/v1/companies/{id}/exports/research-report.json`
  - includes Data Audit rows with structured `trace_sections`
- `GET /api/v1/companies/{id}/exports/data-audit.csv`
  - includes flattened source fields plus `input_trace_keys`, `calculation_inputs_json`, and `source_trace_json`
- `GET /api/v1/companies/{id}/fiscal-fitness`
- `GET /api/v1/companies/{id}/health-check`
- `GET /api/v1/companies/{id}/use-of-cash`
- `GET /api/data-audit/{fact_id}`
  - returns the fact row plus `trace_sections`: Source evidence, Calculation, Quality, and Input traces
- `GET /api/security/{ticker}/adjusted`
- `GET /api/security/{ticker}/adjusted/waterfall`
- `GET /api/v1/companies/{id}/forecast-snapshots`
- `GET /api/v1/companies/{id}/analyst-scorecard`
- `GET /api/v1/system/readiness`
- `GET /api/v1/system/source-coverage`
- `GET /api/v1/system/priority-universe`
- `GET /api/v1/macro-series`
- `GET /api/v1/industry-series`
- `GET /api/v1/screener`
- `GET /api/v1/watchlist`
- `POST /api/v1/watchlist/items`
- `DELETE /api/v1/watchlist/items/{ticker}`
- `GET /api/v1/portfolio`
- `GET /api/v1/portfolio/sample`

Source-backed Data Audit includes direct `financial_facts.<taxonomy>.<tag>` rows
from SEC bulk warehouse loads, not only derived valuation and financials rows.
It also includes `forecast_assumption.*` rows so forecast mode, case, growth,
target multiple, manual overrides, formula, and source are visible in the audit
table and CSV/JSON exports.
Forecast-period Data Audit also includes `forecast.price_cagr_pct` and
`forecast.total_return_cagr_pct`; these rows use return-specific formulas and
calculation inputs rather than reusing the EPS extraction formula.
- `POST /api/v1/portfolio/import`
- `GET /api/v1/charts/valuation-map/{id}.svg`
- `GET /api/v1/charts/valuation-map/{id}.png`
- `POST /api/v1/charts/valuation-map/runs`
- `GET /api/v1/charts/valuation-map/runs/{chart_run_id}`
- `GET /api/v1/charts/valuation-map/runs/{chart_run_id}.svg`
- `GET /api/v1/charts/valuation-map/runs/{chart_run_id}.png`
- `GET /api/v1/chart-layouts`
- `POST /api/v1/chart-layouts`
- `DELETE /api/v1/chart-layouts/{layout_id}`

`GET /api/v1/watchlist` is the owner-scoped watchlist read path. `POST /api/v1/watchlist/items` and `DELETE /api/v1/watchlist/items/{ticker}` persist owner watchlist items when `DATA_BACKEND=postgres`. The Watchlist tab exposes add/remove controls and keeps each item tied to user-provided or source-backed trace metadata.

`GET /api/v1/portfolio` is the source-backed portfolio read path. `GET /api/v1/portfolio/sample` is demo-only and remains blocked when production fixture fallback is disabled. `POST /api/v1/portfolio/import` accepts CSV transactions, exposes `import_trace`, and persists them to Postgres when `DATA_BACKEND=postgres`. The Portfolio tab includes a CSV import form that updates holdings and chart transaction overlays.

`GET /api/v1/companies/{id}/use-of-cash` returns a source-traced capital allocation series. It computes FCF margin and dividend payout only when the required source facts exist. OCF, Capex, share repurchases, debt repayment, acquisitions, and net cash use remain `null` with explicit missing-source flags until those line items are ingested.

`GET /api/v1/companies/{id}/fiscal-fitness` returns source-traced profitability, cash generation, growth, solvency, and liquidity checks. Current ratio, quick ratio, and interest coverage remain `null` with explicit missing-source flags until balance-sheet and financing facts are normalized.

`GET /api/v1/companies/{id}/health-check` returns an FG Score-style 0-100 quality score. It derives five axes from Fiscal Fitness rows and forecast/scorecard evidence: profitability, cash generation, financial strength, growth, and predictability. Missing point-in-time consensus evidence is not inferred; the predictability axis is flagged.

`GET /api/v1/companies/{id}/research-report` returns a deterministic source-audited report. It uses valuation-map, Health Check, Fiscal Fitness, Forecast, and Use of Cash facts, and exposes report-level `audit_facts` for Data Audit. It does not use an LLM to create financial numbers.

Research exports are read-only deterministic downloads. Markdown and JSON exports package the source-audited report with Data Audit rows; CSV export serializes fact-level source traces for spreadsheet review. These endpoints do not create or infer new financial values.

`GET /api/v1/companies/{id}/fun-graphs` returns source-traced Financial Underlying Numbers series for revenue, adjusted EPS, GAAP diluted EPS, FCF, margins, ROE, ROIC, and debt/equity. The UI renders these as toggleable line graphs and exposes each point in Data Audit.

`GET /api/v1/companies/{id}/analyst-scorecard` returns a source-traced 1Y/2Y estimate accuracy table. Production rows require point-in-time `consensus_estimate_snapshots` and actual adjusted EPS overlap; fixture proxy rows remain labeled `fixture_non_production_scorecard_proxy`.

`GET /api/v1/screener` accepts `max_per`, `min_roe`, `min_eps_cagr`, `max_debt_to_equity`, `relative_discount_pct`, and `require_roe_gt_roic`. It recomputes metric-to-value, metric-to-metric, company-relative, and all-pass flags from the loaded universe.

Chart endpoints accept the same metric, forecast, and line-visibility query parameters as valuation-map, including `hidden_scenario_lines=18x,19x` for individual forecast valuation lines. They render Matplotlib SVG/PNG server-side, return `X-Chart-Cache-Key`, and cache rendered files under `storage/rendered_charts` by default. `POST /runs` snapshots the valuation payload and render settings into a replayable `chart_run_id`. Set `CHART_BLOB_QUEUE_ENABLED=true` to enqueue rendered chart artifacts for `pnpm blob:sync`.

Chart layouts are user-saved chart presets, separate from chart runs. `POST /api/v1/chart-layouts` stores the current ticker, metric, forecast mode/case, manual EPS values, line visibility, and hidden scenario line labels. With `DATA_BACKEND=postgres`, layouts persist in Neon/Postgres; without Postgres they use `CHART_LAYOUT_DIR` for local development manifests.

Private user state is owner-scoped from the signed `pf_session` cookie when API auth is enabled. The API stores a hashed owner key derived from the allowlisted email, not the raw email address. This owner key is used for chart layouts, watchlists, and portfolio transactions.

Forecasting 예시:

```powershell
curl "http://127.0.0.1:8000/api/v1/companies/AAPL/valuation-map?forecast_mode=custom&forecast_years=3&manual_eps_values=7.50,8.00,8.50&target_multiple=20"
curl "http://127.0.0.1:8000/api/v1/companies/AAPL/valuation-map?forecast_mode=ai_review"
```

When `DATA_BACKEND=postgres` has point-in-time `consensus_estimate_snapshots`,
`forecast_mode=estimates` and `forecast_mode=normal_multiple` use those source-backed
EPS snapshots before fixture forecast presets. Missing forecast years remain flagged
in `source_trace.missing_consensus_years` and are filled only by the deterministic
growth formula needed to keep the 1Y-5Y chart continuous.
The Forecasting panel exposes this as `Forecast evidence` and avoids displaying
empty source-backed ranges as fake low/median/high consensus values.
The Forecasting panel also exposes a case matrix for low/median/high estimate
EPS and a return calculator for FY1-FY5 target price, price CAGR, and
dividend-included CAGR. Each visible forecast return number and consensus
estimate links back to Data Audit using the same active valuation query.

Source coverage treats 1Y-5Y forecast readiness as valuation-ready only when
each required fiscal year has an `adjusted_operating_eps` snapshot with
`estimate_case=median` or `estimate_case=current`. Low/high scenario rows alone
do not satisfy the deploy gate because the valuation map needs a base EPS path.

## CLI

```powershell
python -m backend.normalize.cli collect-sec --ticker AAPL --years 2020:2025
python -m backend.normalize.cli normalize --ticker AAPL --policy street_comparable --years 2020:2025
python -m backend.normalize.cli inspect --ticker AAPL --year 2024
python -m backend.normalize.cli export-golden --ticker AAPL --year 2024 --out tests/golden/adjusted/aapl.json
```

실제 SEC 호출에는 `SEC_USER_AGENT`가 필요합니다.

Vercel-first ingestion worker:

```powershell
pnpm e2e:source:kr:005930:local-dry-run
pnpm doctor:kr
pnpm e2e:source:kr:check
pnpm secrets:local
pnpm e2e:source:kr:005930:check
pnpm e2e:source:kr:005930:dry-run
pnpm e2e:source:kr:005930
python -m services.ingestion_worker.cli run-source-e2e --market KR --years 2020:2025 --persist --continue-on-error --dry-run --summary-only
python -m services.ingestion_worker.cli run-source-e2e --market KR --tickers 005930.KS --years 2020:2025 --persist --continue-on-error --dry-run --summary-only
python -m services.ingestion_worker.cli run-source-e2e --market KR --years 2020:2025 --persist --continue-on-error
python -m services.ingestion_worker.cli collect --market US --ticker AAPL --years 2020:2025 --persist
python -m services.ingestion_worker.cli normalize-us --ticker AAPL --years 2020:2025 --persist
python -m services.ingestion_worker.cli normalize-us-batch --tickers AAPL,NVDA,CRM,O,JPM --years 2020:2025 --persist --continue-on-error
python -m services.ingestion_worker.cli collect --market KR --ticker 005930.KS --years 2024:2024 --persist
python -m services.ingestion_worker.cli collect --market JP --ticker 7203.T --years 2024:2024 --persist
python -m services.ingestion_worker.cli collect-fred --series DGS10,DGS2,FEDFUNDS,CPIAUCSL,UNRATE,USREC,DEXKOUS,DEXJPUS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-ecos --series "<stat_code:cycle:item_code>" --years 2024:2024 --persist
python -m services.ingestion_worker.cli collect-kosis --tables "<orgId:tblId-or-userStatsId>" --years 2024:2024 --persist
python -m services.ingestion_worker.cli collect-estat --stats-data-ids "<statsDataId>" --years 2024:2024 --persist
python -m services.ingestion_worker.cli collect-sec-bulk --archives companyfacts,submissions --persist
python -m services.ingestion_worker.cli load-sec-bulk-warehouse --tickers AAPL,NVDA,CRM,O,JPM --persist
python -m services.ingestion_worker.cli collect-stooq-prices --market US --tickers AAPL,NVDA,CRM,O,JPM --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-fdr-prices --market US --tickers AAPL,NVDA,CRM,O,JPM --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-pykrx-prices --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-marcap --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-jquants --tickers 7203.T,6758.T,6861.T,8306.T,7974.T --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-edinet --tickers 7203.T,6758.T,6861.T,8306.T,7974.T --years 2020:2025 --persist
python -m services.ingestion_worker.cli import-market-csv --path storage/imports/market_prices.csv --persist
python -m services.ingestion_worker.cli consensus-workpaper --tickers 005930.KS --csv-path storage/imports/consensus_005930.csv --template-cases median --validation-cases median,current --case-mode any --out storage/imports/consensus_005930_workpaper.md
python -m services.ingestion_worker.cli export-consensus-template --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --cases median --out storage/imports/consensus_estimates.csv
python -m services.ingestion_worker.cli validate-consensus-csv --path storage/imports/consensus_estimates.csv --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --cases median,current --case-mode any --strict
python -m services.ingestion_worker.cli import-consensus-csv --path storage/imports/consensus_estimates.csv --persist
python -m services.ingestion_worker.cli import-fnguide-export --path storage/imports/fnguide_dataguide.csv --persist
python -m services.ingestion_worker.cli source-catalog --format markdown
python -m services.ingestion_worker.cli data-lake-plan --markets US,KR,JP --years 2020:2025 --format markdown --out storage/ingestion_plans/data_lake_plan.json
python -m services.ingestion_worker.cli run-source-e2e --market US --tickers AAPL,NVDA,CRM,O,JPM --years 2020:2025 --persist --continue-on-error
python -m services.ingestion_worker.cli run-source-e2e --market US --tickers AAPL --years 2020:2025 --persist --dry-run --summary-only
python -m services.ingestion_worker.cli run-p1-e2e --years 2020:2025 --persist --continue-on-error
python -m services.ingestion_worker.cli run-p1-e2e --years 2020:2025 --persist --continue-on-error --dry-run --summary-only
python -m services.ingestion_worker.cli source-coverage --market KR --require-consensus-forecast --strict
python -m services.ingestion_worker.cli secret-audit --strict
python -m services.ingestion_worker.cli deploy-gate --markets KR --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --require-blob --require-consensus-forecast --summary-only --strict
python -m services.ingestion_worker.cli preflight --markets KR --require-blob --strict
python -m services.ingestion_worker.cli doctor --markets KR --require-blob --strict
pnpm preflight
pnpm preflight:deploy
pnpm doctor:kr
pnpm doctor:kr:deploy
pnpm e2e:source:kr:local-dry-run
pnpm e2e:source:kr:check
pnpm e2e:source:kr:dry-run
pnpm e2e:source:kr
pnpm e2e:source:us:dry-run
pnpm e2e:source:us
pnpm e2e:p1:dry-run
pnpm e2e:p1
pnpm ingest:us:mvp
pnpm data-lake:plan
pnpm collect:fred
pnpm collect:sec:bulk
pnpm load:sec:bulk
pnpm collect:kr:005930:raw
pnpm inspect:raw:kr:005930
pnpm build:valuation-inputs:kr:005930
pnpm load:valuation-warehouse:kr:005930
pnpm e2e:source:kr:top10:local-dry-run
pnpm inspect:raw:kr:top10
pnpm build:valuation-inputs:kr:top10
pnpm load:valuation-warehouse:kr:top10
pnpm readiness:kr:top10
pnpm collect:opendart:kr:005930:raw
pnpm collect:pykrx:kr:005930:raw
pnpm collect:marcap:kr:005930:raw
pnpm collect:stooq:us
pnpm collect:fdr:us
pnpm collect:pykrx:kr
pnpm collect:marcap:kr
pnpm collect:jquants:jp
pnpm collect:edinet:jp
pnpm import:fnguide
pnpm template:consensus
pnpm validate:consensus
pnpm secret:audit
pnpm deploy:gate
pnpm blob:sync:dry-run
pnpm blob:sync
pnpm smoke:api -- --base-url https://your-private-preview.vercel.app --ticker 005930.KS
pnpm smoke:api -- --base-url https://your-private-preview.vercel.app --ticker 005930.KS --require-consensus-forecast
pnpm smoke:api:kr -- --base-url https://your-private-preview.vercel.app
```

Use `pnpm e2e:source:kr:005930:local-dry-run` before Neon/Postgres and Vercel
Blob are configured. It does not persist rows, so it reports market-source
credential gaps without requiring database or Blob settings. Use `:check`,
`:dry-run`, and the non-suffixed E2E commands after persistence is configured.

`preflight`는 Vercel-first production gate입니다. `.env.example`, Vercel route, FastAPI entrypoint, GitHub Actions worker, Alembic migration head, production fixture fallback policy, private auth runtime variables, connector/Blob/Postgres 설정을 한 번에 점검합니다. `doctor`는 runtime secret과 DB/connector 설정 점검에 더 좁게 집중합니다.

`--persist`는 `DATA_BACKEND=postgres`와 `DATABASE_URL`이 있을 때 Neon/Postgres에 저장합니다. 원문 파일은 `storage/raw/**`에 저장되고 `storage/blob_queue/**`에 Blob 업로드 manifest가 쌓입니다. `pnpm secret:audit`은 저장된 Blob queue/cache/parse-failure 메타데이터에 원문 API key, token, runtime secret 값이 남아 있는지 검사하며 secret 값 자체는 출력하지 않습니다. `pnpm blob:sync:dry-run`은 토큰 없이 큐 manifest와 로컬 파일 존재 여부를 검증합니다. 실제 `pnpm blob:sync` 업로드에는 `BLOB_READ_WRITE_TOKEN`이 필요합니다.

Before running any `--persist` remediation command from `source-coverage`, set
`DATA_BACKEND=postgres` and `DATABASE_URL`. US collection also requires
`SEC_USER_AGENT`. The `source-coverage` response includes these prerequisites
under `remediation.prerequisites` so failed KR E2E gates distinguish missing
environment from missing source rows.

For local development, copy `.env.example` to `.env.local` and place private
keys there. `.env.local` is gitignored. The FastAPI app and ingestion worker
load `.env.local` automatically without overriding real process environment
variables, so CI/Vercel secrets still take precedence. Do not paste API keys
into shell commands, docs, commits, or test fixtures.

On Windows, prefer the secret-safe helper so OpenDART/FRED/ECOS keys do not end
up in shell history:

```powershell
pnpm secrets:local
```

Use `pnpm secrets:local -- --Overwrite` only when intentionally replacing an
existing local value. Add `-- --IncludeDatabase` if the same `.env.local` should
also receive `DATABASE_URL` and `DATA_BACKEND` for persisted Neon/Postgres runs.

Before Neon/Postgres is configured, use the raw-only Samsung bootstrap:

```powershell
pnpm collect:kr:005930:raw
pnpm inspect:raw:kr:005930
pnpm build:valuation-inputs:kr:005930
```

This collects `005930.KS` OpenDART financial statements, pykrx OHLCV, and
FinanceData marcap source documents into ignored local raw/cache storage
without `--persist`. OpenDART still requires `DART_API_KEY` or
`OPENDART_API_KEY` through `.env.local` or the process environment. The inspect
command checks cached market raw file hashes, row counts, date ranges, market
cap evidence, listed-share evidence, and source traces without computing
valuation metrics. The valuation-input builder promotes the year-end close,
market capitalization, listed shares, OpenDART EPS, and OpenDART financial
metrics into storage-ready `normalized_facts`. If EPS is present it emits the
first source-backed KR valuation point; if EPS is absent it remains blocked
with `missing_open_dart_metric_values`.

Both `inspect-raw-kr` and `build-kr-valuation-inputs` return a `next_actions`
array. It contains the exact next operator commands, for example
`pnpm secrets:local`, OpenDART collection, re-inspection, and valuation-input
rebuild. Secret values are never embedded in these commands; the output only
names the required secret keys.

FAST Graphs와 FnGuide는 공개 문서에서 확인되는 분석 흐름과 정보 구조만 수동으로
검토합니다. 인증 세션 자동화, 접근 제한 우회, DOM 수집, 스크래핑, 제3자
스크린샷·자산의 저장소 반입은 허용하지 않습니다. 제품은 LUXON 고유 코드,
브랜드, 데이터 계약과 계산 엔진만 사용합니다.

For the first KR Top 10 production slice, run
`python -m services.ingestion_worker.cli doctor --markets KR --strict`
before `run-source-e2e --market KR`. The strict doctor gate treats
OpenDART/DART credentials, `DATA_BACKEND=postgres`, and `DATABASE_URL` as
required operator configuration. US/JP secrets become required when those
markets are explicitly selected later.

Windows helper:

```powershell
pnpm e2e:source:kr:top10:local-dry-run
pnpm inspect:raw:kr:top10
pnpm build:valuation-inputs:kr:top10
pnpm load:valuation-warehouse:kr:top10
./scripts/run-kr-e2e.ps1 -Persist -Strict -ContinueOnError
./scripts/run-kr-e2e.ps1 -Tickers 005930.KS -Persist -Strict -ContinueOnError
./scripts/run-kr-e2e.ps1 -Persist -Strict -ContinueOnError -Execute
./scripts/run-priority-e2e.ps1 -Markets KR -Persist -Strict
./scripts/run-priority-e2e.ps1 -Markets KR -Persist -Strict -Execute
```

The KR helper runs `doctor --markets KR` first, then
`run-source-e2e --market KR`. It defaults to dry-run mode. Add `-Execute` only
when the secret-safe dry-run output shows the expected configuration and ticker
plan. The priority helper also defaults to `KR`; pass `-Markets KR,US,JP` only
after the KR slice is green.

GitHub Actions has the same first-slice path in
`.github/workflows/kr-e2e.yml` as `KR Top 10 E2E`. Use that workflow before the
broader `ingestion-worker.yml` control plane when the goal is only to close the
Korea Top 10 source-backed E2E path.

To extend that workflow into a protected Vercel API smoke, set the HTTPS
deployment URL once as the `KR_SMOKE_BASE_URL` repository variable, configure
the `PF_SESSION_COOKIE` repository secret, and set `run_api_smoke=true`.
`preview_base_url` is only an optional equality assertion and can never redirect
the cookie to a different host. The workflow then runs
`pnpm smoke:api:kr` after the source coverage and optional deploy gate checks,
so the same KR Top 10 readiness contract is verified against the deployed API.

Windows dispatch helper:

```powershell
pnpm workflow:kr:smoke -- -BaseUrl https://your-private-preview.vercel.app -PreflightOnly
pnpm workflow:kr:smoke -- -BaseUrl https://your-private-preview.vercel.app -PartialAudit -PartialTickers 005930.KS -Watch
pnpm workflow:kr:smoke -- -BaseUrl https://your-private-preview.vercel.app -Persist -ContinueOnError -RequireConsensusForecast -SyncBlob -RunDeployGate -Watch
pnpm workflow:kr:smoke -- -BaseUrl https://your-private-preview.vercel.app -RunLabel kr-smoke-001 -Watch
```

Use `-RunLabel` when concurrent KR smoke runs may exist. If omitted, the helper
generates a `kr-smoke-YYYYMMDDHHMMSS` marker and `-Watch` follows the GitHub
Actions run whose display title contains that marker.
Use `-PartialAudit` while KR Top10 is still transitioning from partial
source-backed cache coverage to the full production gate.

`run-source-e2e --market KR` is the first production orchestration command. It
collects OpenDART financial facts, pykrx prices, FinanceData marcap market
structure, builds KR valuation inputs, promotes the source-backed valuation
cache into Postgres, and runs final `source-coverage` in the required order. Use
`--dry-run` first to inspect the plan and missing prerequisites without making
network calls or writing source rows.

`run-p1-e2e` is a later cross-market P1 orchestration command for `AAPL`,
`005930.KS`, and `7203.T`. It reuses the US E2E runner, then collects KR
OpenDART, pykrx, and marcap data, JP J-Quants and EDINET data, JP Stooq prices,
and one final source coverage gate. It does not synthesize missing data; if a
market path is incomplete, the result remains `needs_source_data` with the
missing source requirements listed.

Consensus CSV import is for point-in-time forecast and scorecard evidence.
Required columns are `ticker`, `fiscal_year`, `snapshot_date`, `estimate_case`,
`estimate_eps`, `currency`, and `source`. Import-ready rows must also provide at
least one trace anchor: `source_url`, `source_document_id`, or `filing_id`.
Optional columns are `metric_key`, `period_end`, `growth_rate_pct`,
`analyst_count`, `quality_status`, and `notes`. Values without traceable
evidence are not production forecast evidence.

Explicit user forecast assumptions are allowed only when the row is tagged with
`source=manual_forecast_assumption` or
`quality_status=manual_forecast_assumption`, includes `notes` describing the
assumption basis, and still carries a trace anchor. They are stored separately
from external consensus snapshots in `source_trace.assumption_type`.

Consensus CSV dry-run validates values before persistence. It rejects unknown
`estimate_case`, non-positive `estimate_eps`, invalid dates, invalid currency
codes, negative analyst counts, non-HTTP source URLs, missing trace anchors
(`source_url`, `source_document_id`, or `filing_id`), and template or
fixture-only `quality_status` values. It also rejects FAST Graphs screenshots or
application pages, LLM-generated numbers, fixtures, demos, samples,
placeholders, and templates as numeric forecast evidence. Manual assumption rows
without `notes` are also rejected. The dry-run summary returns tickers, fiscal
years, snapshot dates, estimate case counts, source types, quality statuses, and
external-vs-manual assumption counts so the operator can inspect the import shape
before writing to Neon.

`export-consensus-template` creates a blank 1Y-5Y forecast snapshot CSV for the
selected tickers. It intentionally leaves `estimate_eps` and `source` empty, so
it cannot be imported as production evidence until the operator fills values
from traceable consensus, company guidance, or explicit manual assumptions and
adds a source URL, source document id, or filing id.

`consensus-workpaper` creates a Markdown operator checklist for the same gate. It
lists the required ticker-year rows, accepted evidence anchors, blocked evidence,
and exact export/validate/import commands. It does not generate financial
estimates.

Run `validate-consensus-csv` before import. It checks the file-level row shape
and the gate-level coverage contract: selected tickers, 1Y-5Y fiscal years, and
at least one `median` or `current` case per ticker-year. It reports missing
periods, invalid rows, duplicates, and the exact next commands without
persisting values. `pnpm validate:consensus` runs this check for the KR Top10
template path used by the deploy gate.

When `source-coverage` reports missing `consensus_forecast`, the remediation
payload also includes `forecast_csv_preflight`. This is a lightweight operator
readout for the expected CSV path: missing file, template-pending rows, filled
candidate rows, trace-anchor gaps, and the exact strict validator command. It is
not a substitute for `validate-consensus-csv`; it exists so the product gate can
show whether the local forecast evidence package is ready for strict validation.
Manual forecast assumptions are counted separately from external consensus rows
and are not preflight-ready unless the row includes operator notes explaining
the basis. The same preflight also flags non-positive or non-numeric EPS values,
invalid currency codes, and blocked evidence labels such as fixture/demo/LLM or
FAST Graphs-derived sources before the stricter validator is run.

Market CSV dry-run also validates values before writing prices or dividends. It
rejects non-positive `close_price`, negative `dividend`, invalid dates, invalid
currency codes, missing source ids, and non-HTTP source URLs. The summary returns
tickers, fiscal years, date range, price row count, dividend row count, source
types, and currencies.

FnGuide/DataGuide import is user-supplied only and never scrapes the service.
Dry-run preserves the file hash, validates value-bearing rows before writing
`metric_values`, rejects invalid KR tickers and invalid currency codes, and
returns tickers, fiscal years, metric keys, units, and currencies for operator
review. Blank-value rows are counted as skipped; malformed value-bearing rows
fail the import.

GitHub Actions 수동 worker는 `.github/workflows/ingestion-worker.yml`에 있습니다. `preflight`, `doctor`, `migrate`, `normalize_us`, `normalize_us_batch`, `collect_market`, `collect_sec_bulk`, `load_sec_bulk_warehouse`, `collect_fred`, `collect_ecos`, `collect_kosis`, `collect_estat`, `collect_stooq_prices`, `collect_pykrx_prices`, `collect_marcap`, `collect_jquants`, `collect_edinet`, `import_market_csv`, `export_consensus_template`, `import_consensus_csv`, `import_fnguide_export`, `source_coverage`, `secret_audit`, `data_lake_plan`, `blob_sync`를 Vercel 요청 경로 밖에서 실행합니다. `secret_audit`은 Blob 업로드 전에 저장된 source metadata secret 누출 여부를 점검합니다. `normalize_us_batch`는 `coverage_tickers` 입력으로 US MVP universe를 한 번에 수집/정규화합니다. `collect_sec_bulk`는 SEC 공식 `companyfacts.zip`과 `submissions.zip`을 append-only raw archive로 보존합니다. `load_sec_bulk_warehouse`는 해당 ZIP에서 핵심 us-gaap facts를 `financial_facts`와 `metric_values`에 적재합니다. `collect_fred`는 금리·물가·실업률·침체음영·환율 macro series를 `macro_series`와 `recession_periods`에 적재합니다. `collect_ecos`, `collect_kosis`, `collect_estat`은 사용자가 큐레이션한 공식 통계표 ID를 받아 KR/JP macro·industry JSON을 `source_documents`/`raw_objects`에 보존하고, parse 가능한 관측치를 `macro_series`와 `industry_series`로 승격합니다. `collect_stooq_prices`는 무료 Stooq daily CSV를 raw로 보존하고 `price_bars`에 source-backed close price를 적재합니다. `collect_marcap`은 FinanceData marcap 연도별 parquet를 append-only raw로 보존하고 선택 KR 티커의 close, 시총, 상장주식수, 순위를 `price_bars.source_trace`와 함께 적재합니다. `collect_jquants`는 JP seed universe의 daily quotes, statements, dividends JSON을 raw로 보존하고 `price_bars`, `adjusted_earnings`, `metric_values`, `dividends`에 적재합니다. `collect_edinet`은 JP annual securities report metadata와 XBRL-to-CSV/XBRL ZIP을 raw로 보존합니다. `data_lake_plan`은 대량 선적재 전에 시장·연도·소스별 작업 manifest를 생성합니다. `blob_sync`와 ingestion 후 자동 sync는 모두 `pnpm secret:audit`과 `pnpm blob:sync:dry-run`을 먼저 통과해야 업로드합니다.

`pnpm smoke:api`는 배포 URL 또는 로컬 API를 대상으로 `/api/health`, source readiness, source coverage, macro/industry series, security search, adjusted forecast valuation-map, forecast snapshots, SVG/PNG chart render를 점검합니다. Source coverage는 `source_documents`, `raw_objects`, `financial_facts` 증거를 함께 확인합니다. 보호된 배포에서는 `PF_SESSION_COOKIE` 또는 `--cookie`로 private session cookie를 전달합니다. `--require-consensus-forecast`를 추가하면 smoke가 1Y-5Y consensus snapshot readiness와 source-backed forecast evidence를 필수 조건으로 검증합니다.

`pnpm smoke:api:kr`는 첫 KR production slice 전용 smoke입니다. KR Top10
valuation cache가 10/10 valuation-ready인지 확인하고, source coverage가
Neon/Postgres production DB와 1Y-5Y forecast readiness를 10/10으로 증명해야
통과합니다.

`pnpm readiness:kr:top10`은 KR Top10 전환 상태를 빠르게 보는 운영 체크입니다.
로컬 valuation cache, partial source-backed 종목, Postgres source coverage,
다음 실행 명령을 요약합니다. Neon 적재 전 정상 상태는 로컬 proof 기준
`source_coverage_status=ready` / `production_status=local_warehouse_only`입니다.
production 완료 상태는 `production_ready`입니다.

KR Top10 전환 구간에서는 full production gate 전에 partial gap-audit smoke를
사용합니다.

```powershell
pnpm smoke:api:kr:partial -- --base-url https://your-private-preview.vercel.app --expect-kr-top10-partial-tickers 005930.KS
```

이 smoke는 최종 production gate를 느슨하게 만들지 않고,
`partial_source_backed` 종목의 `gap_audit_refs`가 Data Audit 후속 점검에 필요한
계약을 갖췄는지 검증합니다. 실패 시 `kr_partial_audit_failed`와 함께
`expected`, `partial`, `invalid_or_missing` 종목 목록을 출력합니다. 또한 각
`gap_audit_refs.fact_id`가 `/api/data-audit/{fact_id}`에서 source trace와 함께
열리는지도 확인합니다.

Source coverage gate:

```powershell
python -m services.ingestion_worker.cli load-kr-valuation-postgres --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --strict
python -m services.ingestion_worker.cli source-coverage --market KR --require-consensus-forecast --strict
```

Run `load-kr-valuation-postgres` after the KR valuation input cache is built and
before `source-coverage`. It promotes source-backed cache rows into the Neon /
Postgres API tables counted by the production gate: `adjusted_earnings`,
`metric_values`, `financial_facts`, `price_bars`, `dividends`,
`source_documents`, and `raw_objects`.

This is the deploy data gate for the KR Top 10 production slice. It only counts
source-backed Postgres coverage for securities, adjusted earnings years, price
years, valuation-ready `metric_values`, source evidence, S1/S2/S4 method counts,
and, when `--require-consensus-forecast` is set, 1Y-5Y consensus snapshots. It
does not create or infer financial values.

Combined deploy gate:

```powershell
python -m services.ingestion_worker.cli deploy-gate --markets KR --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --require-blob --require-consensus-forecast --summary-only --strict
```

This combines strict Vercel preflight and source coverage. Use it after Neon, Blob, private auth, connector secrets, source-backed rows, and 1Y-5Y forecast snapshots are configured.

## MCP

로컬 MCP-compatible JSON-RPC 서버:

```powershell
python -m mcp.valuation_server
```

제공 도구:

- `securities_search`
- `company_snapshot`
- `valuation_map`
- `adjusted_earnings`
- `financials`
- `data_audit`

## 데이터 추가

1. 개발 fixture는 `tests/fixtures/terminal/seed_universe.json`, `financial_series.csv`, `portfolio_transactions.csv`에 둡니다.
2. 원문 JSON/XBRL/CSV/HTML은 `data/raw/{market}/{source}/{identifier}/`에 append-only로 저장합니다.
3. connector는 원천 값을 `source_trace`와 함께 정규화합니다.
4. valuation engine은 정규화된 metric, price, dividend만 입력받아 deterministic formula로 계산합니다.
5. `adjusted_operating`, `diluted_eps`, `gaap_diluted_eps`, `basic_eps`, `sales_share`, `revenue_share`, `operating_cash_flow_share`, `fcf_share`, `ebitda_share`, `ebit_share`는 각기 다른 source trace와 formula를 가져야 합니다. SEC bulk loader는 reported facts + diluted shares로 source-backed `metric_values`를 계산합니다.
6. `smart_metric`은 source-backed sector rule table이 필요하며 fixture mode에서 임의 생성하지 않습니다.
7. `ffo_affo`는 REIT 전용 metric이며, production에서는 FFO/AFFO 리콘실리에이션 source가 필요합니다.
8. Restatement는 overwrite하지 않고 accession/filed_at/accepted_at 기준으로 versioning합니다.
9. Future consensus/estimate 데이터는 `consensus_estimate_snapshots`에 snapshot date 기준으로 versioning합니다. Analyst Scorecard는 과거 snapshot과 실제 adjusted EPS가 겹치는 기간만 production hit-rate로 계산합니다.

`data/raw/**`, `data/warehouse/**`, SQLite, DuckDB, Parquet 산출물은 Git에 커밋하지 않습니다. 디렉터리 구조만 `.gitkeep`으로 유지합니다.

## Vercel

Private deployment and smoke-check details are documented in `docs/DEPLOYMENT.md`.

Vercel 배포는 `apps/web`과 `/api/*` FastAPI entrypoint 기준으로 설계했습니다.

```powershell
vercel build
vercel deploy --prebuilt
```

Private access uses GitHub OAuth, an allowlisted email, and the signed FastAPI
`pf_session` cookie. Vercel environments should set at least `AUTH_SECRET`,
`AUTH_GITHUB_ID`, `AUTH_GITHUB_SECRET`, `AUTH_ALLOWED_EMAILS`,
`PF_COOKIE_SECRET`, `API_AUTH_REQUIRED=true`, and `API_AUTH_DISABLED=false`.
Production API hardening should also set explicit `API_CORS_ORIGINS`,
`API_RATE_LIMIT_ENABLED=true`, `API_RATE_LIMIT_REQUESTS`,
`API_RATE_LIMIT_WINDOW_SECONDS`, and `API_ENABLE_HSTS=true`.

현재 로컬에는 Vercel project settings가 없어 `vercel pull --yes --environment preview` 이후 재검증이 필요합니다.

## 라이선스

이 저장소는 소스 열람과 포트폴리오 검토를 위해 공개되어 있지만 오픈소스
라이선스를 부여하지 않습니다. 별도 서면 허가가 없는 복제, 수정, 재배포 및
상업적 이용 권한은 유보됩니다. 제3자 패키지와 외부 데이터에는 각 제공자의
라이선스와 이용약관이 별도로 적용됩니다.
