export type ValuationRow = {
  fiscal_year: number;
  metric: string;
  price: string;
  dividend: string;
  yoy: string | null;
  normal_multiple: string | null;
  fair_multiple: string;
  fair_value_price: string;
  forecast_flag: boolean;
  source_trace?: Record<string, unknown>;
  forecast_source?: string;
  price_cagr_pct?: string;
  total_return_cagr_pct?: string;
  margin_of_safety_pct?: string;
};

export type ChartReturnSelection = {
  startYear: number;
  endYear: number;
  years: number;
  startPrice: number;
  endPrice: number;
  dividends: number;
  priceReturnPct: number;
  totalReturnPct: number;
  annualizedPriceReturnPct: number;
  annualizedTotalReturnPct: number;
};

export type RecessionBand = {
  series_id: string;
  start_date: string;
  end_date: string | null;
  source: string;
  source_trace?: Record<string, unknown>;
};

export type PricePoint = {
  date: string;
  fiscal_year: number;
  close_price: string;
  currency: string | null;
  frequency: string | null;
  source_trace: Record<string, unknown>;
};

export type AdjustedRow = {
  fiscal_year: number;
  method: string;
  confidence: string;
  quality_status: string;
  gaap_eps_diluted: string;
  adjusted_eps: string;
  company_adjusted_eps: string | null;
  flags: string[];
  warnings: string[];
  source_trace: { source_url?: string; accession_number?: string; source_type?: string };
  waterfall: Array<{
    label: string;
    category: string;
    pretax_amount: string | null;
    tax_effect: string | null;
    after_tax_impact: string | null;
    eps_impact: string | null;
    included_by_policy: boolean;
    recurring: boolean;
  }>;
};

export type Snapshot = {
  ticker: string;
  name: string;
  market: string;
  country: string;
  currency: string;
  sector_policy?: string;
  current_price: string;
  market_cap: string | null;
  listed_shares: string | null;
  per: string | null;
  dividend_yield: string | null;
  eps: string;
  eps_cagr: string | null;
  roe: string | null;
  roic: string | null;
  debt_ratio: string | null;
  eps_method: string;
  confidence: string | null;
  source_note: string;
  source_trace?: Record<string, unknown>;
};

export type FinancialRow = {
  fiscal_year: number;
  revenue: string | null;
  eps: string | null;
  gaap_eps_diluted?: string | null;
  fcf: string | null;
  gross_margin: string | null;
  operating_margin: string | null;
  net_margin: string | null;
  roe: string | null;
  roic: string | null;
  debt_to_equity: string | null;
  method: string;
  confidence: string | null;
  source_trace?: Record<string, unknown>;
};

export type FunGraphPoint = {
  fiscal_year: number;
  value: string | null;
  method: string;
  confidence: string | null;
  quality_status: string;
  flags: string[];
  source_trace?: Record<string, unknown>;
};

export type FunGraphMetric = {
  metric_key: string;
  label: string;
  unit: string;
  statement: string;
  formula: string;
  points: FunGraphPoint[];
  quality_status: string;
  flags: string[];
};

export type FunGraphsSummary = {
  latest_year: number | null;
  metric_count: number;
  point_count: number;
  quality_status: string;
  flags: string[];
};

export type FunGraphs = {
  ticker: string;
  currency: string;
  metrics: FunGraphMetric[];
  summary: FunGraphsSummary;
  source_trace?: Record<string, unknown>;
};

export type UseOfCashRow = {
  fiscal_year: number;
  revenue: string | null;
  operating_cash_flow: string | null;
  free_cash_flow: string | null;
  fcf_margin_pct: string | null;
  dividend_per_share: string | null;
  dividends_paid: string | null;
  eps: string | null;
  dividend_payout_pct: string | null;
  capex: string | null;
  share_repurchases: string | null;
  debt_repayment: string | null;
  acquisitions: string | null;
  net_cash_use: string | null;
  debt_to_equity: string | null;
  method: string;
  confidence: string | null;
  quality_status: string;
  flags: string[];
  source_trace?: Record<string, unknown>;
};

