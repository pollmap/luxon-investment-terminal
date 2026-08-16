export const securities = {
  AAPL: { label: "Apple", market: "US", currency: "USD" },
  NVDA: { label: "NVIDIA", market: "US", currency: "USD" },
  GOOG: { label: "Alphabet", market: "US", currency: "USD" },
  MSFT: { label: "Microsoft", market: "US", currency: "USD" },
  AMZN: { label: "Amazon", market: "US", currency: "USD" },
  AVGO: { label: "Broadcom", market: "US", currency: "USD" },
  TSLA: { label: "Tesla", market: "US", currency: "USD" },
  META: { label: "Meta Platforms", market: "US", currency: "USD" },
  MU: { label: "Micron Technology", market: "US", currency: "USD" },
  LLY: { label: "Eli Lilly", market: "US", currency: "USD" },
  "005930.KS": { label: "Samsung Electronics", market: "KR", currency: "KRW" },
  "000660.KS": { label: "SK hynix", market: "KR", currency: "KRW" },
  "402340.KS": { label: "SK Square", market: "KR", currency: "KRW" },
  "005380.KS": { label: "Hyundai Motor", market: "KR", currency: "KRW" },
  "028260.KS": { label: "Samsung C&T", market: "KR", currency: "KRW" },
  "032830.KS": { label: "Samsung Life Insurance", market: "KR", currency: "KRW" },
  "373220.KS": { label: "LG Energy Solution", market: "KR", currency: "KRW" },
  "207940.KS": { label: "Samsung Biologics", market: "KR", currency: "KRW" },
  "329180.KS": { label: "HD Hyundai Heavy Industries", market: "KR", currency: "KRW" },
  "009155.KS": { label: "Samsung Electro-Mechanics Preferred", market: "KR", currency: "KRW" },
  "000270.KS": { label: "Kia", market: "KR", currency: "KRW" },
  "068270.KS": { label: "Celltrion", market: "KR", currency: "KRW" },
  "105560.KS": { label: "KB Financial Group", market: "KR", currency: "KRW" },
  "035420.KS": { label: "NAVER", market: "KR", currency: "KRW" },
  "005490.KS": { label: "POSCO Holdings", market: "KR", currency: "KRW" },
  "285A.T": { label: "Kioxia Holdings", market: "JP", currency: "JPY" },
  "8306.T": { label: "Mitsubishi UFJ Financial", market: "JP", currency: "JPY" },
  "9984.T": { label: "SoftBank Group", market: "JP", currency: "JPY" },
  "8035.T": { label: "Tokyo Electron", market: "JP", currency: "JPY" },
  "7203.T": { label: "Toyota Motor", market: "JP", currency: "JPY" },
  "9983.T": { label: "Fast Retailing", market: "JP", currency: "JPY" },
  "8316.T": { label: "Sumitomo Mitsui Financial Group", market: "JP", currency: "JPY" },
  "6857.T": { label: "Advantest", market: "JP", currency: "JPY" },
  "6501.T": { label: "Hitachi", market: "JP", currency: "JPY" },
  "6981.T": { label: "Murata Manufacturing", market: "JP", currency: "JPY" },
  CRM: { label: "Salesforce", market: "US", currency: "USD" },
  O: { label: "Realty Income", market: "US", currency: "USD" },
  JPM: { label: "JPMorgan", market: "US", currency: "USD" }
} as const;

export const krTopMarketCapPriorityTickers = [
  "005930.KS",
  "000660.KS",
  "402340.KS",
  "005380.KS",
  "028260.KS",
  "032830.KS",
  "373220.KS",
  "207940.KS",
  "329180.KS",
  "009155.KS"
] as const;

export const usTopMarketCapPriorityTickers = [
  "NVDA",
  "AAPL",
  "GOOG",
  "MSFT",
  "AMZN",
  "AVGO",
  "TSLA",
  "META",
  "MU",
  "LLY"
] as const;

export const jpTopMarketCapPriorityTickers = [
  "285A.T",
  "8306.T",
  "9984.T",
  "8035.T",
  "7203.T",
  "9983.T",
  "8316.T",
  "6857.T",
  "6501.T",
  "6981.T"
] as const;

export const defaultTicker = krTopMarketCapPriorityTickers[0];

export const krTopMarketCapUniverseNote =
  "KR top-market-cap priority universe. Production rank must be recomputed from source-backed marcap/KRX market-cap rows.";

export const tabs = [
  "Summary",
  "Historical",
  "Performance",
  "Forecasting",
  "Consensus",
  "Peers",
  "Research Report",
  "Financials",
  "Fun Graphs",
  "Fiscal Fitness",
  "Health Check",
  "Use of Cash",
  "Screener",
  "Watchlist",
  "Portfolio",
  "Data Audit",
  "Analyst Scorecard",
  "System"
] as const;

export const tickers = Object.keys(securities);

export const metricOptionGroups = [
  "Smart",
  "Earnings",
  "Cash Flow",
  "Operating",
  "Sales",
  "Specialized"
] as const;

export const metricOptions = [
  {
    value: "smart_metric",
    label: "Smart Metric",
    group: "Smart",
    requiresSourceBackedMetric: true,
    disabledHint: "source-backed sector rules required"
  },
  { value: "adjusted_operating", label: "Adjusted Operating EPS", group: "Earnings" },
  {
    value: "basic_eps",
    label: "Basic EPS",
    group: "Earnings",
    requiresSourceBackedMetric: true,
    disabledHint: "source-backed basic EPS required"
  },
  { value: "diluted_eps", label: "Diluted EPS", group: "Earnings" },
  {
    value: "operating_cash_flow_share",
    label: "Operating Cash Flow (OCF/FFO)",
    group: "Cash Flow",
    requiresSourceBackedMetric: true,
    disabledHint: "source-backed OCF/share required"
  },
  {
    value: "fcf_share",
    label: "Free Cash Flow to Equity (FCFE/AFFO)",
    group: "Cash Flow",
    requiresSourceBackedMetric: true,
    disabledHint: "source-backed FCF/share required"
  },
  {
    value: "ebitda_share",
    label: "EBITDA/share",
    group: "Operating",
    requiresSourceBackedMetric: true,
    disabledHint: "source-backed EBITDA/share required"
  },
  {
    value: "ebit_share",
    label: "EBIT/share",
    group: "Operating",
    requiresSourceBackedMetric: true,
    disabledHint: "source-backed EBIT/share required"
  },
  { value: "sales_share", label: "Sales/share", group: "Sales" },
  {
    value: "ffo_affo",
    label: "FFO/AFFO",
    group: "Specialized",
    requiresReit: true,
    disabledHint: "REIT only"
  }
] as const;

export const forecastModes = [
  ["consensus", "Consensus"],
  ["estimates", "Estimates"],
  ["normal_multiple", "Normal Multiple"],
  ["lt_growth", "LT Growth"],
  ["historical_cagr", "Historical CAGR"],
  ["ai_review", "AI Review"],
  ["custom", "Custom"]
] as const;

export const forecastCases = [
  ["low", "Low"],
  ["median", "Median"],
  ["high", "High"]
] as const;

export const API_TIMEOUT_MS = 12_000;
