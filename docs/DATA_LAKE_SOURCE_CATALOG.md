# Data Lake Source Catalog

## Purpose

The valuation terminal should eventually preload a broad KR/US/JP equity and
industry data lake. The guiding rule is unchanged: do not invent financial
numbers, do not silently scrape licensed premium datasets, and keep provenance
beside every derived value.

This catalog separates sources into four lanes:

- `official_open_bulk`: official bulk archive or filing source suitable for
  large scheduled downloads.
- `official_open_api`: official API suitable for date/ticker partitioned
  ingestion.
- `open_source_wrapper`: useful wrapper libraries, but raw snapshots must be
  tagged as wrapper-derived and lower priority than official filings.
- `user_supplied_premium_import`: licensed or user-exported premium files such
  as FnGuide DataGuide. These are allowed only when the user provides the file
  or license. The app must not scrape the service.

## Priority Map

| Market | First source | Second source | Breadth/fallback |
|---|---|---|---|
| US | SEC bulk `companyfacts.zip` and `submissions.zip` | SEC 8-K Ex.99.1 exhibits for adjusted EPS | Stooq/FinanceDataReader for price bootstrap, FRED for macro |
| KR | OpenDART XBRL/API | KRX public/open APIs and pykrx for prices and market breadth | FinanceDataReader, metadata-only Naver/Hankyung research links, user-supplied FnGuide CSV/Excel |
| JP | EDINET API/XBRL and J-Quants | JPX/J-Quants prices, financials, listed info, dividends | Stooq/FinanceDataReader price fallback |
| Global/Macro | FRED/ALFRED | FinanceDataReader FRED bridge | source-specific CSV imports |

## Source Registry

The executable registry lives in
`services/ingestion_worker/source_catalog.py`.

```powershell
python -m services.ingestion_worker.cli source-catalog --format markdown
python -m services.ingestion_worker.cli source-catalog --markets KR --exclude-premium
```

The registry defines:

- source id and market coverage,
- lane and priority,
- bulk download strategy,
- raw storage prefix,
- target warehouse tables,
- license/provenance notes.

## Ingestion Plan Manifest

Before downloading a large lake, generate a deterministic market/year/source
manifest:

Current product priority is KR first. AAPL/US remains useful as a regression
fixture and later US expansion target, but the first production coverage gate is
the KR top-market-cap priority universe. The rank itself must be recomputed from
source-backed KRX/marcap market-cap rows instead of treated as a permanent
hardcoded fact.

```txt
KR priority tickers:
005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS
```

```powershell
python -m services.ingestion_worker.cli data-lake-plan --markets US,KR,JP --years 2020:2025 --format markdown --out storage/ingestion_plans/data_lake_plan.json
python -m services.ingestion_worker.cli collect-sec-bulk --archives companyfacts,submissions --persist
python -m services.ingestion_worker.cli load-sec-bulk-warehouse --tickers AAPL,NVDA,CRM,O,JPM --persist
python -m services.ingestion_worker.cli collect-fred --series DGS10,DGS2,FEDFUNDS,CPIAUCSL,UNRATE,USREC,DEXKOUS,DEXJPUS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-ecos --series "<stat_code:cycle:item_code>" --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-kosis --tables "<orgId:tblId-or-userStatsId>" --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-estat --stats-data-ids "<statsDataId>" --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-stooq-prices --market US --tickers AAPL,NVDA,CRM,O,JPM --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-fdr-prices --market US --tickers AAPL,NVDA,CRM,O,JPM --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-pykrx-prices --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-marcap --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-research-metadata --market KR --sources naver,hankyung --tickers 005930.KS,000660.KS,402340.KS,005380.KS,028260.KS,032830.KS,373220.KS,207940.KS,329180.KS,009155.KS --years 2020:2025 --persist --continue-on-error
python -m services.ingestion_worker.cli collect-jquants --tickers 7203.T,6758.T,6861.T,8306.T,7974.T --years 2020:2025 --persist
python -m services.ingestion_worker.cli collect-edinet --tickers 7203.T,6758.T,6861.T,8306.T,7974.T --years 2020:2025 --persist
python -m services.ingestion_worker.cli import-fnguide-export --path storage/imports/fnguide_dataguide.csv --persist
pnpm data-lake:plan
pnpm collect:sec:bulk
pnpm load:sec:bulk
pnpm collect:fred
pnpm collect:stooq:us
pnpm collect:fdr:us
pnpm collect:pykrx:kr
pnpm collect:marcap:kr
pnpm collect:jquants:jp
pnpm collect:edinet:jp
pnpm import:fnguide
```

The manifest is intentionally a planning artifact, not a downloader. It lists:

- source id and lane,
- market and year partition,
- raw storage prefix,
- warehouse target tables,
- executable command when the worker already supports it,
- manual/import-specific jobs when licensing or source-specific handling is
  required.

This prevents uncontrolled bulk ingestion. The operator can review planned SEC,
OpenDART, KRX, EDINET, J-Quants, FRED, Stooq, pykrx, FinanceDataReader, and
FnGuide/DataGuide jobs before running the actual download/import workers.

## Storage Layout

Raw storage remains append-only:

```txt
storage/raw/sec_bulk/BULK/
storage/raw/sec/exhibits/
storage/raw/opendart/
storage/raw/krx_public/
storage/raw/pykrx/
storage/raw/marcap/
storage/raw/naver_search_research/
storage/raw/hankyung_consensus_metadata/
storage/raw/finance_data_reader/
storage/raw/jquants/
storage/raw/edinet/
storage/raw/fred/
storage/raw/ecos/
storage/raw/kosis/
storage/raw/estat/
storage/raw/stooq/
storage/raw/fnguide/
```

