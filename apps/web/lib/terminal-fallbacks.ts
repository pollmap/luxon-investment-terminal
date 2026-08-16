import type {
  AdjustedRow,
  AnalystScorecard,
  ChartLayout,
  FinancialRow,
  FiscalFitnessRow,
  ForecastEvidence,
  ForecastMeta,
  HealthCheck,
  PortfolioSummary,
  PricePoint,
  RecessionBand,
  ResearchMetadata,
  ResearchReport,
  Snapshot,
  SourceReadiness,
  UseOfCashRow,
  ValuationRow,
  WatchlistSummary
} from "./terminal-types";

export const fallbackValuation: ValuationRow[] = [
  { fiscal_year: 2020, metric: "3.28", price: "132", dividend: "0.80", yoy: null, normal_multiple: "31.10", fair_multiple: "15.00", fair_value_price: "49.20", forecast_flag: false },
  { fiscal_year: 2021, metric: "5.61", price: "177", dividend: "0.85", yoy: "71.04", normal_multiple: "31.10", fair_multiple: "15.00", fair_value_price: "84.15", forecast_flag: false },
  { fiscal_year: 2022, metric: "6.11", price: "129", dividend: "0.90", yoy: "8.91", normal_multiple: "31.10", fair_multiple: "15.00", fair_value_price: "91.65", forecast_flag: false },
  { fiscal_year: 2023, metric: "6.13", price: "192", dividend: "0.94", yoy: "0.33", normal_multiple: "31.10", fair_multiple: "15.00", fair_value_price: "91.95", forecast_flag: false },
  { fiscal_year: 2024, metric: "6.08", price: "250", dividend: "1.00", yoy: "-0.82", normal_multiple: "31.10", fair_multiple: "15.00", fair_value_price: "91.20", forecast_flag: false },
  { fiscal_year: 2025, metric: "6.57", price: "118.26", dividend: "1.00", yoy: null, normal_multiple: "31.10", fair_multiple: "18.00", fair_value_price: "118.26", forecast_flag: true, forecast_source: "user_input", total_return_cagr_pct: "-52.31" },
  { fiscal_year: 2026, metric: "7.09", price: "127.62", dividend: "1.00", yoy: null, normal_multiple: "31.10", fair_multiple: "18.00", fair_value_price: "127.62", forecast_flag: true, forecast_source: "user_input", total_return_cagr_pct: "-28.23" },
  { fiscal_year: 2027, metric: "7.66", price: "137.88", dividend: "1.00", yoy: null, normal_multiple: "31.10", fair_multiple: "18.00", fair_value_price: "137.88", forecast_flag: true, forecast_source: "user_input", total_return_cagr_pct: "-17.40" },
  { fiscal_year: 2028, metric: "8.27", price: "148.86", dividend: "1.00", yoy: null, normal_multiple: "31.10", fair_multiple: "18.00", fair_value_price: "148.86", forecast_flag: true, forecast_source: "user_input", total_return_cagr_pct: "-10.28" },
  { fiscal_year: 2029, metric: "8.93", price: "160.74", dividend: "1.00", yoy: null, normal_multiple: "31.10", fair_multiple: "18.00", fair_value_price: "160.74", forecast_flag: true, forecast_source: "user_input", total_return_cagr_pct: "-7.90" }
];

export const fallbackPricePoints: PricePoint[] = buildFallbackAnnualPricePoints(fallbackValuation);

export const productTourSteps = [
  {
    tab: "Summary",
    title: "Command Workspace",
    body: "Start with a company question and visual dashboard: Summary preview, Historical Map, Forecast fan, Performance, and Data Audit share one source_trace graph."
  },
  {
    tab: "Historical",
    title: "Historical Valuation Map",
    body: "Compare price against the selected fundamental metric, fair value line, normal multiple line, forecast zone, and fiscal table."
  },
  {
    tab: "Forecasting",
    title: "Forecast Lab",
    body: "Separate consensus snapshots, user assumptions, historical CAGR, AI commentary, and custom target multiples before calculating returns."
  },
  {
    tab: "Performance",
    title: "Performance Calculator",
    body: "Inspect price return, dividend cash flow, reinvested return, annualized CAGR, and the source trace behind each row."
  },
  {
    tab: "Data Audit",
    title: "Source Audit",
    body: "Open any number into source document, filing id, period, unit, formula, method, confidence, flags, and upstream inputs."
  }
] as const;

