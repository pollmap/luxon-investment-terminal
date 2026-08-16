import {
  jpTopMarketCapPriorityTickers,
  krTopMarketCapPriorityTickers,
  krTopMarketCapUniverseNote,
  securities,
  tickers,
  usTopMarketCapPriorityTickers
} from "./terminal-config";
import { fallbackReadiness } from "./terminal-fallbacks";
import type {
  AdjustedRow,
  AnalystScorecard,
  AuditRow,
  ForecastEvidence,
  ForecastMeta,
  HealthCheck,
  KrValuationCacheCoverage,
  PriorityUniverse,
  Snapshot,
  SourceCoverage,
  SourceReadiness,
  SourceSeriesMeta
} from "./terminal-types";

export const priorityCoverageGroups = [
  {
    market: "KR",
    label: "KR priority universe",
    currency: "KRW",
    tickers: [...krTopMarketCapPriorityTickers],
    note: krTopMarketCapUniverseNote,
    pattern: "kr_top_market_cap"
  },
  {
    market: "US",
    label: "US priority universe",
    currency: "USD",
    tickers: [...usTopMarketCapPriorityTickers],
    note: "US top-market-cap priority universe. Production rank must be recomputed from source-backed market-cap rows.",
    pattern: "us_top_market_cap"
  },
  {
    market: "JP",
    label: "JP priority universe",
    currency: "JPY",
    tickers: [...jpTopMarketCapPriorityTickers],
    note: "JP top-market-cap priority universe. Production rank must be recomputed from source-backed market-cap rows.",
    pattern: "jp_top_market_cap"
  }
] as const;

export const priorityCoverageTickers = priorityCoverageGroups.flatMap((group) => group.tickers);
export const priorityCoverageTickerCsv = priorityCoverageTickers.join(",");
const priorityTickerSet = new Set<string>(priorityCoverageTickers);

export const regressionSeedTickers = tickers.filter((item) => !priorityTickerSet.has(item));

const fallbackPriorityUniverseSourceTrace = {
  source: "NEXUS_PRODUCT_PRIORITY_CONTRACT",
  source_type: "product_priority_universe_contract",
  source_document_id: "nexus-global-top-market-cap-priority-universe-v1",
  filing_id: "NEXUS-GLOBAL-TOP-MARKET-CAP-PRIORITY-V1",
  period: "initial_coverage",
  available_at: "2026-06-27T00:00:00+09:00",
  unit: "ticker_list",
  currency: "N/A",
  method: "coverage_priority_contract",
  formula:
    "coverage_priority_order is a deterministic product collection order; production market-cap rank must be recomputed from source-backed marcap or KRX market-cap rows before display as rank.",
  quality_status: "coverage_contract_not_financial_data",
  quality_flags: ["not_live_market_cap_rank", "requires_source_backed_rank_recompute"]
} satisfies Record<string, unknown>;

export const fallbackPriorityUniverse: PriorityUniverse = {
  universe_id: "global-top-market-cap-priority-v1",
  label: "KR/US/JP top-market-cap priority universes",
  market: "ALL",
  currency: "MULTI",
  data_mode: "source_backed_required",
  rank_coverage_status: "coverage_contract_only",
  rank_count: 0,
  rank_limit: 30,
  missing_rank_slots: 30,
  note: "Thirty-stock E2E priority contract. Live ranks require source-backed market-cap rows.",
  source_trace: fallbackPriorityUniverseSourceTrace,
  tickers: priorityCoverageGroups.flatMap((group) =>
    group.tickers.map((item, index) => ({
      ticker: item,
      name: securities[item]?.label ?? item,
      market: group.market,
      currency: group.currency,
      coverage_priority_order: index + 1,
      rank_policy: "not_a_live_market_cap_rank",
      rank_coverage_status: "coverage_contract_only",
      rank_count: 0,
      rank_limit: group.tickers.length,
      missing_rank_slots: group.tickers.length,
      source_trace: {
        ...fallbackPriorityUniverseSourceTrace,
        source_document_id: `nexus-${group.market.toLowerCase()}-top-market-cap-priority-universe-${group.market === "KR" ? "v2" : "v1"}`,
        filing_id: `NEXUS-${group.market}-TOP-MARKET-CAP-PRIORITY`,
        fact_id: `${group.market.toLowerCase()}-top-market-cap-priority:${item}`,
        ticker: item
      }
    }))
  )
};

