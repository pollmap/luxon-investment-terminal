import {
  fallbackCoverage,
  fallbackPriorityUniverse,
  fallbackSourceSeriesMeta
} from "./terminal-source-gate";
import type {
  IndustrySeriesRow,
  KrValuationCacheGapAuditRef,
  KrValuationCacheUniverseCoverage,
  KrValuationCacheUniverseRow,
  MacroSeriesRow,
  PriorityUniverse,
  PriorityUniverseTicker,
  SourceCoverage,
  SourceCoverageForecastCsvPreflight,
  SourceCoverageRemediationAction,
  SourceCoverageTicker,
  SourceSeriesMeta
} from "./terminal-types";

export function normalizeSourceCoverage(raw: unknown): SourceCoverage {
  if (!isRecord(raw) || !isRecord(raw.summary) || !Array.isArray(raw.tickers)) {
    return fallbackCoverage;
  }
  const tickers = raw.tickers
    .filter(isRecord)
    .map((row) => normalizeSourceCoverageTicker(row))
    .filter((row): row is SourceCoverageTicker => row !== null);
  if (!tickers.length) {
    return fallbackCoverage;
  }
  const summary = raw.summary;
  const postgres = isRecord(raw.postgres) ? raw.postgres : {};
  const requirements = normalizeSourceCoverageRequirements(raw.requirements);
  return {
    status: String(raw.status ?? "missing"),
    data_mode: String(raw.data_mode ?? "source_backed_required"),
    data_backend: typeof raw.data_backend === "string" ? raw.data_backend : undefined,
    postgres: {
      reachable: Boolean(postgres.reachable),
      error: postgres.error === null || postgres.error === undefined ? null : String(postgres.error)
    },
    requirements,
    summary: {
      tickers_expected: toNumberOrNull(summary.tickers_expected) ?? tickers.length,
      core_ready: toNumberOrNull(summary.core_ready) ?? tickers.filter((row) => row.core_ready).length,
      consensus_forecast_ready:
        toNumberOrNull(summary.consensus_forecast_ready) ??
        tickers.filter((row) => row.consensus_forecast_ready).length,
      missing_core: toStringArray(summary.missing_core),
      missing_consensus_forecast: toStringArray(summary.missing_consensus_forecast),
      missing_by_requirement: normalizeStringArrayRecord(summary.missing_by_requirement)
    },
    remediation: normalizeSourceCoverageRemediation(raw.remediation),
    local_overlays: isRecord(raw.local_overlays) ? raw.local_overlays : undefined,
    tickers
  };
}

export function normalizePriorityUniverse(raw: unknown): PriorityUniverse {
  if (isRecord(raw) && Array.isArray(raw.universes)) {
    const universes = raw.universes
      .filter(isRecord)
      .map(normalizePriorityUniverse)
      .filter((item) => item.tickers.length);
    const tickers = universes.flatMap((item) => item.tickers);
    if (!tickers.length) {
      return fallbackPriorityUniverse;
    }
    const coverage = derivePriorityRankCoverage(raw, universes);
    return {
      universe_id: String(raw.universe_id ?? fallbackPriorityUniverse.universe_id),
      label: String(raw.label ?? fallbackPriorityUniverse.label),
      market: "ALL",
      currency: "MULTI",
      data_mode: String(raw.data_mode ?? fallbackPriorityUniverse.data_mode),
      rank_coverage_status: coverage.rank_coverage_status,
      rank_count: coverage.rank_count,
      rank_limit: coverage.rank_limit,
      missing_rank_slots: coverage.missing_rank_slots,
      note: String(raw.note ?? fallbackPriorityUniverse.note),
      source_trace: isRecord(raw.source_trace) ? raw.source_trace : fallbackPriorityUniverse.source_trace,
      tickers
    };
  }
  if (!isRecord(raw) || !Array.isArray(raw.tickers) || !isRecord(raw.source_trace)) {
    return fallbackPriorityUniverse;
  }
  const tickers = raw.tickers
    .filter(isRecord)
    .map(normalizePriorityUniverseTicker)
    .filter((row): row is PriorityUniverseTicker => row !== null);
  if (!tickers.length) {
    return fallbackPriorityUniverse;
  }
  const coverage = derivePriorityRankCoverage(raw, [], tickers);
  return {
    universe_id: String(raw.universe_id ?? fallbackPriorityUniverse.universe_id),
    label: String(raw.label ?? fallbackPriorityUniverse.label),
    market: String(raw.market ?? fallbackPriorityUniverse.market),
    currency: String(raw.currency ?? fallbackPriorityUniverse.currency),
    data_mode: String(raw.data_mode ?? fallbackPriorityUniverse.data_mode),
    rank_coverage_status: coverage.rank_coverage_status,
    rank_count: coverage.rank_count,
    rank_limit: coverage.rank_limit,
    missing_rank_slots: coverage.missing_rank_slots,
    note: String(raw.note ?? fallbackPriorityUniverse.note),
    source_trace: raw.source_trace,
    tickers
  };
}