export const fallbackRecessionBands: RecessionBand[] = [
  {
    series_id: "USREC",
    start_date: "2020-02-01",
    end_date: "2020-04-30",
    source: "fred_fixture",
    source_trace: {
      source_type: "fixture_non_production_macro",
      source_document_id: "fred-usrec-2020-fixture",
      period: "2020-02-01:2020-04-30",
      formula: "Contiguous FRED USREC observations equal to 1",
      quality_status: "fixture_non_production",
      source_url: "https://fred.stlouisfed.org/series/USREC"
    }
  }
];

export const fallbackAdjusted: AdjustedRow[] = [
  {
    fiscal_year: 2024,
    method: "S1_SEC_RECONCILIATION",
    confidence: "0.85",
    quality_status: "passed",
    gaap_eps_diluted: "6.08",
    adjusted_eps: "6.08",
    company_adjusted_eps: "6.08",
    flags: [],
    warnings: [],
    source_trace: { source_url: "https://www.sec.gov/fixture-ex99.html", accession_number: "fixture" },
    waterfall: [
      { label: "GAAP net income", category: "gaap_ni", pretax_amount: null, tax_effect: null, after_tax_impact: "93736", eps_impact: "6.08", included_by_policy: true, recurring: false },
      { label: "Company adjusted diluted EPS", category: "company_adjusted_eps", pretax_amount: null, tax_effect: null, after_tax_impact: "0", eps_impact: "0.00", included_by_policy: true, recurring: false }
    ]
  }
];

export const fallbackSnapshot: Snapshot = {
  ticker: "AAPL",
  name: "Apple Inc.",
  market: "US",
  country: "US",
  currency: "USD",
  sector_policy: "default",
  current_price: "250",
  market_cap: null,
  listed_shares: null,
  per: "41.12",
  dividend_yield: "0.40",
  eps: "6.08",
  eps_cagr: "16.7",
  roe: "151.1",
  roic: "53.1",
  debt_ratio: "1.87",
  eps_method: "S1_SEC_RECONCILIATION",
  confidence: "0.85",
  source_note: "fixture_non_production"
};

export const fallbackFinancials: FinancialRow[] = [
  { fiscal_year: 2020, revenue: "274515", eps: "3.28", fcf: "73365", gross_margin: "38.2", operating_margin: "24.1", net_margin: "20.9", roe: "73.7", roic: "34.7", debt_to_equity: "1.72", method: "S4_GAAP_FALLBACK", confidence: "0.35" },
  { fiscal_year: 2021, revenue: "365817", eps: "5.61", fcf: "92953", gross_margin: "41.8", operating_margin: "29.8", net_margin: "25.9", roe: "147.4", roic: "49.5", debt_to_equity: "1.98", method: "S4_GAAP_FALLBACK", confidence: "0.35" },
  { fiscal_year: 2022, revenue: "394328", eps: "6.11", fcf: "111443", gross_margin: "43.3", operating_margin: "30.3", net_margin: "25.3", roe: "175.5", roic: "56.8", debt_to_equity: "1.76", method: "S4_GAAP_FALLBACK", confidence: "0.35" },
  { fiscal_year: 2023, revenue: "383285", eps: "6.13", fcf: "99584", gross_margin: "44.1", operating_margin: "29.8", net_margin: "25.3", roe: "171.9", roic: "56.0", debt_to_equity: "1.79", method: "S4_GAAP_FALLBACK", confidence: "0.35" },
  { fiscal_year: 2024, revenue: "391035", eps: "6.08", fcf: "108807", gross_margin: "46.2", operating_margin: "31.5", net_margin: "24.0", roe: "151.1", roic: "53.1", debt_to_equity: "1.87", method: "S1_SEC_RECONCILIATION", confidence: "0.85" }
];

export const fallbackUseOfCash: UseOfCashRow[] = [
  {
    fiscal_year: 2024,
    revenue: "391035",
    operating_cash_flow: null,
    free_cash_flow: "108807",
    fcf_margin_pct: "27.83",
    dividend_per_share: "1.00",
    dividends_paid: null,
    eps: "6.08",
    dividend_payout_pct: "16.45",
    capex: null,
    share_repurchases: null,
    debt_repayment: null,
    acquisitions: null,
    net_cash_use: null,
    debt_to_equity: "1.87",
    method: "S1_SEC_RECONCILIATION",
    confidence: "0.85",
    quality_status: "fixture_non_production_use_of_cash",
    flags: ["missing_capex_source", "missing_share_repurchases_source", "missing_debt_repayment_source"],
    source_trace: { source_type: "use_of_cash_derived", quality_status: "fixture_non_production_use_of_cash" }
  }
];