export type FiscalFitnessRow = {
  fiscal_year: number;
  metric_key: string;
  label: string;
  category: string;
  value: string | null;
  unit: string;
  direction: string;
  method: string;
  confidence: string | null;
  quality_status: string;
  flags: string[];
  source_trace?: Record<string, unknown>;
};

export type HealthCheckAxis = {
  axis_key: string;
  label: string;
  score: string;
  weight: string;
  quality_status: string;
  flags: string[];
  inputs: Array<{
    metric_key: string;
    label: string;
    value: string | null;
    score: string | null;
    quality_status: string | null;
    flags: string[];
    source_trace?: Record<string, unknown>;
  }>;
  source_trace?: Record<string, unknown>;
};

export type HealthCheck = {
  ticker: string;
  fiscal_year: number;
  overall_score: string;
  rating: string;
  quality_status: string;
  flags: string[];
  axes: HealthCheckAxis[];
  source_trace?: Record<string, unknown>;
};

export type ResearchReportEvidence = {
  label: string;
  value: string | number | null;
  unit: string;
  source_trace?: Record<string, unknown>;
};

export type ResearchReportSection = {
  section_key: string;
  title: string;
  verdict: string;
  bullets: string[];
  evidence: ResearchReportEvidence[];
  flags: string[];
  quality_status: string;
  source_trace?: Record<string, unknown>;
};

export type ResearchReport = {
  ticker: string;
  title: string;
  fiscal_year: number | null;
  data_mode: string;
  executive_summary: string[];
  sections: ResearchReportSection[];
  audit_facts: Array<{
    fact_name: string;
    value: string | number | null;
    fiscal_year: number;
    source_trace?: Record<string, unknown>;
  }>;
  flags: string[];
  quality_status: string;
  source_trace?: Record<string, unknown>;
};

export type ResearchMetadataItem = {
  source: string;
  source_label: string;
  ticker: string;
  identifier: string;
  title: string;
  link: string;
  description: string;
  source_url: string;
  source_document_id: string;
  content_hash: string;
  content_type: string;
  item_count: number;
  financial_numbers_allowed: boolean;
  terms_note: string;
  source_note: string;
  source_trace?: Record<string, unknown>;
};

export type ResearchMetadata = {
  ticker: string;
  data_mode: string;
  policy: string;
  quality_status: string;
  items: ResearchMetadataItem[];
  source_trace?: Record<string, unknown>;
  meta?: Record<string, unknown>;
};

export type ChartEvidenceSummary = {
  metric?: string;
  metric_label?: string;
  data_mode?: string;
  data_backend?: string;
  methods?: string[];
  sources?: string[];
  quality_statuses?: string[];
  source_document_count?: number;
  filing_count?: number;
  actual_periods?: number;
  forecast_periods?: number;
  latest_available_at?: string | null;
  source_trace_rows?: number;
  row_count?: number;
};

export type PerformanceRow = {
  start_year: number;
  end_year: number;
  years: number;
  start_price: string;
  end_price: string;
  shares_purchased: string;
  initial_investment: string;
  ending_value: string;
  dividends_received: string;
  reinvested_shares: string;
  reinvested_dividends: string;
  reinvested_ending_value: string;
  capital_gain: string;
  total_gain: string;
  reinvested_total_gain: string;
  price_return_pct: string;
  dividend_return_pct: string;
  total_return_pct: string;
  reinvested_total_return_pct: string;
  annualized_price_return_pct: string;
  annualized_total_return_pct: string;
  reinvested_annualized_total_return_pct: string;
  quality_status: string;
  flags: string[];
  source_trace?: Record<string, unknown>;
};

export type PerformanceSummary = {
  ticker: string;
  currency: string;
  initial_investment: string;
  rows: PerformanceRow[];
  summary: Record<string, string | number | null>;
  quality_status: string;
  flags: string[];
  source_trace?: Record<string, unknown>;
};