export function normalizeKrValuationCacheUniverse(raw: unknown): KrValuationCacheUniverseCoverage | null {
  if (!isRecord(raw) || !isRecord(raw.summary) || !Array.isArray(raw.rows) || !isRecord(raw.source_trace)) {
    return null;
  }
  const rows = raw.rows
    .filter(isRecord)
    .map(normalizeKrValuationCacheUniverseRow)
    .filter((row): row is KrValuationCacheUniverseRow => row !== null);
  if (!rows.length) {
    return null;
  }
  const summary = raw.summary;
  return {
    market: String(raw.market ?? "KR"),
    data_backend: String(raw.data_backend ?? "kr_valuation_input_cache"),
    data_mode: String(raw.data_mode ?? "source_backed_required"),
    coverage_status: String(raw.coverage_status ?? "missing_source_backed_cache"),
    quality_status: String(raw.quality_status ?? "missing_source_backed_data"),
    summary: {
      tickers_expected: toNumberOrNull(summary.tickers_expected) ?? rows.length,
      cache_files_found: toNumberOrNull(summary.cache_files_found) ?? rows.filter((row) => row.cache_found).length,
      valuation_ready: toNumberOrNull(summary.valuation_ready) ?? rows.filter((row) => row.valuation_ready).length,
      complete: toNumberOrNull(summary.complete) ?? rows.filter((row) => row.coverage_status === "complete").length,
      partial_source_backed:
        toNumberOrNull(summary.partial_source_backed) ??
        rows.filter((row) => row.coverage_status === "partial_source_backed").length,
      missing: toNumberOrNull(summary.missing) ?? rows.filter((row) => !row.valuation_ready).length,
      full_coverage_ready: toNumberOrNull(summary.full_coverage_ready) ?? rows.filter((row) => row.full_coverage_ready).length,
      financial_numbers_allowed:
        toNumberOrNull(summary.financial_numbers_allowed) ??
        rows.filter((row) => row.financial_numbers_allowed).length
    },
    quality_flags: toStringArray(raw.quality_flags),
    rows,
    source_trace: raw.source_trace
  };
}

export function normalizeMacroSeriesResponse(raw: unknown): { data: MacroSeriesRow[]; meta: SourceSeriesMeta } {
  if (!isRecord(raw)) {
    return { data: [], meta: fallbackSourceSeriesMeta };
  }
  return {
    data: Array.isArray(raw.data)
      ? raw.data.filter(isRecord).map(normalizeMacroSeriesRow).filter((row): row is MacroSeriesRow => row !== null)
      : [],
    meta: normalizeSourceSeriesMeta(raw.meta)
  };
}

export function normalizeIndustrySeriesResponse(raw: unknown): { data: IndustrySeriesRow[]; meta: SourceSeriesMeta } {
  if (!isRecord(raw)) {
    return { data: [], meta: fallbackSourceSeriesMeta };
  }
  return {
    data: Array.isArray(raw.data)
      ? raw.data
        .filter(isRecord)
        .map(normalizeIndustrySeriesRow)
        .filter((row): row is IndustrySeriesRow => row !== null)
      : [],
    meta: normalizeSourceSeriesMeta(raw.meta)
  };
}

function normalizeKrValuationCacheUniverseRow(raw: Record<string, unknown>): KrValuationCacheUniverseRow | null {
  const ticker = String(raw.ticker ?? "").toUpperCase();
  if (!ticker) {
    return null;
  }
  const missingYears = isRecord(raw.missing_years) ? raw.missing_years : {};
  return {
    ticker,
    cache_found: Boolean(raw.cache_found),
    valuation_ready: Boolean(raw.valuation_ready),
    financial_numbers_allowed: Boolean(raw.financial_numbers_allowed),
    full_coverage_ready: Boolean(raw.full_coverage_ready),
    coverage_status: String(raw.coverage_status ?? "missing_source_backed_cache"),
    cache_status: scalarToString(raw.cache_status),
    cache_path: scalarToString(raw.cache_path),
    cache_generated_at: scalarToString(raw.cache_generated_at),
    valuation_years: numberArray(raw.valuation_years),
    missing_years: {
      market_input: numberArray(missingYears.market_input),
      financial_metric: numberArray(missingYears.financial_metric)
    },
    market_gap_count: toNumberOrNull(raw.market_gap_count) ?? 0,
    financial_gap_count: toNumberOrNull(raw.financial_gap_count) ?? 0,
    gap_audit_refs: normalizeKrGapAuditRefs(raw.gap_audit_refs),
    rejected_cache_points: toNumberOrNull(raw.rejected_cache_points) ?? 0,
    quality_flags: toStringArray(raw.quality_flags),
    source_note: String(raw.source_note ?? "")
  };
}