export const fallbackCoverage: SourceCoverage = {
  status: "missing",
  data_mode: "source_backed_required",
  data_backend: "fixture",
  postgres: {
    reachable: false,
    error: "not_loaded"
  },
  requirements: {
    min_historical_years: 3,
    min_forecast_years: 5,
    consensus_forecast_required: true,
    core_required: ["security", "adjusted_earnings", "price_bars", "financial_metrics", "source_evidence"],
    consensus_forecast_optional: [
      "consensus_estimate_snapshots",
      "median_or_current_adjusted_operating_eps_by_forecast_year"
    ]
  },
  summary: {
    tickers_expected: priorityCoverageTickers.length,
    core_ready: 0,
    consensus_forecast_ready: 0,
    missing_core: priorityCoverageTickers,
    missing_consensus_forecast: priorityCoverageTickers,
    missing_by_requirement: {
      adjusted_earnings: priorityCoverageTickers,
      financial_metrics: priorityCoverageTickers,
      price_bars: priorityCoverageTickers,
      source_evidence: priorityCoverageTickers
    }
  },
  remediation: {
    status: "needs_source_data",
    years: "2020:2025",
    forecast_csv_preflight: null,
    next_actions: [
      {
        id: "run_priority_e2e",
        priority: 10,
        requirements: ["security", "adjusted_earnings", "financial_metrics", "price_bars", "source_evidence"],
        tickers: priorityCoverageTickers,
        description: "Run KR, US, then JP Top 10 source-backed E2E before showing production ranks.",
        cli_commands: [
          "python -m services.ingestion_worker.cli run-priority-e2e --markets KR,US,JP --years 2020:2025 --persist --continue-on-error --strict"
        ],
        github_actions: {
          workflow: "ingestion-worker.yml",
          command: "run_priority_e2e",
          priority_e2e_markets: "KR,US,JP",
          coverage_tickers: priorityCoverageTickerCsv
        }
      }
    ],
    notes: ["run actions in priority order", "KR/US/JP Top 10 requires source_trace before production display"]
  },
  tickers: priorityCoverageGroups.flatMap((group) =>
    group.tickers.map((item) => ({
      ticker: item,
      name: securities[item]?.label ?? item,
      market: group.market,
      country: group.market,
      currency: group.currency,
      pattern: group.pattern,
      status: "missing",
      core_ready: false,
      consensus_forecast_ready: false,
      counts: {
        security: 0,
        adjusted_years: 0,
        price_years: 0,
        source_documents: 0,
        raw_objects: 0,
        financial_metric_years: 0,
        financial_metric_keys: 0,
        consensus_forecast_years: 0,
        consensus_valuation_years: 0,
        consensus_snapshots: 0,
        consensus_valuation_snapshots: 0
      },
      method_counts: { s1: 0, s2: 0, s4: 0 },
      available_metric_keys: [],
      missing_required: ["security", "adjusted_earnings", "financial_metrics", "price_bars", "source_evidence"]
    }))
  )
};

export const metricCoverageAliases: Record<string, string[]> = {
  basic_eps: ["basic_eps", "reported_eps_basic"],
  diluted_eps: ["diluted_eps", "gaap_diluted_eps", "reported_eps_diluted"],
  gaap_diluted_eps: ["gaap_diluted_eps", "diluted_eps", "reported_eps_diluted"],
  operating_cash_flow_share: ["operating_cash_flow_share", "ocf_share"],
  fcf_share: ["fcf_share", "fcfe_share"],
  sales_share: ["sales_share", "revenue_share"],
  revenue_share: ["revenue_share", "sales_share"],
  ebitda_share: ["ebitda_share"],
  ebit_share: ["ebit_share"],
  ffo_affo: ["ffo_affo", "ffo_share", "affo_share"]
};

export const fallbackSourceSeriesMeta: SourceSeriesMeta = {
  data_mode: "source_backed_required",
  quality_status: "missing_source_backed_data",
  row_count: 0,
  source_note: "Source-backed macro and industry observations require Postgres ingestion.",
  filters: {}
};