export type ScreenerRow = {
  ticker: string;
  name: string;
  market: string;
  currency: string;
  market_cap?: string | null;
  market_cap_usd?: string | null;
  listed_shares?: string | null;
  per: string;
  normal_pe: string;
  roe: string;
  roic: string;
  eps_cagr: string;
  debt_to_equity: string;
  filters: {
    metric_to_value: boolean;
    metric_to_metric: boolean;
    company_relative: boolean;
    passes_all?: boolean;
  };
  filter_reasons?: string[];
  source_trace?: Record<string, unknown>;
};

export type PortfolioSummary = {
  as_of: string;
  total_market_value: string;
  xirr: string | null;
  sector_weights: Record<string, string>;
  import_trace?: Record<string, unknown>;
  source_trace?: Record<string, unknown>;
  holdings: Array<{
    ticker: string;
    quantity: string;
    average_cost: string;
    latest_price: string;
    market_value: string;
    unrealized_pnl: string;
    weight_pct: string;
    sector: string;
    currency: string;
    source_trace?: Record<string, unknown>;
    transactions: PortfolioTransactionView[];
  }>;
};

export type PortfolioTransactionView = { date: string; side: string; quantity: string; price: string };

export type WatchlistSummary = {
  id: string;
  name: string;
  owner_key: string;
  items: Array<{
    ticker: string;
    name: string;
    market: string | null;
    country: string | null;
    currency: string | null;
    current_price: string | null;
    per: string | null;
    dividend_yield: string | null;
    eps_cagr: string | null;
    quality_status: string | null;
    note: string | null;
    source_trace?: Record<string, unknown>;
  }>;
  source_trace?: Record<string, unknown>;
};

export type AuditRow = {
  fact_id: string;
  fact_name?: string;
  value?: string | null;
  fiscal_year: number;
  method: string;
  policy: string;
  confidence: string;
  quality_status: string;
  flags: string[];
  formula: string | null;
  source_trace: {
    source?: string;
    source_document_id?: string;
    source_type?: string;
    accession_number?: string;
    filing_id?: string;
    available_at?: string;
    period?: string;
    unit?: string;
    currency?: string;
    method?: string;
    formula?: string;
    quality_status?: string;
    source_url?: string;
  };
};

export type AskNarrative = {
  verdict: string;
  bullets: string[];
  evidence: Array<{
    label: string;
    value: string;
    method: string;
    quality: string;
    href?: string;
  }>;
};

export type AskConsensusEvidence = {
  caseLabel: string;
  fiscalYear: number;
  estimateEps: string;
  growthRatePct: string;
  analystCount: number | null;
  method: string;
  quality: string;
  href?: string;
};

export type ForecastAiReviewNote = {
  label: string;
  value: string;
  detail: string;
  method: string;
  quality: string;
  href?: string;
};

export type LineVisibility = Record<"price" | "metricArea" | "fairValue" | "normalMultiple" | "currentValuation" | "customValuation" | "dividendFloor" | "payoutRatio" | "dividendYield" | "recessionBands" | "forecast" | "scenarioLines", boolean>;

export type AskInstruction = {
  tab: string;
  ticker?: string;
  forecastMode?: string;
  forecastCase?: string;
  forecastYears?: number;
  growth?: number;
  targetMultiple?: number;
  manualEpsValues?: string[];
  visibility?: Partial<LineVisibility>;
  applied: string[];
};

export type ChartLayoutConfig = {
  company_id: string;
  metric: string;
  forecast_mode: string;
  forecast_case: string;
  forecast_years: number;
  start_year: number | null;
  end_year: number | null;
  normal_multiple_years: number | null;
  user_growth_rate: string | null;
  target_multiple: string | null;
  manual_eps_values: string;
  visibility: {
    price: boolean;
    metric_area: boolean;
    fair_value: boolean;
    normal_multiple: boolean;
    current_valuation: boolean;
    custom_valuation: boolean;
    dividend_floor: boolean;
    payout_ratio: boolean;
    dividend_yield: boolean;
    recession_bands: boolean;
    forecast: boolean;
    scenario_lines: boolean;
  };
  hidden_scenario_lines: string[];
};

export type ChartLayout = {
  id: string;
  owner_key: string;
  name: string;
  ticker: string;
  metric: string;
  config: ChartLayoutConfig;
  source_trace?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
};