function normalizeKrGapAuditRefs(value: unknown): KrValuationCacheGapAuditRef[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const refs: KrValuationCacheGapAuditRef[] = [];
  for (const item of value) {
    if (!isRecord(item)) {
      continue;
    }
    const factId = scalarToString(item.fact_id);
    if (!factId) {
      continue;
    }
    const ref: KrValuationCacheGapAuditRef = {
      scope: String(item.scope ?? "gap"),
      factId
    };
    const fiscalYear = toNumberOrNull(item.fiscal_year);
    if (fiscalYear !== null) ref.fiscalYear = fiscalYear;
    const status = scalarToString(item.status);
    if (status) ref.status = status;
    const factName = scalarToString(item.fact_name);
    if (factName) ref.factName = factName;
    const label = scalarToString(item.label);
    if (label) ref.label = label;
    const sourceDocumentId = scalarToString(item.source_document_id);
    if (sourceDocumentId) ref.sourceDocumentId = sourceDocumentId;
    const sourceType = scalarToString(item.source_type);
    if (sourceType) ref.sourceType = sourceType;
    const method = scalarToString(item.method);
    if (method) ref.method = method;
    const qualityStatus = scalarToString(item.quality_status);
    if (qualityStatus) ref.qualityStatus = qualityStatus;
    const reason = scalarToString(item.reason);
    if (reason) ref.reason = reason;
    const nextAction = scalarToString(item.next_action);
    if (nextAction) ref.nextAction = nextAction;
    refs.push(ref);
  }
  return refs;
}

function derivePriorityRankCoverage(
  raw: Record<string, unknown>,
  universes: PriorityUniverse[] = [],
  tickers: PriorityUniverseTicker[] = []
) {
  const rawRankCount = toNumberOrNull(raw.rank_count);
  const rawRankLimit = toNumberOrNull(raw.rank_limit);
  const rawMissingSlots = toNumberOrNull(raw.missing_rank_slots);
  const dataMode = String(raw.data_mode ?? "");
  const market = String(raw.market ?? "");
  const sourceBackedRankCount = tickers.filter((row) => row.rank_policy === "source_backed_latest_market_cap").length;
  const rankCount =
    rawRankCount ??
    (universes.length ? sumNumbers(universes.map((item) => item.rank_count)) : sourceBackedRankCount);
  const singleMarketDefaultLimit = ["KR", "US", "JP"].includes(market) ? 10 : Math.max(tickers.length, rankCount, 1);
  const rankLimit =
    rawRankLimit ?? (universes.length ? sumNumbers(universes.map((item) => item.rank_limit)) : singleMarketDefaultLimit);
  const missingRankSlots = rawMissingSlots ?? Math.max(0, rankLimit - rankCount);
  const rawStatus = scalarToString(raw.rank_coverage_status);
  const rankCoverageStatus =
    rawStatus ??
    (dataMode === "source_backed"
      ? missingRankSlots === 0
        ? "complete_top_market_cap_rank"
        : "partial_top_market_cap_rank"
      : "coverage_contract_only");
  return {
    rank_coverage_status: rankCoverageStatus,
    rank_count: rankCount,
    rank_limit: rankLimit,
    missing_rank_slots: missingRankSlots
  };
}

function normalizePriorityUniverseTicker(raw: Record<string, unknown>): PriorityUniverseTicker | null {
  const ticker = String(raw.ticker ?? "").toUpperCase();
  if (!ticker || !isRecord(raw.source_trace) || !raw.source_trace.source_document_id) {
    return null;
  }
  return {
    ticker,
    name: String(raw.name ?? ticker),
    market: String(raw.market ?? "KR"),
    currency: String(raw.currency ?? "KRW"),
    coverage_priority_order: toNumberOrNull(raw.coverage_priority_order) ?? 999,
    market_cap: scalarToString(raw.market_cap) ?? undefined,
    market_cap_rank: toNumberOrNull(raw.market_cap_rank) ?? undefined,
    market_cap_rank_input_date: scalarToString(raw.market_cap_rank_input_date) ?? undefined,
    rank_policy: String(raw.rank_policy ?? "not_a_live_market_cap_rank"),
    rank_coverage_status: scalarToString(raw.rank_coverage_status) ?? undefined,
    rank_count: toNumberOrNull(raw.rank_count) ?? undefined,
    rank_limit: toNumberOrNull(raw.rank_limit) ?? undefined,
    missing_rank_slots: toNumberOrNull(raw.missing_rank_slots) ?? undefined,
    source_trace: raw.source_trace
  };
}