export function isPriorityCoverageTicker(ticker: string) {
  return priorityTickerSet.has(ticker.toUpperCase());
}

function priorityTraceForTicker(ticker: string) {
  const normalizedTicker = ticker.toUpperCase();
  const row = fallbackPriorityUniverse.tickers.find((item) => item.ticker === normalizedTicker);
  return {
    ...(row?.source_trace ?? fallbackPriorityUniverse.source_trace),
    ticker: normalizedTicker,
    source_type: "source_backed_ingestion_required",
    method: "ingestion_required",
    formula: "No financial value displayed until market-specific source-backed rows are ingested.",
    quality_status: "missing_source_backed_data",
    quality_flags: [
      ...new Set([
        ...((row?.source_trace?.quality_flags as string[] | undefined) ?? []),
        "missing_source_backed_data",
        "financial_values_blocked"
      ])
    ]
  };
}

export function missingSnapshotForTicker(ticker: string): Snapshot {
  const normalizedTicker = ticker.toUpperCase();
  const meta = securities[normalizedTicker as keyof typeof securities];
  const trace = priorityTraceForTicker(normalizedTicker);
  return {
    ticker: normalizedTicker,
    name: meta?.label ?? normalizedTicker,
    market: meta?.market ?? "KR",
    country: meta?.market ?? "KR",
    currency: meta?.currency ?? "KRW",
    sector_policy: "default",
    current_price: "-",
    market_cap: null,
    listed_shares: null,
    per: null,
    dividend_yield: null,
    eps: "-",
    eps_cagr: null,
    roe: null,
    roic: null,
    debt_ratio: null,
    eps_method: "INGESTION_REQUIRED",
    confidence: "0",
    source_note: "source-backed ingestion required",
    source_trace: trace
  };
}

export function missingAdjustedRowsForTicker(ticker: string): AdjustedRow[] {
  const fiscalYear = new Date().getFullYear();
  return [
    {
      fiscal_year: fiscalYear,
      method: "INGESTION_REQUIRED",
      confidence: "0",
      quality_status: "missing_source_backed_data",
      gaap_eps_diluted: "-",
      adjusted_eps: "-",
      company_adjusted_eps: null,
      flags: ["missing_source_backed_data", "financial_values_blocked"],
      warnings: ["Run source-backed OpenDART/pykrx/marcap ingestion before displaying financial values."],
      source_trace: priorityTraceForTicker(ticker),
      waterfall: []
    }
  ];
}

export function missingAuditRowsForTicker(ticker: string): AuditRow[] {
  const fiscalYear = new Date().getFullYear();
  const trace = priorityTraceForTicker(ticker);
  return [
    {
      fact_id: `${ticker.toUpperCase()}-${fiscalYear}-ingestion_required`,
      fact_name: "source_coverage.ingestion_required",
      value: null,
      fiscal_year: fiscalYear,
      method: "INGESTION_REQUIRED",
      policy: "source_backed_required",
      confidence: "0",
      quality_status: "missing_source_backed_data",
      flags: ["missing_source_backed_data", "financial_values_blocked"],
      formula: "No valuation formula is run because required source-backed facts are missing.",
      source_trace: trace
    }
  ];
}

export function missingHealthCheckForTicker(ticker: string): HealthCheck {
  const fiscalYear = new Date().getFullYear();
  const trace = priorityTraceForTicker(ticker);
  const axes = ["profitability", "cash_generation", "financial_strength", "growth", "predictability"].map((axisKey) => ({
    axis_key: axisKey,
    label: axisKey
      .split("_")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" "),
    score: "",
    weight: "0",
    quality_status: "missing_source_backed_data",
    flags: ["missing_source_backed_data", "financial_values_blocked"],
    inputs: [],
    source_trace: trace
  }));
  return {
    ticker: ticker.toUpperCase(),
    fiscal_year: fiscalYear,
    overall_score: "",
    rating: "source required",
    quality_status: "missing_source_backed_data",
    flags: ["missing_source_backed_data", "financial_values_blocked"],
    axes,
    source_trace: trace
  };
}