export type ForecastCalculationLine = {
  multiple: string;
  label: string;
  points: Array<{ fiscal_year: number; target_price: string }>;
};

export type ForecastMeta = {
  years: number;
  mode: string;
  case?: string;
  normal_multiple?: {
    window_years: number | null;
    formula: string;
  };
  growth_rate_pct: string;
  target_multiple: string;
  analyst_count: number | null;
  source: string | null;
  formula: string;
  source_trace?: Record<string, unknown>;
  consensus?: {
    case: string;
    selected_growth_rate_pct: string;
    low_growth_rate_pct: string | null;
    median_growth_rate_pct: string | null;
    high_growth_rate_pct: string | null;
    lt_growth_rate_pct: string | null;
    analyst_count: number;
    quality_status: string;
    revision_status: string;
    missing_years?: number[];
    source_note: string;
  };
  manual_eps_values: Array<string | null>;
  calculation_lines: ForecastCalculationLine[];
};

export type ForecastEvidence = {
  forecast_year: number;
  metric_name: string;
  cases: Array<{
    case: string;
    growth_rate_pct: string;
    estimate_eps: string;
    source_trace?: Record<string, unknown>;
  }>;
  revisions: Array<{
    as_of_label: string;
    age_months: number;
    estimate_eps: string;
    analyst_count: number;
    revision_delta_pct: string | null;
    quality_status: string;
  }>;
  sentiment: {
    label: string;
    net_revision_score_pct: string;
    up_revisions: number;
    down_revisions: number;
    unchanged: number;
    quality_status: string;
  };
  scorecard: {
    status: string;
    rows: Array<{
      fiscal_year: number;
      actual_eps: string;
      estimate_1y_prior: string;
      estimate_2y_prior: string;
      error_1y_pct: string;
      error_2y_pct: string;
      result_1y: string;
      result_2y: string;
      quality_status: string;
    }>;
    summary: {
      hit_rate_1y_pct: string;
      hit_rate_2y_pct: string;
      required_source: string;
    };
  };
  meta?: {
    data_mode: string;
    quality_status: string;
    source_note: string;
  };
  source_trace?: Record<string, unknown>;
};

export type AnalystScorecardRow = {
  fiscal_year: number;
  actual_eps: string | null;
  estimate_1y_prior: string | null;
  estimate_2y_prior: string | null;
  error_1y_pct: string | null;
  error_2y_pct: string | null;
  result_1y: string;
  result_2y: string;
  quality_status: string;
  flags: string[];
  source_trace?: Record<string, unknown>;
};

export type AnalystScorecard = {
  ticker: string;
  status: string;
  rows: AnalystScorecardRow[];
  summary: {
    hit_rate_1y_pct: string;
    hit_rate_2y_pct: string;
    scored_years: number;
    required_source: string;
    quality_status: string;
    flags: string[];
  };
  quality_status: string;
  flags: string[];
  source_trace?: Record<string, unknown>;
};

export type OwnerSession = {
  loading: boolean;
  auth_required: boolean;
  authenticated: boolean;
  email: string | null;
};

export type SourceReadiness = {
  status: string;
  data_backend: string;
  data_mode: string;
  checks: Array<{
    name: string;
    ok: boolean;
    required: boolean;
    detail: string;
  }>;
  postgres: {
    reachable: boolean;
    counts: Record<string, number>;
    error: string | null;
  };
};