function normalizeSourceCoverageRequirements(raw: unknown): SourceCoverage["requirements"] {
  if (!isRecord(raw)) {
    return fallbackCoverage.requirements;
  }
  return {
    min_historical_years: toNumberOrNull(raw.min_historical_years) ?? fallbackCoverage.requirements.min_historical_years,
    min_forecast_years: toNumberOrNull(raw.min_forecast_years) ?? fallbackCoverage.requirements.min_forecast_years,
    consensus_forecast_required:
      typeof raw.consensus_forecast_required === "boolean"
        ? raw.consensus_forecast_required
        : fallbackCoverage.requirements.consensus_forecast_required,
    core_required: toStringArray(raw.core_required).length
      ? toStringArray(raw.core_required)
      : fallbackCoverage.requirements.core_required,
    consensus_forecast_optional: toStringArray(raw.consensus_forecast_optional).length
      ? toStringArray(raw.consensus_forecast_optional)
      : fallbackCoverage.requirements.consensus_forecast_optional
  };
}

function normalizeSourceCoverageRemediation(raw: unknown): SourceCoverage["remediation"] {
  if (!isRecord(raw)) {
    return fallbackCoverage.remediation;
  }
  return {
    status: String(raw.status ?? "needs_source_data"),
    years: scalarToString(raw.years),
    forecast_csv_preflight: normalizeForecastCsvPreflight(raw.forecast_csv_preflight),
    next_actions: Array.isArray(raw.next_actions)
      ? raw.next_actions
        .filter(isRecord)
        .map(normalizeSourceCoverageAction)
        .filter((action): action is SourceCoverageRemediationAction => action !== null)
      : [],
    notes: toStringArray(raw.notes)
  };
}

function normalizeForecastCsvPreflight(raw: unknown): SourceCoverageForecastCsvPreflight | null {
  if (!isRecord(raw)) {
    return null;
  }
  return {
    path: String(raw.path ?? ""),
    exists: Boolean(raw.exists),
    status: String(raw.status ?? "missing_csv"),
    tickers: toStringArray(raw.tickers),
    required_periods: toNumberOrNull(raw.required_periods) ?? 0,
    covered_periods: toNumberOrNull(raw.covered_periods) ?? 0,
    missing_periods: Array.isArray(raw.missing_periods)
      ? raw.missing_periods.filter(isRecord).map((row) => ({
          ticker: String(row.ticker ?? ""),
          fiscal_year: toNumberOrNull(row.fiscal_year) ?? 0,
          estimate_cases_allowed: toStringArray(row.estimate_cases_allowed)
        }))
      : [],
    rows: toNumberOrNull(raw.rows) ?? 0,
    candidate_rows: toNumberOrNull(raw.candidate_rows) ?? 0,
    ready_rows: toNumberOrNull(raw.ready_rows) ?? 0,
    missing_value_rows: toNumberOrNull(raw.missing_value_rows) ?? 0,
    missing_trace_rows: toNumberOrNull(raw.missing_trace_rows) ?? 0,
    missing_manual_notes_rows: toNumberOrNull(raw.missing_manual_notes_rows) ?? 0,
    invalid_value_rows: toNumberOrNull(raw.invalid_value_rows) ?? 0,
    invalid_currency_rows: toNumberOrNull(raw.invalid_currency_rows) ?? 0,
    blocked_evidence_rows: toNumberOrNull(raw.blocked_evidence_rows) ?? 0,
    manual_assumption_ready_rows: toNumberOrNull(raw.manual_assumption_ready_rows) ?? 0,
    external_consensus_ready_rows: toNumberOrNull(raw.external_consensus_ready_rows) ?? 0,
    assumption_types: normalizeNumberRecord(raw.assumption_types),
    import_ready_candidate: Boolean(raw.import_ready_candidate),
    strict_validator: String(raw.strict_validator ?? ""),
    error: scalarToString(raw.error) ?? undefined
  };
}