export const fallbackFiscalFitness: FiscalFitnessRow[] = [
  {
    fiscal_year: 2024,
    metric_key: "roe_pct",
    label: "ROE",
    category: "profitability",
    value: "151.1",
    unit: "percent",
    direction: "higher_is_better",
    method: "S1_SEC_RECONCILIATION",
    confidence: "0.85",
    quality_status: "fixture_non_production_fiscal_fitness",
    flags: [],
    source_trace: { source_type: "fiscal_fitness_derived", quality_status: "fixture_non_production_fiscal_fitness" }
  },
  {
    fiscal_year: 2024,
    metric_key: "current_ratio",
    label: "Current ratio",
    category: "liquidity",
    value: null,
    unit: "ratio",
    direction: "higher_is_better",
    method: "S1_SEC_RECONCILIATION",
    confidence: "0.85",
    quality_status: "fixture_non_production_fiscal_fitness",
    flags: ["missing_current_ratio_source"],
    source_trace: { source_type: "fiscal_fitness_derived", quality_status: "fixture_non_production_fiscal_fitness" }
  }
];

export const fallbackHealthCheck: HealthCheck = {
  ticker: "AAPL",
  fiscal_year: 2024,
  overall_score: "76.00",
  rating: "healthy",
  quality_status: "fixture_non_production_health_check",
  flags: ["fixture_non_production_scorecard_proxy"],
  axes: [
    { axis_key: "profitability", label: "Profitability", score: "90.00", weight: "0.25", quality_status: "fixture_non_production_health_check", flags: [], inputs: [], source_trace: { source_type: "health_check_axis_derived", quality_status: "fixture_non_production_health_check" } },
    { axis_key: "cash_generation", label: "Cash generation", score: "100.00", weight: "0.20", quality_status: "fixture_non_production_health_check", flags: [], inputs: [], source_trace: { source_type: "health_check_axis_derived", quality_status: "fixture_non_production_health_check" } },
    { axis_key: "financial_strength", label: "Financial strength", score: "50.00", weight: "0.20", quality_status: "fixture_non_production_health_check", flags: ["missing_current_ratio_source"], inputs: [], source_trace: { source_type: "health_check_axis_derived", quality_status: "fixture_non_production_health_check" } },
    { axis_key: "growth", label: "Growth", score: "55.00", weight: "0.20", quality_status: "fixture_non_production_health_check", flags: [], inputs: [], source_trace: { source_type: "health_check_axis_derived", quality_status: "fixture_non_production_health_check" } },
    { axis_key: "predictability", label: "Predictability", score: "83.00", weight: "0.15", quality_status: "fixture_non_production_health_check", flags: ["fixture_non_production_scorecard_proxy"], inputs: [], source_trace: { source_type: "health_check_axis_derived", quality_status: "fixture_non_production_health_check" } }
  ],
  source_trace: { source_type: "health_check_derived", quality_status: "fixture_non_production_health_check" }
};

export const fallbackResearchReport: ResearchReport = {
  ticker: "AAPL",
  title: "AAPL Source-Audited Research Report",
  fiscal_year: 2024,
  data_mode: "fixture_non_production",
  executive_summary: [
    "AAPL trades at 250 versus deterministic fair value 91.20.",
    "Valuation gap is 174.12% and quality rating is healthy.",
    "Latest 1-5Y forecast endpoint implies total return CAGR of -7.90%."
  ],
  sections: [
    {
      section_key: "valuation",
      title: "Valuation",
      verdict: "premium_to_fair_value",
      bullets: ["Latest price is 250 USD.", "Deterministic fair value is 91.20 USD."],
      evidence: [],
      flags: [],
      quality_status: "fixture_non_production_research_report",
      source_trace: { source_type: "research_report_section", quality_status: "fixture_non_production_research_report" }
    }
  ],
  audit_facts: [],
  flags: ["fixture_non_production_report"],
  quality_status: "fixture_non_production_research_report",
  source_trace: { source_type: "research_report_derived", quality_status: "fixture_non_production_research_report" }
};