export function missingForecastMetaForTicker(ticker: string): ForecastMeta {
  return {
    years: 0,
    mode: "source_backed_required",
    case: "source_backed_required",
    normal_multiple: {
      window_years: null,
      formula: "Not computed until source-backed valuation rows are ingested."
    },
    growth_rate_pct: "",
    target_multiple: "",
    analyst_count: null,
    source: null,
    formula: "No forecast calculation is run because required source-backed facts are missing.",
    source_trace: priorityTraceForTicker(ticker),
    manual_eps_values: [],
    calculation_lines: []
  };
}

export function missingForecastEvidenceForTicker(ticker: string): ForecastEvidence {
  const trace = priorityTraceForTicker(ticker);
  return {
    forecast_year: new Date().getFullYear() + 1,
    metric_name: "source-backed metric required",
    cases: [],
    revisions: [],
    sentiment: {
      label: "source required",
      net_revision_score_pct: "",
      up_revisions: 0,
      down_revisions: 0,
      unchanged: 0,
      quality_status: "missing_source_backed_data"
    },
    scorecard: {
      status: "missing_source_backed_data",
      rows: [],
      summary: {
        hit_rate_1y_pct: "",
        hit_rate_2y_pct: "",
        required_source: "point_in_time_consensus_snapshots"
      }
    },
    meta: {
      data_mode: "source_backed_required",
      quality_status: "missing_source_backed_data",
      source_note: "Source-backed consensus snapshots are required before forecast evidence is displayed."
    },
    source_trace: trace
  };
}

export function missingAnalystScorecardForTicker(ticker: string): AnalystScorecard {
  const trace = priorityTraceForTicker(ticker);
  return {
    ticker: ticker.toUpperCase(),
    status: "missing_source_backed_data",
    rows: [],
    summary: {
      hit_rate_1y_pct: "",
      hit_rate_2y_pct: "",
      scored_years: 0,
      required_source: "point_in_time_consensus_snapshots",
      quality_status: "missing_source_backed_data",
      flags: ["missing_source_backed_data", "financial_values_blocked"]
    },
    quality_status: "missing_source_backed_data",
    flags: ["missing_source_backed_data", "financial_values_blocked"],
    source_trace: trace
  };
}

export function isSourceBackedRequiredPayload(payload: unknown) {
  if (!isRecord(payload)) {
    return false;
  }
  const meta = isRecord(payload.meta) ? payload.meta : payload;
  return (
    meta.data_mode === "source_backed_required" ||
    meta.quality_status === "missing_source_backed_data" ||
    meta.financial_numbers_allowed === false
  );
}

export function isSourceSatisfiedMode(mode: unknown) {
  return mode === "source_backed" || mode === "source_backed_cache";
}

function isKrSourceBackedValuationBackend(backend: unknown) {
  return backend === "kr_valuation_input_cache" || backend === "kr_valuation_warehouse";
}

export function isSourceBackedCacheValuationPayload(payload: unknown) {
  if (!isRecord(payload)) {
    return false;
  }
  const meta = isRecord(payload.meta) ? payload.meta : {};
  return (
    isSourceSatisfiedMode(meta.data_mode) &&
    isKrSourceBackedValuationBackend(meta.data_backend) &&
    Array.isArray(payload.data) &&
    payload.data.length > 0 &&
    !containsNonProductionEvidence(payload)
  );
}

export function containsNonProductionEvidence(payload: unknown) {
  const stack: unknown[] = [payload];
  const seen = new Set<object>();
  while (stack.length) {
    const item = stack.pop();
    if (typeof item === "string") {
      const normalized = item.toLowerCase();
      if (
        normalized.includes("fixture") ||
        normalized.includes("non_production") ||
        normalized.includes("mock") ||
        normalized.includes("sample")
      ) {
        return true;
      }
      continue;
    }
    if (Array.isArray(item)) {
      stack.push(...item);
      continue;
    }
    if (!isRecord(item)) {
      continue;
    }
    if (seen.has(item)) {
      continue;
    }
    seen.add(item);
    for (const [key, value] of Object.entries(item)) {
      if (key === "financial_numbers_allowed" && value === false) {
        return true;
      }
      stack.push(value);
    }
  }
  return false;
}