export type KrValuationCacheCoverage = {
  data_backend?: string;
  data_mode?: string;
  financial_numbers_allowed?: boolean;
  cache_status?: string;
  coverage_status?: string;
  full_coverage_ready?: boolean;
  valuation_ready?: boolean;
  rejected_cache_points?: number;
  rejected_warehouse_rows?: number;
  cache_path?: string;
  cache_paths?: string[];
  warehouse_db_path?: string;
  warehouse_views?: Record<string, string>;
  loaded_at?: string | null;
  coverage_years: {
    price: number[];
    market_structure: number[];
    financial_metric: number[];
    valuation_points: number[];
  };
  missing_years: {
    market_input: number[];
    financial_metric: number[];
  };
  market_gap_diagnostics: Array<{
    fiscal_year?: number;
    status?: string;
    reason?: string;
    next_action?: string;
    missing_price?: boolean;
    missing_market_structure?: boolean;
    first_available_market_date?: string;
    pykrx_source_document_id?: string;
    marcap_source_document_id?: string;
  }>;
  financial_gap_diagnostics: Array<{
    fiscal_year?: number;
    source_document_id?: string;
    filing_id?: string;
    status?: string;
    reason?: string;
    next_action?: string;
    opendart_status?: string;
    opendart_message?: string;
    row_count?: number;
  }>;
  metric_status?: Record<string, unknown>;
  quality_flags: string[];
};

export type KrValuationCacheUniverseRow = {
  ticker: string;
  cache_found: boolean;
  valuation_ready: boolean;
  financial_numbers_allowed: boolean;
  full_coverage_ready: boolean;
  coverage_status: string;
  cache_status?: string | null;
  cache_path?: string | null;
  cache_generated_at?: string | null;
  valuation_years: number[];
  missing_years: {
    market_input: number[];
    financial_metric: number[];
  };
  market_gap_count: number;
  financial_gap_count: number;
  gap_audit_refs: KrValuationCacheGapAuditRef[];
  rejected_cache_points: number;
  quality_flags: string[];
  source_note: string;
};

export type KrValuationCacheGapAuditRef = {
  scope: string;
  fiscalYear?: number;
  status?: string;
  factName?: string;
  factId: string;
  label?: string;
  sourceDocumentId?: string | null;
  sourceType?: string;
  method?: string;
  qualityStatus?: string;
  reason?: string;
  nextAction?: string;
};

export type KrValuationCacheUniverseCoverage = {
  market: string;
  data_backend: string;
  data_mode: string;
  coverage_status: string;
  quality_status: string;
  summary: {
    tickers_expected: number;
    cache_files_found: number;
    valuation_ready: number;
    complete: number;
    partial_source_backed: number;
    missing: number;
    full_coverage_ready: number;
    financial_numbers_allowed: number;
  };
  quality_flags: string[];
  rows: KrValuationCacheUniverseRow[];
  source_trace: Record<string, unknown>;
};

export type SourceCoverageTicker = {
  ticker: string;
  name: string;
  market: string | null;
  country: string | null;
  currency: string | null;
  pattern: string;
  status: string;
  core_ready: boolean;
  consensus_forecast_ready: boolean;
  local_consensus_overlay_ready?: boolean;
  local_consensus_overlay_source?: string | null;
  counts: Record<string, number>;
  method_counts: Record<string, number>;
  available_metric_keys: string[];
  missing_required: string[];
};

export type SourceCoverageRemediationAction = {
  id: string;
  priority: number;
  requirements: string[];
  tickers: string[];
  description: string;
  cli_commands: string[];
  github_actions: Record<string, unknown>;
};

export type SourceCoverageForecastCsvPreflight = {
  path: string;
  exists: boolean;
  status: string;
  tickers: string[];
  required_periods: number;
  covered_periods: number;
  missing_periods: {
    ticker: string;
    fiscal_year: number;
    estimate_cases_allowed: string[];
  }[];
  rows: number;
  candidate_rows: number;
  ready_rows: number;
  missing_value_rows: number;
  missing_trace_rows: number;
  missing_manual_notes_rows: number;
  invalid_value_rows: number;
  invalid_currency_rows: number;
  blocked_evidence_rows: number;
  manual_assumption_ready_rows: number;
  external_consensus_ready_rows: number;
  assumption_types: Record<string, number>;
  import_ready_candidate: boolean;
  strict_validator: string;
  error?: string;
};