export const fallbackResearchMetadata: ResearchMetadata = {
  ticker: "AAPL",
  data_mode: "source_backed_required",
  policy: "metadata_only_no_financial_numbers",
  quality_status: "missing_source_backed_data",
  items: [],
  source_trace: {
    source_type: "research_metadata",
    source_document_id: "research_metadata:not_loaded",
    quality_status: "missing_source_backed_data",
    method: "metadata_only_no_financial_numbers",
    formula: "No source-backed external research metadata is substituted by fixture data.",
    unit: "research_metadata",
    currency: "N/A"
  }
};

export const fallbackPortfolio: PortfolioSummary = {
  as_of: "2026-05-31",
  total_market_value: "2500.00",
  xirr: "12.00",
  sector_weights: { Technology: "100.00" },
  holdings: [
    {
      ticker: "AAPL",
      quantity: "10",
      average_cost: "130.00",
      latest_price: "250",
      market_value: "2500.00",
      unrealized_pnl: "1200.00",
      weight_pct: "100.00",
      sector: "Technology",
      currency: "USD",
      transactions: [{ date: "2023-01-10", side: "buy", quantity: "10", price: "130" }]
    }
  ]
};

export const samplePortfolioCsv = [
  "date,ticker,side,quantity,price,currency,sector",
  "2024-01-02,AAPL,buy,10,185,USD,Technology",
  "2024-03-15,NVDA,buy,4,900,USD,Technology"
].join("\n");

export const fallbackWatchlist: WatchlistSummary = {
  id: "fixture-default",
  name: "Default",
  owner_key: "fixture",
  items: [
    { ticker: "AAPL", name: "Apple Inc.", market: "NASDAQ", country: "US", currency: "USD", current_price: "250.42", per: "41.19", dividend_yield: "0.40", eps_cagr: "-0.25", quality_status: "fixture_non_production", note: null },
    { ticker: "NVDA", name: "NVIDIA Corp.", market: "NASDAQ", country: "US", currency: "USD", current_price: "148.88", per: "44.44", dividend_yield: "0.03", eps_cagr: "44.20", quality_status: "fixture_non_production", note: null },
    { ticker: "CRM", name: "Salesforce Inc.", market: "NYSE", country: "US", currency: "USD", current_price: "298.31", per: "47.81", dividend_yield: "0.00", eps_cagr: "22.10", quality_status: "fixture_non_production", note: "SBC-heavy software pattern" }
  ],
  source_trace: { quality_status: "fixture_non_production_watchlist" }
};

export const fallbackChartLayouts: ChartLayout[] = [];

export const emptyPortfolio: PortfolioSummary = {
  as_of: new Date().toISOString().slice(0, 10),
  total_market_value: "0.00",
  xirr: null,
  sector_weights: {},
  holdings: []
};

export const fallbackForecastMeta: ForecastMeta = {
  years: 5,
  mode: "custom",
  case: "median",
  normal_multiple: {
    window_years: 5,
    formula: "trimmed_mean(price / metric) over selected historical fiscal-year window"
  },
  growth_rate_pct: "8",
  target_multiple: "18",
  analyst_count: null,
  source: "user_input",
  formula: "custom EPS override when provided; missing years use growth from previous metric",
  source_trace: {
    source_type: "user_input",
    source_document_id: "fixture-forecast-assumption",
    filing_id: "fixture-forecast-assumption",
    period: "FY2025-FY2029",
    unit: "per_share",
    currency: "USD",
    formula: "custom EPS override when provided; missing years use growth from previous metric",
    quality_status: "fixture_non_production_forecast"
  },
  manual_eps_values: [null, null, null, null, null],
  calculation_lines: []
};