function normalizeSourceCoverageAction(raw: Record<string, unknown>): SourceCoverageRemediationAction | null {
  const id = String(raw.id ?? "");
  if (!id) {
    return null;
  }
  return {
    id,
    priority: toNumberOrNull(raw.priority) ?? 999,
    requirements: toStringArray(raw.requirements),
    tickers: toStringArray(raw.tickers),
    description: String(raw.description ?? ""),
    cli_commands: toStringArray(raw.cli_commands),
    github_actions: isRecord(raw.github_actions) ? raw.github_actions : {}
  };
}

function normalizeSourceCoverageTicker(raw: Record<string, unknown>): SourceCoverageTicker | null {
  const ticker = String(raw.ticker ?? "").toUpperCase();
  if (!ticker) {
    return null;
  }
  return {
    ticker,
    name: String(raw.name ?? ticker),
    market: scalarToString(raw.market),
    country: scalarToString(raw.country),
    currency: scalarToString(raw.currency),
    pattern: String(raw.pattern ?? "custom"),
    status: String(raw.status ?? "missing"),
    core_ready: Boolean(raw.core_ready),
    consensus_forecast_ready: Boolean(raw.consensus_forecast_ready),
    local_consensus_overlay_ready: Boolean(raw.local_consensus_overlay_ready),
    local_consensus_overlay_source: scalarToString(raw.local_consensus_overlay_source),
    counts: normalizeNumberRecord(raw.counts),
    method_counts: normalizeNumberRecord(raw.method_counts),
    available_metric_keys: toStringArray(raw.available_metric_keys),
    missing_required: toStringArray(raw.missing_required)
  };
}

function normalizeMacroSeriesRow(raw: Record<string, unknown>): MacroSeriesRow | null {
  const seriesId = String(raw.series_id ?? "");
  const observationDate = String(raw.observation_date ?? "");
  if (!seriesId || !observationDate) {
    return null;
  }
  return {
    series_id: seriesId,
    observation_date: observationDate,
    value: String(raw.value ?? ""),
    unit: scalarToString(raw.unit),
    frequency: scalarToString(raw.frequency),
    source: String(raw.source ?? "unknown"),
    source_url: scalarToString(raw.source_url),
    source_document_id: String(raw.source_document_id ?? ""),
    source_trace: isRecord(raw.source_trace) ? raw.source_trace : {}
  };
}

function normalizeIndustrySeriesRow(raw: Record<string, unknown>): IndustrySeriesRow | null {
  const seriesId = String(raw.series_id ?? "");
  const observationDate = String(raw.observation_date ?? "");
  if (!seriesId || !observationDate) {
    return null;
  }
  return {
    market: String(raw.market ?? "unknown"),
    series_id: seriesId,
    observation_date: observationDate,
    value: String(raw.value ?? ""),
    unit: scalarToString(raw.unit),
    frequency: scalarToString(raw.frequency),
    category: String(raw.category ?? "official_statistics"),
    region: scalarToString(raw.region),
    industry: scalarToString(raw.industry),
    source: String(raw.source ?? "unknown"),
    source_url: scalarToString(raw.source_url),
    source_document_id: String(raw.source_document_id ?? ""),
    dimensions: isRecord(raw.dimensions) ? raw.dimensions : {},
    source_trace: isRecord(raw.source_trace) ? raw.source_trace : {}
  };
}

function normalizeSourceSeriesMeta(raw: unknown): SourceSeriesMeta {
  if (!isRecord(raw)) {
    return fallbackSourceSeriesMeta;
  }
  return {
    data_mode: String(raw.data_mode ?? fallbackSourceSeriesMeta.data_mode),
    quality_status: String(raw.quality_status ?? fallbackSourceSeriesMeta.quality_status),
    row_count: toNumberOrNull(raw.row_count) ?? 0,
    source_note: String(raw.source_note ?? fallbackSourceSeriesMeta.source_note),
    filters: isRecord(raw.filters) ? raw.filters : {}
  };
}

function sumNumbers(values: Array<number | undefined>): number {
  return values.reduce<number>((sum, value) => sum + (typeof value === "number" ? value : 0), 0);
}

function scalarToString(value: unknown): string | null {
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

function normalizeNumberRecord(value: unknown): Record<string, number> {
  if (!isRecord(value)) {
    return {};
  }
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, toNumberOrNull(item) ?? 0]));
}

function normalizeStringArrayRecord(value: unknown): Record<string, string[]> {
  if (!isRecord(value)) {
    return {};
  }
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, toStringArray(item)]));
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function numberArray(value: unknown): number[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => toNumberOrNull(item))
    .filter((item): item is number => item !== null);
}

function toNumberOrNull(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