export function isKrPriorityCoverageTicker(ticker: string) {
  return krTopMarketCapPriorityTickers.includes(ticker.toUpperCase() as typeof krTopMarketCapPriorityTickers[number]);
}

export function isUnsafePriorityFinancialPayload(ticker: string, payload: unknown) {
  return isSourceBackedRequiredPayload(payload) || (isKrPriorityCoverageTicker(ticker) && containsNonProductionEvidence(payload));
}

export function sourceRequiredReadinessForTicker(ticker: string, meta: unknown): SourceReadiness {
  const detail =
    isRecord(meta) && typeof meta.source_note === "string" && meta.source_note.trim()
      ? meta.source_note
      : "OpenDART/pykrx/marcap ingestion must run before financial values are displayed.";
  return {
    ...fallbackReadiness,
    status: "source_backed_required",
    data_backend: isRecord(meta) && typeof meta.data_backend === "string" ? meta.data_backend : "postgres_required",
    data_mode: "source_backed_required",
    checks: [
      {
        name: "priority_ticker_source_trace_gate",
        ok: false,
        required: true,
        detail
      }
    ],
    postgres: {
      ...fallbackReadiness.postgres,
      error: `${ticker.toUpperCase()}: source-backed rows not loaded`
    }
  };
}

export function sourceReadinessFromValuationPayload(
  current: SourceReadiness,
  valuationPayload: unknown
): SourceReadiness {
  if (!isRecord(valuationPayload) || !isRecord(valuationPayload.meta)) {
    return current;
  }
  const meta = valuationPayload.meta;
  if (!isSourceSatisfiedMode(meta.data_mode) || !isKrSourceBackedValuationBackend(meta.data_backend)) {
    return current;
  }
  const isWarehouse = meta.data_backend === "kr_valuation_warehouse";
  const warehouse = isRecord(meta.kr_warehouse) ? meta.kr_warehouse : {};
  const cache = isRecord(meta.kr_cache) ? meta.kr_cache : {};
  const cachePath =
    typeof meta.cache_path === "string"
      ? meta.cache_path
      : typeof cache.cache_path === "string"
        ? cache.cache_path
        : "storage/cache/kr-valuation-inputs";
  const warehousePath =
    typeof meta.warehouse_db_path === "string"
      ? meta.warehouse_db_path
      : typeof warehouse.warehouse_db_path === "string"
        ? warehouse.warehouse_db_path
        : "data/warehouse/warehouse.duckdb";
  const backend = typeof meta.data_backend === "string" ? meta.data_backend : "kr_valuation_input_cache";
  const mode = isWarehouse ? "source_backed" : "source_backed_cache";
  return {
    ...current,
    status: mode,
    data_backend: backend,
    data_mode: mode,
    checks: [
      {
        name: backend,
        ok: true,
        required: true,
        detail: isWarehouse
          ? `valuation-map rows loaded from ${warehousePath}`
          : `valuation-map rows loaded from ${cachePath}`
      },
      ...current.checks
    ]
  };
}