export const fallbackForecastEvidence: ForecastEvidence = {
  forecast_year: 2025,
  metric_name: "Adjusted Operating EPS",
  cases: [
    { case: "low", growth_rate_pct: "5.0", estimate_eps: "6.38" },
    { case: "median", growth_rate_pct: "7.0", estimate_eps: "6.51" },
    { case: "high", growth_rate_pct: "9.0", estimate_eps: "6.63" }
  ],
  revisions: [
    { as_of_label: "12M prior", age_months: 12, estimate_eps: "6.25", analyst_count: 27, revision_delta_pct: null, quality_status: "fixture_non_production_consensus_proxy" },
    { as_of_label: "3M prior", age_months: 3, estimate_eps: "6.38", analyst_count: 29, revision_delta_pct: "2.08", quality_status: "fixture_non_production_consensus_proxy" },
    { as_of_label: "1M prior", age_months: 1, estimate_eps: "6.58", analyst_count: 30, revision_delta_pct: "3.13", quality_status: "fixture_non_production_consensus_proxy" },
    { as_of_label: "current", age_months: 0, estimate_eps: "6.51", analyst_count: 31, revision_delta_pct: "-1.06", quality_status: "fixture_non_production_consensus_proxy" }
  ],
  sentiment: {
    label: "positive",
    net_revision_score_pct: "1.72",
    up_revisions: 10,
    down_revisions: 5,
    unchanged: 16,
    quality_status: "fixture_non_production_consensus_proxy"
  },
  scorecard: {
    status: "fixture_non_production_scorecard_proxy",
    rows: [
      { fiscal_year: 2022, actual_eps: "6.11", estimate_1y_prior: "6.35", estimate_2y_prior: "5.62", error_1y_pct: "3.93", error_2y_pct: "-8.02", result_1y: "hit", result_2y: "hit", quality_status: "fixture_non_production_scorecard_proxy" },
      { fiscal_year: 2023, actual_eps: "6.13", estimate_1y_prior: "6.38", estimate_2y_prior: "5.64", error_1y_pct: "4.08", error_2y_pct: "-7.99", result_1y: "hit", result_2y: "hit", quality_status: "fixture_non_production_scorecard_proxy" },
      { fiscal_year: 2024, actual_eps: "6.08", estimate_1y_prior: "6.32", estimate_2y_prior: "5.59", error_1y_pct: "3.95", error_2y_pct: "-8.06", result_1y: "hit", result_2y: "hit", quality_status: "fixture_non_production_scorecard_proxy" }
    ],
    summary: {
      hit_rate_1y_pct: "100.00",
      hit_rate_2y_pct: "100.00",
      required_source: "point_in_time_consensus_snapshots"
    }
  },
  meta: {
    data_mode: "fixture_non_production",
    quality_status: "fixture_non_production_consensus_proxy",
    source_note: "Proxy values are for UI and contract testing only."
  }
};

export const fallbackAnalystScorecard: AnalystScorecard = {
  ticker: "AAPL",
  status: "fixture_non_production_scorecard_proxy",
  rows: fallbackForecastEvidence.scorecard.rows.map((row) => ({
    ...row,
    flags: ["fixture_non_production_scorecard_proxy"],
    source_trace: fallbackForecastEvidence.source_trace
  })),
  summary: {
    hit_rate_1y_pct: fallbackForecastEvidence.scorecard.summary.hit_rate_1y_pct,
    hit_rate_2y_pct: fallbackForecastEvidence.scorecard.summary.hit_rate_2y_pct,
    scored_years: fallbackForecastEvidence.scorecard.rows.length,
    required_source: fallbackForecastEvidence.scorecard.summary.required_source,
    quality_status: "fixture_non_production_scorecard_proxy",
    flags: ["fixture_non_production_scorecard_proxy"]
  },
  quality_status: "fixture_non_production_scorecard_proxy",
  flags: ["fixture_non_production_scorecard_proxy"],
  source_trace: fallbackForecastEvidence.source_trace
};

export const fallbackReadiness: SourceReadiness = {
  status: "fixture_only",
  data_backend: "fixture",
  data_mode: "fixture_non_production",
  checks: [],
  postgres: {
    reachable: false,
    counts: {},
    error: "not_loaded"
  }
};

function buildFallbackAnnualPricePoints(rows: ValuationRow[]): PricePoint[] {
  return rows.map((row) => ({
    date: `${row.fiscal_year}-12-31`,
    fiscal_year: row.fiscal_year,
    close_price: row.price,
    currency: "USD",
    frequency: "annual",
    source_trace: {
      source_type: "fixture_non_production_price",
      source_document_id: `fixture-price-${row.fiscal_year}`,
      filing_id: `fixture-price-${row.fiscal_year}`,
      period: `${row.fiscal_year}-12-31`,
      unit: "price",
      currency: "USD",
      formula: "fixture annual close price for non-production chart tests",
      quality_status: "fixture_non_production"
    }
  }));
}
