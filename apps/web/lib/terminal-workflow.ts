export const fundRailItems = [
  { label: "H", tab: "Summary", ariaLabel: "LUXON rail H", title: "Home" },
  { label: "T", tab: "Historical", ariaLabel: "LUXON rail T", title: "Terminal" },
  { label: "S", tab: "Screener", ariaLabel: "LUXON rail S", title: "Screens" },
  { label: "P", tab: "Portfolio", ariaLabel: "LUXON rail P", title: "Portfolio" },
  { label: "A", tab: "Data Audit", ariaLabel: "LUXON rail A", title: "Audit" }
] as const;

export const primaryWorkflowTabs = [
  { tab: "Historical", label: "Home" },
  { tab: "Summary", label: "Underwrite" },
  { tab: "Forecasting", label: "Forecast" },
  { tab: "Data Audit", label: "Audit" }
] as const;

export const workspaceCards = [
  {
    key: "Historical",
    label: "Valuation Map",
    detail: "Price, EPS, fair value, normal multiple, dividend floor"
  },
  {
    key: "Forecasting",
    label: "Forecast Lab",
    detail: "Consensus, user EPS, AI review, 1Y-5Y scenario lines"
  },
  {
    key: "Financials",
    label: "Financials",
    detail: "IS, BS, CF, margins, ROE, ROIC, debt and FCF trend"
  },
  {
    key: "Screener",
    label: "Screener",
    detail: "Metric-to-value, metric-to-metric, company-relative filters"
  },
  {
    key: "Portfolio",
    label: "Portfolio Lab",
    detail: "CSV trades, XIRR, allocation, buy and sell overlays"
  },
  {
    key: "Data Audit",
    label: "Data Audit",
    detail: "Source documents, formula, confidence, flags, waterfall"
  }
] as const;

export const mobileWorkflowTabs = [
  { tab: "Historical", label: "Map" },
  { tab: "Forecasting", label: "Forecast" },
  { tab: "Financials", label: "Finance" },
  { tab: "Screener", label: "More" },
  { tab: "Data Audit", label: "Audit" }
] as const;