export function krValuationCacheCoverageFromPayload(payload: unknown): KrValuationCacheCoverage | null {
  if (!isRecord(payload) || !isRecord(payload.meta)) {
    return null;
  }
  const meta = payload.meta;
  const cache = isRecord(meta.kr_warehouse)
    ? meta.kr_warehouse
    : isRecord(meta.kr_cache)
      ? meta.kr_cache
      : isKrSourceBackedValuationBackend(meta.data_backend)
        ? meta
        : null;
  if (!cache) {
    return null;
  }
  const coverageYears = isRecord(cache.coverage_years) ? cache.coverage_years : {};
  const missingYears = isRecord(cache.missing_years) ? cache.missing_years : {};
  const derivedValuationYears = deriveValuationYearsFromPayload(payload);
  const isWarehouse = cache.data_backend === "kr_valuation_warehouse" || meta.data_backend === "kr_valuation_warehouse";
  const valuationYears = numberList(coverageYears.valuation_points);
  const priceYears = numberList(coverageYears.price);
  const marketYears = numberList(coverageYears.market_structure);
  const metricYears = numberList(coverageYears.financial_metric);
  const ready = booleanOrUndefined(cache.valuation_ready) ?? (valuationYears.length > 0 || derivedValuationYears.length > 0);
  return {
    data_backend: stringOrUndefined(cache.data_backend),
    data_mode: stringOrUndefined(cache.data_mode),
    financial_numbers_allowed: booleanOrUndefined(cache.financial_numbers_allowed),
    cache_status: stringOrUndefined(cache.cache_status),
    coverage_status: stringOrUndefined(cache.coverage_status) ?? (isWarehouse ? "warehouse_loaded" : undefined),
    full_coverage_ready: booleanOrUndefined(cache.full_coverage_ready) ?? ready,
    valuation_ready: ready,
    rejected_cache_points: numberOrUndefined(cache.rejected_cache_points),
    rejected_warehouse_rows: numberOrUndefined(cache.rejected_warehouse_rows),
    cache_path: stringOrUndefined(cache.cache_path),
    cache_paths: stringList(cache.cache_paths),
    warehouse_db_path: stringOrUndefined(cache.warehouse_db_path),
    warehouse_views: isRecord(cache.warehouse_views)
      ? Object.fromEntries(
          Object.entries(cache.warehouse_views)
            .filter(([, value]) => typeof value === "string")
            .map(([key, value]) => [key, String(value)])
        )
      : undefined,
    loaded_at: stringOrUndefined(cache.loaded_at) ?? null,
    coverage_years: {
      price: priceYears.length ? priceYears : derivedValuationYears,
      market_structure: marketYears.length ? marketYears : derivedValuationYears,
      financial_metric: metricYears.length ? metricYears : derivedValuationYears,
      valuation_points: valuationYears.length ? valuationYears : derivedValuationYears
    },
    missing_years: {
      market_input: numberList(missingYears.market_input),
      financial_metric: numberList(missingYears.financial_metric)
    },
    market_gap_diagnostics: recordList(cache.market_gap_diagnostics).map((item) => ({
      fiscal_year: numberOrUndefined(item.fiscal_year),
      status: stringOrUndefined(item.status),
      reason: stringOrUndefined(item.reason),
      next_action: stringOrUndefined(item.next_action),
      missing_price: booleanOrUndefined(item.missing_price),
      missing_market_structure: booleanOrUndefined(item.missing_market_structure),
      first_available_market_date: stringOrUndefined(item.first_available_market_date),
      pykrx_source_document_id: stringOrUndefined(item.pykrx_source_document_id),
      marcap_source_document_id: stringOrUndefined(item.marcap_source_document_id)
    })),
    financial_gap_diagnostics: recordList(cache.financial_gap_diagnostics).map((item) => ({
      fiscal_year: numberOrUndefined(item.fiscal_year),
      source_document_id: stringOrUndefined(item.source_document_id),
      filing_id: stringOrUndefined(item.filing_id),
      status: stringOrUndefined(item.status),
      reason: stringOrUndefined(item.reason),
      next_action: stringOrUndefined(item.next_action),
      opendart_status: stringOrUndefined(item.opendart_status),
      opendart_message: stringOrUndefined(item.opendart_message),
      row_count: numberOrUndefined(item.row_count)
    })),
    metric_status: isRecord(cache.metric_status) ? cache.metric_status : undefined,
    quality_flags: stringList(cache.quality_flags)
  };
}

function deriveValuationYearsFromPayload(payload: Record<string, unknown>) {
  const rows = Array.isArray(payload.data) ? payload.data.filter(isRecord) : [];
  return Array.from(
    new Set(
      rows
        .filter((row) => row.forecast_flag !== true)
        .map((row) => Number(row.fiscal_year))
        .filter((year) => Number.isFinite(year))
    )
  ).sort((a, b) => a - b);
}

function stringOrUndefined(value: unknown) {
  return typeof value === "string" ? value : undefined;
}

function booleanOrUndefined(value: unknown) {
  return typeof value === "boolean" ? value : undefined;
}

function numberOrUndefined(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function numberList(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => Number(item))
    .filter((item) => Number.isFinite(item));
}

function stringList(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => String(item))
    .filter(Boolean);
}

function recordList(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(isRecord);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