Warehouse targets:

```txt
source_documents
raw_objects
companies
securities
price_bars
financial_facts
metric_values
adjusted_earnings
adjustments
dividends
consensus_estimate_snapshots
macro_series
recession_periods
industry_series
market_microstructure
```

## FnGuide / DataGuide Policy

FnGuide DataGuide is valuable for Korea and industry datasets, but it is a
premium Excel-based service. It should be treated as:

- `user_supplied_premium_import`,
- never scraped,
- never mixed into official-source facts without `source_type=fnguide_user_export`,
- stored from user-exported Excel/CSV only,
- traceable to local file hash, import timestamp, sheet name, and row/column
  coordinates.
- imported only through `import-fnguide-export`, which preserves the raw file and
  loads canonical per-security rows into `metric_values`.

Expected canonical columns are flexible and may use English or Korean headers:

```txt
ticker / 종목코드
name / 종목명
fiscal_year / 회계연도
fiscal_period / 분기
metric_key / 계정명 / 지표
value / 금액 / 수치
unit / 단위
currency / 통화
```

This keeps the project useful for personal research without turning premium
vendor data into an unlicensed redistribution path.

## Bulk Ingestion Phases

### Phase A: US Lake

1. Download SEC bulk `companyfacts.zip` and `submissions.zip` with `collect-sec-bulk`.
2. Store both ZIP archives as content-hashed append-only raw objects.
3. Build ticker to CIK index.
4. Load selected us-gaap facts with `load-sec-bulk-warehouse`.
5. Normalize annual/quarterly XBRL facts into `financial_facts`.
6. Promote valuation-ready FY facts into `metric_values`.
7. Fetch 8-K Item 2.02 Ex.99.1 exhibits for MVP and high-coverage universe.
8. Parse adjusted EPS waterfalls into `adjusted_earnings` and `adjustments`.

### Phase B: KR Lake

1. Download OpenDART corp code universe.
2. Pull annual and quarterly XBRL/API statements for KOSPI/KOSDAQ/KONEX.
3. Pull KRX/public-data price bars, market cap, listed shares.
4. Run pykrx daily OHLCV bootstrap for KR seed tickers, preserving raw CSV and
   loading source-backed closes into `price_bars`.
5. Run FinanceData marcap yearly parquet bootstrap for KR seed tickers,
   preserving full raw archives and loading close price, market cap, listed
   shares, and rank evidence into `price_bars.source_trace`.
6. Run FinanceDataReader as a wrapper-derived fallback for listings/prices when
   official source coverage is incomplete; keep the raw CSV and mark
   `quality_status=wrapper_derived_price`.
7. Add pykrx breadth jobs for short selling, foreign ownership, ETF, and market
   fundamentals where useful.
8. Collect metadata-only Naver/Hankyung research links for Data Audit and
   research context. These documents stay in `source_documents` and
   `raw_objects`; snippets, report bodies, target prices, and consensus values
   are not promoted to financial facts.
9. Add optional FnGuide user-export importer for licensed local files.

### Phase C: JP Lake

1. Run `collect-edinet` to scan EDINET submission dates and preserve annual
   securities report metadata plus XBRL-to-CSV/XBRL ZIP bundles.
2. Run `collect-jquants` for the JP seed universe, preserving daily quotes,
   statements, and dividend JSON in raw storage while loading adjusted closes,
   market-standard EPS/statement metrics, and per-share dividends.
3. Join EDINET code, local code, ticker, company name, exchange, and sector.

### Phase D: Macro / Industry

1. Curate FRED/ALFRED series for rates, inflation, FX, spreads, recession
   shading, commodities, and sector context.
2. Curate Korea ECOS/KOSIS and Japan e-Stat statistical table IDs before
   running `collect-ecos`, `collect-kosis`, or `collect-estat`.
3. Preserve ECOS/KOSIS/e-Stat API responses in raw storage, then promote
   parseable observations into `macro_series` and `industry_series` with the raw
   row and dimensions in source trace.
4. Add table-specific industry/region dimension mappings only when source
   provenance, license, and official table definitions are clear.

## Minimum Gate Before Production

For each production ticker:

- security row exists,
- price history exists,
- adjusted or market-standard earnings history exists,
- source evidence exists,
- 1Y-5Y forecast snapshot exists when forecast gate is required,
- source document/raw object can be audited,
- vendor/manual rows are explicitly separated from official filing rows.

Use:

```powershell
pnpm ingest:us:mvp
pnpm deploy:gate
```

## References

- SEC EDGAR APIs and nightly bulk ZIP files:
  https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- OpenDART API:
  https://engopendart.fss.or.kr/intro/main.do
- KRX public stock price API through Korea public data portal:
  https://www.data.go.kr/en/data/15094808/openapi.do
- KRX Open API:
  https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S1.cmd
- J-Quants API:
  https://www.jpx.co.jp/english/markets/other-data-services/j-quants-api/index.html
- EDINET API v2 endpoint:
  https://api.edinet-fsa.go.jp/api/v2/documents.json
- FRED API:
  https://fred.stlouisfed.org/docs/api/fred/overview.html
- Bank of Korea ECOS API:
  https://ecos.bok.or.kr/api/#/
- KOSIS OpenAPI:
  https://kosis.kr/openapi/devGuide/devGuide_0201List.do
- Japan e-Stat API:
  https://www.e-stat.go.jp/api/
- FinanceDataReader:
  https://github.com/financedata/financedatareader
- FinanceData marcap:
  https://github.com/financedata/marcap
- pykrx:
  https://github.com/sharebook-kr/pykrx
- Stooq historical data:
  https://stooq.com/db/h/
- FnGuide DataGuide:
  https://dataguide.fnguide.com/