export type SourceCoverage = {
  status: string;
  data_mode: string;
  data_backend?: string;
  postgres: {
    reachable: boolean;
    error: string | null;
  };
  requirements: {
    min_historical_years: number;
    min_forecast_years: number;
    consensus_forecast_required: boolean;
    core_required: string[];
    consensus_forecast_optional: string[];
  };
  summary: {
    tickers_expected: number;
    core_ready: number;
    consensus_forecast_ready: number;
    missing_core: string[];
    missing_consensus_forecast: string[];
    missing_by_requirement: Record<string, string[]>;
  };
  remediation: {
    status: string;
    years: string | null;
    forecast_csv_preflight: SourceCoverageForecastCsvPreflight | null;
    next_actions: SourceCoverageRemediationAction[];
    notes: string[];
  };
  local_overlays?: Record<string, unknown>;
  tickers: SourceCoverageTicker[];
};

export type PriorityUniverseTicker = {
  ticker: string;
  name: string;
  market: string;
  currency: string;
  coverage_priority_order: number;
  market_cap?: string;
  market_cap_rank?: number;
  market_cap_rank_input_date?: string;
  rank_policy: string;
  rank_coverage_status?: string;
  rank_count?: number;
  rank_limit?: number;
  missing_rank_slots?: number;
  source_trace: Record<string, unknown>;
};

export type PriorityUniverse = {
  universe_id: string;
  label: string;
  market: string;
  currency: string;
  data_mode: string;
  rank_coverage_status?: string;
  rank_count?: number;
  rank_limit?: number;
  missing_rank_slots?: number;
  note: string;
  source_trace: Record<string, unknown>;
  tickers: PriorityUniverseTicker[];
};

export type SourceSeriesMeta = {
  data_mode: string;
  quality_status: string;
  row_count: number;
  source_note: string;
  filters: Record<string, unknown>;
};

export type MacroSeriesRow = {
  series_id: string;
  observation_date: string;
  value: string;
  unit: string | null;
  frequency: string | null;
  source: string;
  source_url: string | null;
  source_document_id: string;
  source_trace: Record<string, unknown>;
};

export type IndustrySeriesRow = {
  market: string;
  series_id: string;
  observation_date: string;
  value: string;
  unit: string | null;
  frequency: string | null;
  category: string;
  region: string | null;
  industry: string | null;
  source: string;
  source_url: string | null;
  source_document_id: string;
  dimensions: Record<string, unknown>;
  source_trace: Record<string, unknown>;
};

export type ContractDataStatus =
  | "ready"
  | "partial"
  | "configured"
  | "stale"
  | "fixture_non_production"
  | "missing_source"
  | "missing_contract"
  | "missing_key"
  | "rate_limited"
  | "upstream_error";

export type ContractDataState = {
  status: ContractDataStatus;
  available: boolean;
  data_mode: string;
  reason: string | null;
};

export type ContractEnvelope<T> = {
  data: T | null;
  state: ContractDataState;
  meta: Record<string, unknown>;
};

export type ContractFactValue = {
  metric: string;
  value: string | number | null;
  period: string | null;
  unit: string | null;
  currency: string | null;
  source_trace: Record<string, unknown> | null;
};

export type ConsensusContractCase = {
  case: "low" | "median" | "high" | "current";
  estimate_eps: ContractFactValue;
  growth_rate_pct: ContractFactValue | null;
  assumption_type: "external_consensus" | "manual_assumption";
  quality_status: string;
};

export type ConsensusContractData = {
  company_id: string;
  metric_key: string;
  metric_name: string;
  forecast_year: number;
  provider: string;
  evidence_kind: "external_consensus" | "manual_assumption" | "mixed";
  quality_status: string;
  cases: ConsensusContractCase[];
};

export type PeerContractRecord = {
  company_id: string;
  name: string;
  relationship: string;
  facts: ContractFactValue[];
  source_trace: Record<string, unknown>;
};

export type PeerContractData = {
  company_id: string;
  kind: "business" | "valuation";
  peers: PeerContractRecord[];
};

export type ProviderContract = {
  provider_id: string;
  label: string;
  capabilities: string[];
  contract_available: boolean;
  configured: boolean;
  verification: "configuration_only" | "contract_only" | "not_available";
  required_env: string[];
  state: ContractDataState;
};

export type ProvidersContractData = {
  providers: ProviderContract[];
};


