"use client";

import { HelpCircle, LogIn, Settings2, X } from "lucide-react";
import type { FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GraphKeyLedgerItem } from "../components/graph-key-ledger";
import { AnalystScorecardPanel } from "../components/analyst-scorecard-panel";
import { DataAuditPanel, SelectedAuditTrace } from "../components/data-audit-panel";
import { EvidenceRail } from "../components/evidence-rail";
import { FiscalFitnessPanel } from "../components/fiscal-fitness-panel";
import { ForecastLab } from "../components/forecast-lab";
import { FinancialsPanel } from "../components/financials-panel";
import { FunGraphsPanel } from "../components/fun-graphs-panel";
import { HealthCheckPanel } from "../components/health-check-panel";
import { HistoricalControlsPanel } from "../components/historical-controls-panel";
import { HistoricalMapPanel } from "../components/historical-map-panel";
import { PerformancePanel } from "../components/performance-panel";
import { PortfolioPanel } from "../components/portfolio-panel";
import { ResearchReportPanel } from "../components/research-report-panel";
import { ConsensusPanel, PeersPanel, ProviderStatusPanel } from "../components/research-contract-panels";
import { SearchOverlay } from "../components/search-overlay";
import { ScreenerPanel } from "../components/screener-panel";
import { BrandMark, Metric, NumberControl, Toggle } from "../components/terminal-primitives";
import { UseOfCashPanel } from "../components/use-of-cash-panel";
import {
  API_TIMEOUT_MS,
  defaultTicker,
  metricOptions,
  securities,
  tabs,
  tickers
} from "../lib/terminal-config";
import {
  fallbackCoverage,
  fallbackPriorityUniverse,
  fallbackSourceSeriesMeta,
  isKrPriorityCoverageTicker,
  isPriorityCoverageTicker,
  isSourceBackedCacheValuationPayload,
  isSourceSatisfiedMode,
  isUnsafePriorityFinancialPayload,
  krValuationCacheCoverageFromPayload,
  metricCoverageAliases,
  missingAdjustedRowsForTicker,
  missingAnalystScorecardForTicker,
  missingAuditRowsForTicker,
  missingForecastEvidenceForTicker,
  missingForecastMetaForTicker,
  missingHealthCheckForTicker,
  missingSnapshotForTicker,
  priorityCoverageGroups,
  regressionSeedTickers,
  sourceReadinessFromValuationPayload,
  sourceRequiredReadinessForTicker
} from "../lib/terminal-source-gate";
import {
  normalizeKrValuationCacheUniverse,
  normalizeIndustrySeriesResponse,
  normalizeMacroSeriesResponse,
  normalizePriorityUniverse,
  normalizeSourceCoverage
} from "../lib/terminal-source-normalizers";
import { fundRailItems, mobileWorkflowTabs, primaryWorkflowTabs, workspaceCards } from "../lib/terminal-workflow";
import {
  buildChartReturnSelection,
  buildLinePoints,
  buildMetricAreaPath,
  currentValuationMultiple,
  formatMaybePercent,
  latestDividendRatioMetrics,
  latestHistoricalYear,
  maxChartValue
} from "../lib/terminal-chart";
import { auditFactHref, auditTestIdPart, publicTraceSummary } from "../lib/audit-utils";

import type {
  AdjustedRow,
  AnalystScorecard,
  AskConsensusEvidence,
  AskInstruction,
  AskNarrative,
  AuditRow,
  ChartLayout,
  ChartLayoutConfig,
  FiscalFitnessRow,
  ForecastEvidence,
  ForecastMeta,
  FunGraphs,
  HealthCheck,
  IndustrySeriesRow,
  KrValuationCacheCoverage,
  KrValuationCacheUniverseCoverage,
  LineVisibility,
  MacroSeriesRow,
  OwnerSession,
  PerformanceSummary,
  PortfolioSummary,
  PricePoint,
  PriorityUniverse,
  RecessionBand,
  ResearchMetadata,
  ResearchReport,
  ResearchReportSection,
  ScreenerRow,
  Snapshot,
  SourceCoverage,
  SourceReadiness,
  SourceSeriesMeta,
  UseOfCashRow,
  ValuationRow,
  WatchlistSummary,
  FinancialRow
} from "../lib/terminal-types";
import {
  emptyPortfolio,
  fallbackAdjusted,
  fallbackAnalystScorecard,
  fallbackChartLayouts,
  fallbackFinancials,
  fallbackFiscalFitness,
  fallbackForecastEvidence,
  fallbackForecastMeta,
  fallbackHealthCheck,
  fallbackPortfolio,
  fallbackPricePoints,
  fallbackRecessionBands,
  fallbackReadiness,
  fallbackResearchMetadata,
  fallbackResearchReport,
  fallbackSnapshot,
  fallbackUseOfCash,
  fallbackValuation,
  fallbackWatchlist,
  productTourSteps,
  samplePortfolioCsv
} from "../lib/terminal-fallbacks";
function buildValuationContextQuery({
  metric,
  forecastMode,
  forecastCase,
  forecastYears,
  normalMultipleYears,
  growth,
  manualEps,
  rangeStartYear,
  rangeEndYear,
  targetMultiple
}: {
  metric: string;
  forecastMode: string;
  forecastCase: string;
  forecastYears: number;
  normalMultipleYears: number;
  growth: number;
  manualEps: string[];
  rangeStartYear: string;
  rangeEndYear: string;
  targetMultiple: number;
}) {
  const query = new URLSearchParams({
    metric,
    forecast_mode: forecastMode,
    forecast_case: forecastCase,
    forecast_years: String(forecastYears),
    normal_multiple_years: String(normalMultipleYears),
    user_growth_rate: String(growth),
    manual_eps_values: manualEps.slice(0, forecastYears).join(",")
  });
  if (rangeStartYear.trim()) {
    query.set("start_year", rangeStartYear.trim());
  }
  if (rangeEndYear.trim()) {
    query.set("end_year", rangeEndYear.trim());
  }
  if (forecastMode === "custom") {
    query.set("target_multiple", String(targetMultiple));
  }
  return query;
}

function useDebouncedValue<T>(value: T, delayMs: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);
  return debounced;
}

function parseAskInstruction(prompt: string, baseFiscalYear?: number): AskInstruction {
  const text = prompt.toLowerCase();
  const ticker = tickerForAskPrompt(prompt);
  const forecastYears = parseForecastYears(prompt);
  const forecastMode = parseForecastMode(text);
  const forecastCase = parseForecastCase(text);
  const growth = parsePromptPercent(text);
  const targetMultiple = parsePromptTargetMultiple(text);
  const manualEpsValues = parsePromptManualEps(prompt, baseFiscalYear);
  const manualOverrideCount = manualEpsValues?.filter((value) => value.trim()).length ?? 0;
  const manualHorizon = manualEpsValues ? latestManualEpsIndex(manualEpsValues) + 1 : undefined;
  const resolvedForecastYears = manualHorizon
    ? Math.min(5, Math.max(forecastYears ?? 1, manualHorizon))
    : forecastYears;
  const resolvedForecastMode = manualOverrideCount ? "custom" : forecastMode;
  const visibility = parsePromptLineVisibility(text);
  const applied: string[] = [];
  const hasForecastIntent = Boolean(
    resolvedForecastYears ||
    resolvedForecastMode ||
    forecastCase ||
    growth !== undefined ||
    targetMultiple !== undefined ||
    manualOverrideCount ||
    promptHasAny(text, ["forecast", "projection", "scenario", "target", "cagr", "estimate", "consensus", "예측", "전망", "목표", "성장률"])
  );
  const hasAuditIntent = promptHasAny(text, ["source", "trace", "audit", "filing", "evidence", "출처", "근거", "감사", "공시"]);
  const tab = hasForecastIntent
    ? "Forecasting"
    : hasAuditIntent
      ? "Data Audit"
      : promptHasAny(text, ["screen", "filter", "screener", "필터", "스크리너"])
        ? "Screener"
        : promptHasAny(text, ["financial", "roe", "roic", "cash", "margin", "debt", "revenue", "재무", "마진", "부채"])
          ? "Financials"
          : promptHasAny(text, ["report", "thesis", "narrative", "리포트", "보고서"])
            ? "Research Report"
            : "Historical";

  if (resolvedForecastYears) {
    applied.push(`${resolvedForecastYears}Y forecast`);
  }
  if (resolvedForecastMode) {
    applied.push(`${resolvedForecastMode.replaceAll("_", " ")} mode`);
  }
  if (forecastCase) {
    applied.push(`${forecastCase} case`);
  }
  if (growth !== undefined) {
    applied.push(`${growth}% EPS growth`);
  }
  if (targetMultiple !== undefined) {
    applied.push(`${targetMultiple}x target P/E overlay`);
    visibility.customValuation = true;
  }
  if (manualOverrideCount) {
    applied.push(`${manualOverrideCount}/${resolvedForecastYears ?? 5} manual EPS overrides`);
  }
  if (promptHasAny(text, ["bear/base/bull", "bear base bull", "scenario lines", "시나리오"])) {
    visibility.scenarioLines = true;
  }
  for (const [key, value] of Object.entries(visibility) as Array<[keyof LineVisibility, boolean]>) {
    if (key !== "customValuation" || targetMultiple === undefined) {
      applied.push(`${lineVisibilityLabel(key)} ${value ? "on" : "off"}`);
    }
  }

  return {
    tab,
    ticker,
    forecastMode: resolvedForecastMode,
    forecastCase,
    forecastYears: resolvedForecastYears,
    growth,
    targetMultiple,
    manualEpsValues,
    visibility,
    applied
  };
}

function tickerForAskPrompt(prompt: string) {
  const normalized = prompt.toUpperCase();
  return tickers.find((item) => normalized.includes(item.toUpperCase())) ??
    tickers.find((item) => prompt.toLowerCase().includes(securities[item as keyof typeof securities].label.toLowerCase()));
}

function parseForecastYears(prompt: string) {
  const text = prompt.toLowerCase();
  if (/1\s*(?:y|yr|year|년)\s*(?:-|~|to|부터|에서)\s*5\s*(?:y|yr|year|년)/.test(text)) {
    return 5;
  }
  const matches = [...text.matchAll(/\b([1-5])\s*(?:y|yr|yrs|year|years|년)\b/g)].map((match) => Number(match[1]));
  if (!matches.length) {
    return undefined;
  }
  return Math.min(5, Math.max(1, Math.max(...matches)));
}

function parseForecastMode(text: string) {
  if (promptHasAny(text, ["consensus", "estimate", "estimates", "컨센", "컨센서스"])) {
    return "consensus";
  }
  if (promptHasAny(text, ["normal multiple", "normal p/e", "normal pe", "평균 per", "평균 멀티플"])) {
    return "normal_multiple";
  }
  if (promptHasAny(text, ["lt growth", "long-term growth", "long term growth", "장기 성장"])) {
    return "lt_growth";
  }
  if (promptHasAny(text, ["historical cagr", "history cagr", "past cagr", "과거 cagr"])) {
    return "historical_cagr";
  }
  if (promptHasAny(text, ["ai review", "ai-assisted", "ai assisted", "ai 추론", "ai"])) {
    return "ai_review";
  }
  if (promptHasAny(text, ["custom", "manual", "user input", "user values", "직접", "사용자"])) {
    return "custom";
  }
  return undefined;
}

function parseForecastCase(text: string) {
  if (promptHasAny(text, ["bear/base/bull", "bear base bull"])) {
    return "median";
  }
  if (promptHasAny(text, ["bear", "low", "downside", "conservative", "하방", "보수"])) {
    return "low";
  }
  if (promptHasAny(text, ["bull", "high", "upside", "aggressive", "상방", "공격"])) {
    return "high";
  }
  if (promptHasAny(text, ["base", "median", "mid", "중립", "기준"])) {
    return "median";
  }
  return undefined;
}

function parsePromptPercent(text: string) {
  const match =
    text.match(/(?:eps\s*)?(?:growth|cagr|성장률)[^\d-]*(-?\d+(?:\.\d+)?)\s*%/) ??
    text.match(/(-?\d+(?:\.\d+)?)\s*%\s*(?:eps\s*)?(?:growth|cagr|성장률)/);
  if (!match) {
    return undefined;
  }
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : undefined;
}

function parsePromptTargetMultiple(text: string) {
  const match =
    text.match(/(?:target|custom|terminal|목표)?\s*(?:p\/?e|pe|multiple|멀티플)[^\d-]*(-?\d+(?:\.\d+)?)/) ??
    text.match(/(-?\d+(?:\.\d+)?)\s*(?:x|배)\b/);
  if (!match) {
    return undefined;
  }
  const value = Number(match[1]);
  if (!Number.isFinite(value) || value <= 0) {
    return undefined;
  }
  return value;
}

function parsePromptManualEps(prompt: string, baseFiscalYear?: number) {
  const explicitValues = parseYearTaggedManualEps(prompt, baseFiscalYear);
  if (explicitValues) {
    return explicitValues;
  }
  const sequentialSegment = prompt.match(/(?:manual\s+eps|eps\s+(?:path|values|override|overrides|assumption|assumptions)|eps\s*[:=])([^;\n]+)/i)?.[1];
  if (!sequentialSegment) {
    return undefined;
  }
  const epsSegment = sequentialSegment.split(/\b(?:target|p\/?e|pe|multiple|hide|show|line|forecast|source|trace|audit)\b/i)[0];
  const numbers = [...epsSegment.matchAll(/-?\d+(?:\.\d+)?/g)]
    .map((match) => normalizeManualEpsValue(match[0]))
    .filter((value): value is string => value !== null)
    .slice(0, 5);
  if (!numbers.length) {
    return undefined;
  }
  const values = emptyManualEpsValues();
  numbers.forEach((value, index) => {
    values[index] = value;
  });
  return values;
}

function parseYearTaggedManualEps(prompt: string, baseFiscalYear?: number) {
  const rows: Array<{ year: number; value: string }> = [];
  const patterns = [
    /\b(20\d{2})E?\s*(?:adjusted\s+|gaap\s+|diluted\s+)?(?:eps|metric)[^\d-]*(-?\d+(?:\.\d+)?)/gi,
    /(?:adjusted\s+|gaap\s+|diluted\s+)?(?:eps|metric)\s*(?:fy)?\s*(20\d{2})E?[^\d-]*(-?\d+(?:\.\d+)?)/gi
  ];
  for (const pattern of patterns) {
    for (const match of prompt.matchAll(pattern)) {
      const year = Number(match[1]);
      const value = normalizeManualEpsValue(match[2]);
      if (Number.isFinite(year) && value !== null) {
        rows.push({ year, value });
      }
    }
  }
  if (!rows.length) {
    return undefined;
  }
  const uniqueRows = Array.from(new Map(rows.map((row) => [row.year, row])).values()).sort((left, right) => left.year - right.year);
  const values = emptyManualEpsValues();
  for (const row of uniqueRows) {
    const index = baseFiscalYear ? row.year - baseFiscalYear - 1 : uniqueRows.findIndex((item) => item.year === row.year);
    if (index >= 0 && index < values.length) {
      values[index] = row.value;
    }
  }
  return values.some((value) => value.trim()) ? values : undefined;
}

function emptyManualEpsValues() {
  return ["", "", "", "", ""];
}

function latestManualEpsIndex(values: string[]) {
  for (let index = Math.min(values.length, 5) - 1; index >= 0; index -= 1) {
    if (values[index]?.trim()) {
      return index;
    }
  }
  return -1;
}

function normalizeManualEpsValue(raw: string) {
  const trimmed = raw.trim().replace(/^\+/, "");
  const value = Number(trimmed);
  if (!Number.isFinite(value) || Math.abs(value) >= 10_000) {
    return null;
  }
  return trimmed;
}

function parsePromptLineVisibility(text: string): Partial<LineVisibility> {
  const patch: Partial<LineVisibility> = {};
  const rules: Array<[keyof LineVisibility, string[]]> = [
    ["price", ["price line", "black line", "stock price", "주가선", "검은 선"]],
    ["metricArea", ["metric area", "eps area", "green area", "eps line", "초록", "eps"]],
    ["fairValue", ["fair value", "orange line", "fair line", "주황", "적정가치"]],
    ["normalMultiple", ["normal multiple", "normal p/e", "normal pe", "blue line", "파란", "평균 per"]],
    ["customValuation", ["custom valuation", "target line", "target p/e line", "target pe line", "목표선"]],
    ["dividendFloor", ["dividend floor", "dividend line", "dividend", "배당선", "배당"]],
    ["payoutRatio", ["payout ratio", "payout", "배당성향"]],
    ["dividendYield", ["dividend yield", "배당수익률"]],
    ["recessionBands", ["recession band", "recession", "침체"]],
    ["forecast", ["forecast area", "forecast shade", "forward area", "예측 구간"]],
    ["scenarioLines", ["scenario lines", "scenario line", "scenario", "bear/base/bull", "시나리오"]]
  ];
  for (const [key, terms] of rules) {
    if (!promptHasAny(text, terms)) {
      continue;
    }
    if (promptHasAction(text, ["hide", "remove", "off", "disable", "without", "exclude", "숨", "꺼", "끄", "제거"])) {
      patch[key] = false;
    }
    if (promptHasAction(text, ["show", "add", "on", "enable", "with", "display", "표시", "켜", "추가"])) {
      patch[key] = true;
    }
  }
  return patch;
}

function promptHasAny(text: string, needles: string[]) {
  return needles.some((needle) => text.includes(needle));
}

function promptHasAction(text: string, actions: string[]) {
  return actions.some((action) => {
    if (/^[a-z]+$/.test(action)) {
      return new RegExp(`(^|[^a-z])${action}([^a-z]|$)`).test(text);
    }
    return text.includes(action);
  });
}

function lineVisibilityLabel(key: keyof LineVisibility) {
  return key.replace(/[A-Z]/g, (letter) => ` ${letter.toLowerCase()}`);
}

function defaultAskPromptForTicker(selectedTicker: string) {
  return `Analyze ${selectedTicker} adjusted EPS and 5Y downside`;
}

function defaultChartLayoutNameForTicker(selectedTicker: string) {
  return `${selectedTicker} base case`;
}

const workspaceTabAliases: Record<string, string> = {
  graph: "Historical",
  historical: "Historical",
  snapshot: "Summary",
  summary: "Summary",
  performance: "Performance",
  forecast: "Forecasting",
  forecasting: "Forecasting",
  financials: "Financials",
  consensus: "Consensus",
  peers: "Peers",
  screener: "Screener",
  portfolio: "Portfolio",
  audit: "Data Audit",
  "data-audit": "Data Audit",
  scorecard: "Analyst Scorecard",
  system: "System"
};

function workspaceTabFromQuery(value: string | null) {
  if (!value) {
    return null;
  }
  const directMatch = tabs.find((tab) => tab.toLowerCase() === value.toLowerCase());
  return directMatch ?? workspaceTabAliases[value.toLowerCase()] ?? null;
}

export default function Home() {
  const [ticker, setTicker] = useState<string>(defaultTicker);
  const [activeTab, setActiveTab] = useState("Historical");
  const [productTourOpen, setProductTourOpen] = useState(false);
  const [productTourStep, setProductTourStep] = useState(0);
  const [askPrompt, setAskPrompt] = useState(defaultAskPromptForTicker(defaultTicker));
  const [askStatus, setAskStatus] = useState("Ready for source-traced underwriting");
  const [metric, setMetric] = useState("adjusted_operating");
  const [forecastMode, setForecastMode] = useState("custom");
  const [forecastCase, setForecastCase] = useState("median");
  const [growth, setGrowth] = useState(8);
  const [targetMultiple, setTargetMultiple] = useState(18);
  const [forecastYears, setForecastYears] = useState(5);
  const [rangeMode, setRangeMode] = useState("max");
  const [rangeStartYear, setRangeStartYear] = useState("");
  const [rangeEndYear, setRangeEndYear] = useState("");
  const [normalMultipleYears, setNormalMultipleYears] = useState(5);
  const [chartSettingsOpen, setChartSettingsOpen] = useState(false);
  const [screenerMaxPer, setScreenerMaxPer] = useState(25);
  const [screenerMinRoe, setScreenerMinRoe] = useState(0);
  const [screenerMinEpsCagr, setScreenerMinEpsCagr] = useState(0);
  const [screenerMaxDebt, setScreenerMaxDebt] = useState(3);
  const [screenerMinMarketCap, setScreenerMinMarketCap] = useState(0);
  const [screenerMinMarketCapUsd, setScreenerMinMarketCapUsd] = useState(0);
  const [screenerRelativeDiscount, setScreenerRelativeDiscount] = useState(0);
  const [screenerRequireRoeGtRoic, setScreenerRequireRoeGtRoic] = useState(true);
  const [manualEps, setManualEps] = useState(["", "", "", "", ""]);
  const [hiddenScenarioLines, setHiddenScenarioLines] = useState<string[]>([]);
  const [visibility, setVisibility] = useState<LineVisibility>({
    price: true,
    metricArea: true,
    fairValue: true,
    normalMultiple: true,
    currentValuation: true,
    customValuation: false,
    dividendFloor: true,
    payoutRatio: true,
    dividendYield: false,
    recessionBands: true,
    forecast: true,
    scenarioLines: true
  });
  const [valuation, setValuation] = useState<ValuationRow[]>(() =>
    isPriorityCoverageTicker(defaultTicker) ? [] : fallbackValuation
  );
  const [adjusted, setAdjusted] = useState<AdjustedRow[]>(() =>
    isPriorityCoverageTicker(defaultTicker) ? missingAdjustedRowsForTicker(defaultTicker) : fallbackAdjusted
  );
  const [snapshot, setSnapshot] = useState<Snapshot>(() =>
    isPriorityCoverageTicker(defaultTicker) ? missingSnapshotForTicker(defaultTicker) : fallbackSnapshot
  );
  const [financials, setFinancials] = useState<FinancialRow[]>(() =>
    isPriorityCoverageTicker(defaultTicker) ? [] : fallbackFinancials
  );
  const [funGraphs, setFunGraphs] = useState<FunGraphs | null>(null);
  const [fiscalFitness, setFiscalFitness] = useState<FiscalFitnessRow[]>(() =>
    isPriorityCoverageTicker(defaultTicker) ? [] : fallbackFiscalFitness
  );
  const [healthCheck, setHealthCheck] = useState<HealthCheck>(() =>
    isPriorityCoverageTicker(defaultTicker) ? missingHealthCheckForTicker(defaultTicker) : fallbackHealthCheck
  );
  const [researchReport, setResearchReport] = useState<ResearchReport | null>(null);
  const [researchMetadata, setResearchMetadata] = useState<ResearchMetadata>(fallbackResearchMetadata);
  const [performance, setPerformance] = useState<PerformanceSummary | null>(null);
  const [useOfCash, setUseOfCash] = useState<UseOfCashRow[]>(() =>
    isPriorityCoverageTicker(defaultTicker) ? [] : fallbackUseOfCash
  );
  const [screener, setScreener] = useState<ScreenerRow[]>([]);
  const [portfolio, setPortfolio] = useState<PortfolioSummary>(fallbackPortfolio);
  const [portfolioCsv, setPortfolioCsv] = useState(samplePortfolioCsv);
  const [portfolioImportStatus, setPortfolioImportStatus] = useState("ready");
  const [watchlist, setWatchlist] = useState<WatchlistSummary>(fallbackWatchlist);
  const [watchlistTicker, setWatchlistTicker] = useState("MSFT");
  const [watchlistNote, setWatchlistNote] = useState("review valuation");
  const [watchlistStatus, setWatchlistStatus] = useState("ready");
  const watchlistTouched = useRef(false);
  const [chartLayouts, setChartLayouts] = useState<ChartLayout[]>(fallbackChartLayouts);
  const [chartLayoutName, setChartLayoutName] = useState(defaultChartLayoutNameForTicker(defaultTicker));
  const [selectedChartLayoutId, setSelectedChartLayoutId] = useState("");
  const [chartLayoutStatus, setChartLayoutStatus] = useState("ready");
  const chartLayoutsTouched = useRef(false);
  const [auditRows, setAuditRows] = useState<AuditRow[]>(() =>
    isPriorityCoverageTicker(defaultTicker) ? missingAuditRowsForTicker(defaultTicker) : []
  );
  const [dataAuditFocusFactId, setDataAuditFocusFactId] = useState<string | null>(null);
  const [dataAuditFocusFactFamily, setDataAuditFocusFactFamily] = useState<string | null>(null);
  const [forecastMeta, setForecastMeta] = useState<ForecastMeta>(() =>
    isPriorityCoverageTicker(defaultTicker) ? missingForecastMetaForTicker(defaultTicker) : fallbackForecastMeta
  );
  const [recessionBands, setRecessionBands] = useState<RecessionBand[]>(() =>
    isPriorityCoverageTicker(defaultTicker) ? [] : fallbackRecessionBands
  );
  const [pricePoints, setPricePoints] = useState<PricePoint[]>(() =>
    isPriorityCoverageTicker(defaultTicker) ? [] : fallbackPricePoints
  );
  const [forecastEvidence, setForecastEvidence] = useState<ForecastEvidence>(() =>
    isPriorityCoverageTicker(defaultTicker) ? missingForecastEvidenceForTicker(defaultTicker) : fallbackForecastEvidence
  );
  const [analystScorecard, setAnalystScorecard] = useState<AnalystScorecard>(() =>
    isPriorityCoverageTicker(defaultTicker) ? missingAnalystScorecardForTicker(defaultTicker) : fallbackAnalystScorecard
  );
  const [sourceReadiness, setSourceReadiness] = useState<SourceReadiness>(() =>
    isPriorityCoverageTicker(defaultTicker)
      ? sourceRequiredReadinessForTicker(defaultTicker, { source_note: "KR priority ticker requires source-backed ingestion before display." })
      : fallbackReadiness
  );
  const [krCacheCoverage, setKrCacheCoverage] = useState<KrValuationCacheCoverage | null>(null);
  const [krCacheUniverse, setKrCacheUniverse] = useState<KrValuationCacheUniverseCoverage | null>(null);
  const [sourceCoverage, setSourceCoverage] = useState<SourceCoverage>(fallbackCoverage);
  const [priorityUniverse, setPriorityUniverse] = useState<PriorityUniverse>(fallbackPriorityUniverse);
  const [macroSeries, setMacroSeries] = useState<MacroSeriesRow[]>([]);
  const [macroSeriesMeta, setMacroSeriesMeta] = useState<SourceSeriesMeta>(fallbackSourceSeriesMeta);
  const [industrySeries, setIndustrySeries] = useState<IndustrySeriesRow[]>([]);
  const [industrySeriesMeta, setIndustrySeriesMeta] = useState<SourceSeriesMeta>(fallbackSourceSeriesMeta);
  const [selectedYear, setSelectedYear] = useState(2024);
  const [returnSelectionYears, setReturnSelectionYears] = useState<number[]>([]);
  const [status, setStatus] = useState(isPriorityCoverageTicker(defaultTicker) ? "missing_source" : "fallback");
  const valuationLoadRequestId = useRef(0);
  const [ownerSession, setOwnerSession] = useState<OwnerSession>({
    loading: true,
    auth_required: false,
    authenticated: false,
    email: null
  });
  const [urlStateReady, setUrlStateReady] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);

  const selectTicker = useCallback((nextTicker: string) => {
    setTicker(nextTicker);
    setAskPrompt(defaultAskPromptForTicker(nextTicker));
    setChartLayoutName(defaultChartLayoutNameForTicker(nextTicker));
  }, []);

  const focusDataAuditFact = useCallback((factId: string, factFamily?: string | null) => {
    setDataAuditFocusFactId(factId);
    if (factFamily !== undefined) {
      setDataAuditFocusFactFamily(factFamily);
      return;
    }
    const row = auditRows.find((candidate) => candidate.fact_id === factId);
    setDataAuditFocusFactFamily(row ? dataAuditFamilyForFactRow(row) : null);
  }, [auditRows]);

  const selectWorkspaceTab = useCallback((tab: string) => {
    if (tab === "Data Audit") {
      setDataAuditFocusFactFamily("all");
    }
    setActiveTab(tab);
  }, []);

  useEffect(() => {
    let mounted = true;
    const applyLocationState = () => {
      if (!mounted) {
        return;
      }
      const query = new URLSearchParams(window.location.search);
      const requestedTicker = query.get("ticker")?.trim().toUpperCase() ?? null;
      const requestedTab = workspaceTabFromQuery(query.get("tab") ?? query.get("view"));
      if (requestedTicker && !tickers.includes(requestedTicker)) {
        setRouteError(`Security ${requestedTicker.slice(0, 32)} is not configured in this terminal.`);
      } else {
        setRouteError(null);
        if (requestedTicker) {
          selectTicker(requestedTicker);
        }
      }
      if (requestedTab) {
        setActiveTab(requestedTab);
      }
      setUrlStateReady(true);
    };
    queueMicrotask(applyLocationState);
    window.addEventListener("popstate", applyLocationState);
    return () => {
      mounted = false;
      window.removeEventListener("popstate", applyLocationState);
    };
  }, [selectTicker]);

  useEffect(() => {
    if (!urlStateReady || routeError) {
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("ticker", ticker);
    url.searchParams.set("tab", activeTab);
    url.searchParams.delete("view");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, [activeTab, routeError, ticker, urlStateReady]);

  useEffect(() => {
    let mounted = true;
    async function bridgeSession() {
      try {
        const response = await withTimeout(fetch("/api/pf/session"), API_TIMEOUT_MS);
        const payload = await response.json();
        if (!mounted) {
          return;
        }
        setOwnerSession({
          loading: false,
          auth_required: Boolean(payload.auth_required),
          authenticated: response.ok && Boolean(payload.authenticated),
          email: payload.email ?? null
        });
      } catch {
        if (!mounted) {
          return;
        }
        setOwnerSession({
          loading: false,
          auth_required: true,
          authenticated: false,
          email: null
        });
      }
    }
    bridgeSession();
    return () => {
      mounted = false;
    };
  }, []);

  const currentCoverageRow = useMemo(
    () => sourceCoverage.tickers.find((row) => row.ticker === ticker.toUpperCase()) ?? null,
    [sourceCoverage.tickers, ticker]
  );

  useEffect(() => {
    setReturnSelectionYears([]);
  }, [ticker, metric]);
  const currentMetricKeys = useMemo(
    () => new Set((currentCoverageRow?.available_metric_keys ?? []).map((key) => key.toLowerCase())),
    [currentCoverageRow]
  );
  const sourceBackedMetricsEnabled =
    sourceReadiness.data_backend === "postgres" ||
    sourceReadiness.data_backend === "kr_valuation_input_cache" ||
    sourceReadiness.data_backend === "kr_valuation_warehouse" ||
    isSourceSatisfiedMode(sourceReadiness.data_mode);
  const sourceBackedMetricAvailable = useCallback((metricValue: string) => {
    const aliases = metricCoverageAliases[metricValue] ?? [metricValue];
    return aliases.some((alias) => currentMetricKeys.has(alias.toLowerCase()));
  }, [currentMetricKeys]);
  const getMetricDisabledReason = useCallback((option: (typeof metricOptions)[number]) => {
    if ("requiresReit" in option && option.requiresReit && snapshot.sector_policy !== "reit") {
      return option.disabledHint ?? "REIT only";
    }
    if (
      "requiresSourceBackedMetric" in option &&
      option.requiresSourceBackedMetric &&
      !sourceBackedMetricsEnabled
    ) {
      return option.disabledHint ?? "source-backed metric required";
    }
    if (
      "requiresSourceBackedMetric" in option &&
      option.requiresSourceBackedMetric &&
      !sourceBackedMetricAvailable(option.value)
    ) {
      return option.disabledHint ?? "source-backed metric row required";
    }
    return "";
  }, [snapshot.sector_policy, sourceBackedMetricAvailable, sourceBackedMetricsEnabled]);

  useEffect(() => {
    const selected = metricOptions.find((option) => option.value === metric);
    if (!selected || getMetricDisabledReason(selected)) {
      setMetric("adjusted_operating");
    }
  }, [getMetricDisabledReason, metric]);

  const debouncedForecastYears = useDebouncedValue(forecastYears, 250);
  const debouncedGrowth = useDebouncedValue(growth, 250);
  const debouncedTargetMultiple = useDebouncedValue(targetMultiple, 250);
  const debouncedManualEps = useDebouncedValue(manualEps, 350);

  const valuationDataQueryString = useMemo(
    () =>
      buildValuationContextQuery({
        metric,
        forecastMode,
        forecastCase,
        forecastYears: debouncedForecastYears,
        normalMultipleYears,
        growth: debouncedGrowth,
        manualEps: debouncedManualEps,
        rangeStartYear,
        rangeEndYear,
        targetMultiple: debouncedTargetMultiple
      }).toString(),
    [
      metric,
      forecastMode,
      forecastCase,
      debouncedForecastYears,
      normalMultipleYears,
      debouncedGrowth,
      debouncedManualEps,
      rangeStartYear,
      rangeEndYear,
      debouncedTargetMultiple
    ]
  );

  useEffect(() => {
    let mounted = true;
    const requestId = ++valuationLoadRequestId.current;
    const isCurrentRequest = () => mounted && requestId === valuationLoadRequestId.current;
    async function load() {
      if (ownerSession.loading || !ownerSession.authenticated) {
        return;
      }
      setStatus("loading");
      setKrCacheCoverage(null);
      const query = new URLSearchParams(valuationDataQueryString);
      const screenerQuery = new URLSearchParams({
        max_per: String(screenerMaxPer),
        min_roe: String(screenerMinRoe),
        min_eps_cagr: String(screenerMinEpsCagr),
        max_debt_to_equity: String(screenerMaxDebt),
        relative_discount_pct: String(screenerRelativeDiscount),
        require_roe_gt_roic: String(screenerRequireRoeGtRoic)
      });
      if (screenerMinMarketCap > 0) {
        screenerQuery.set("min_market_cap", String(screenerMinMarketCap));
      }
      if (screenerMinMarketCapUsd > 0) {
        screenerQuery.set("min_market_cap_usd", String(screenerMinMarketCapUsd));
      }
      try {
        const [
          valuationResponse,
          adjustedResponse,
          snapshotResponse,
          financialsResponse,
          funGraphsResponse,
          fiscalFitnessResponse,
          healthCheckResponse,
          researchReportResponse,
          researchMetadataResponse,
          performanceResponse,
          useOfCashResponse,
          screenerResponse,
          portfolioResponse,
          watchlistResponse,
          auditResponse,
          forecastEvidenceResponse,
          analystScorecardResponse,
          readinessResponse,
          coverageResponse,
          priorityUniverseResponse,
          krCacheUniverseResponse,
          macroSeriesResponse,
          industrySeriesResponse,
          chartLayoutsResponse
        ] = await Promise.all([
          withTimeout(fetch(`/api/v1/companies/${ticker}/valuation-map?${query}`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/security/${ticker}/adjusted`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/snapshot`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/financials`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/fun-graphs`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/fiscal-fitness`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/health-check`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/research-report?${query}`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/research-metadata`), API_TIMEOUT_MS).catch(() => null),
          withTimeout(fetch(`/api/v1/companies/${ticker}/performance`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/use-of-cash`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/screener?${screenerQuery}`), API_TIMEOUT_MS),
          withTimeout(fetch("/api/v1/portfolio"), API_TIMEOUT_MS),
          withTimeout(fetch("/api/v1/watchlist"), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/data-audit?${query}`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/forecast-snapshots`), API_TIMEOUT_MS),
          withTimeout(fetch(`/api/v1/companies/${ticker}/analyst-scorecard`), API_TIMEOUT_MS),
          withTimeout(fetch("/api/v1/system/readiness"), API_TIMEOUT_MS),
          withTimeout(fetch("/api/v1/system/source-coverage?market=ALL&require_consensus_forecast=true"), API_TIMEOUT_MS).catch(() => null),
          withTimeout(fetch("/api/v1/system/priority-universe?market=ALL"), API_TIMEOUT_MS).catch(() => null),
          withTimeout(fetch("/api/v1/system/kr-valuation-cache-coverage"), API_TIMEOUT_MS).catch(() => null),
          withTimeout(fetch("/api/v1/macro-series?limit=8"), API_TIMEOUT_MS).catch(() => null),
          withTimeout(fetch("/api/v1/industry-series?limit=8"), API_TIMEOUT_MS).catch(() => null),
          withTimeout(fetch("/api/v1/chart-layouts"), API_TIMEOUT_MS)
        ]);
        const coreResponses = [
          valuationResponse,
          adjustedResponse,
          snapshotResponse,
          financialsResponse,
          fiscalFitnessResponse,
          healthCheckResponse,
          useOfCashResponse
        ];
        if (coreResponses.some((response) => response.status === 401 || response.status === 403)) {
          if (mounted) {
            setOwnerSession({
              loading: false,
              auth_required: true,
              authenticated: false,
              email: null
            });
          }
          return;
        }
        if (
          !valuationResponse.ok ||
          !adjustedResponse.ok ||
          !snapshotResponse.ok ||
          !financialsResponse.ok ||
          !fiscalFitnessResponse.ok ||
          !healthCheckResponse.ok ||
          !useOfCashResponse.ok
        ) {
          throw new Error("api unavailable");
        }
        const valuationPayload = await valuationResponse.json();
        const adjustedPayload = await adjustedResponse.json();
        const snapshotPayload = await snapshotResponse.json();
        const financialsPayload = await financialsResponse.json();
        const funGraphsPayload = funGraphsResponse.ok
          ? await funGraphsResponse.json()
          : { data: null };
        const fiscalFitnessPayload = await fiscalFitnessResponse.json();
        const healthCheckPayload = await healthCheckResponse.json();
        const researchReportPayload = researchReportResponse.ok
          ? await researchReportResponse.json()
          : { data: null };
        const researchMetadataPayload = researchMetadataResponse?.ok
          ? await researchMetadataResponse.json()
          : { data: fallbackResearchMetadata };
        const performancePayload = performanceResponse.ok
          ? await performanceResponse.json()
          : { data: null };
        const useOfCashPayload = await useOfCashResponse.json();
        const screenerPayload = await screenerResponse.json();
        const portfolioPayload = portfolioResponse.ok
          ? await portfolioResponse.json()
          : { data: emptyPortfolio };
        const watchlistPayload = watchlistResponse.ok
          ? await watchlistResponse.json()
          : { data: fallbackWatchlist };
        const auditPayload = await auditResponse.json();
        const forecastEvidencePayload = forecastEvidenceResponse.ok
          ? await forecastEvidenceResponse.json()
          : { data: fallbackForecastEvidence };
        const analystScorecardPayload = analystScorecardResponse.ok
          ? await analystScorecardResponse.json()
          : { data: fallbackAnalystScorecard };
        const readinessPayload = readinessResponse.ok
          ? await readinessResponse.json()
          : fallbackReadiness;
        let coveragePayload = fallbackCoverage;
        if (coverageResponse?.ok) {
          try {
            coveragePayload = normalizeSourceCoverage(await coverageResponse.json());
          } catch {
            coveragePayload = fallbackCoverage;
          }
        }
        let priorityUniversePayload = fallbackPriorityUniverse;
        if (priorityUniverseResponse?.ok) {
          try {
            priorityUniversePayload = normalizePriorityUniverse(await priorityUniverseResponse.json());
          } catch {
            priorityUniversePayload = fallbackPriorityUniverse;
          }
        }
        let krCacheUniversePayload: KrValuationCacheUniverseCoverage | null = null;
        if (krCacheUniverseResponse?.ok) {
          try {
            krCacheUniversePayload = normalizeKrValuationCacheUniverse(await krCacheUniverseResponse.json());
          } catch {
            krCacheUniversePayload = null;
          }
        }
        let macroSeriesPayload = { data: [] as MacroSeriesRow[], meta: fallbackSourceSeriesMeta };
        if (macroSeriesResponse?.ok) {
          try {
            macroSeriesPayload = normalizeMacroSeriesResponse(await macroSeriesResponse.json());
          } catch {
            macroSeriesPayload = { data: [], meta: fallbackSourceSeriesMeta };
          }
        }
        let industrySeriesPayload = { data: [] as IndustrySeriesRow[], meta: fallbackSourceSeriesMeta };
        if (industrySeriesResponse?.ok) {
          try {
            industrySeriesPayload = normalizeIndustrySeriesResponse(await industrySeriesResponse.json());
          } catch {
            industrySeriesPayload = { data: [], meta: fallbackSourceSeriesMeta };
          }
        }
        const chartLayoutsPayload = chartLayoutsResponse.ok
          ? await chartLayoutsResponse.json()
          : { data: { items: fallbackChartLayouts } };
        if (!isCurrentRequest()) {
          return;
        }
        const valuationCacheReady = isSourceBackedCacheValuationPayload(valuationPayload);
        const krCacheCoveragePayload = krValuationCacheCoverageFromPayload(valuationPayload);
        if (
          isKrPriorityCoverageTicker(ticker) &&
          !valuationCacheReady &&
          [valuationPayload, snapshotPayload, financialsPayload].some((payload) =>
            isUnsafePriorityFinancialPayload(ticker, payload)
          )
        ) {
          setValuation([]);
          setAdjusted(missingAdjustedRowsForTicker(ticker));
          setSnapshot(missingSnapshotForTicker(ticker));
          setFinancials([]);
          setFunGraphs(null);
          setFiscalFitness([]);
          setHealthCheck(missingHealthCheckForTicker(ticker));
          setResearchReport(null);
          setResearchMetadata(normalizeResearchMetadata(researchMetadataPayload.data));
          setPerformance(null);
          setUseOfCash([]);
          setScreener([]);
          setPortfolio(emptyPortfolio);
          if (!watchlistTouched.current) {
            setWatchlist(watchlistPayload.data ?? fallbackWatchlist);
          }
          const sourceRequiredAuditRows = Array.isArray(auditPayload.data) && auditPayload.data.length
            ? auditPayload.data
            : missingAuditRowsForTicker(ticker);
          setAuditRows(sourceRequiredAuditRows);
          setForecastMeta(missingForecastMetaForTicker(ticker));
          setRecessionBands([]);
          setPricePoints([]);
          setForecastEvidence(missingForecastEvidenceForTicker(ticker));
          setAnalystScorecard(missingAnalystScorecardForTicker(ticker));
          setSourceReadiness(sourceRequiredReadinessForTicker(ticker, valuationPayload.meta ?? snapshotPayload.meta));
          setKrCacheCoverage(krCacheCoveragePayload);
          setSourceCoverage(coveragePayload);
          setPriorityUniverse(priorityUniversePayload);
          setKrCacheUniverse(krCacheUniversePayload);
          setMacroSeries(macroSeriesPayload.data);
          setMacroSeriesMeta(macroSeriesPayload.meta);
          setIndustrySeries(industrySeriesPayload.data);
          setIndustrySeriesMeta(industrySeriesPayload.meta);
          if (!chartLayoutsTouched.current) {
            setChartLayouts(chartLayoutsPayload.data?.items ?? fallbackChartLayouts);
          }
          setStatus("missing_source");
          return;
        }
        setValuation(Array.isArray(valuationPayload.data) ? valuationPayload.data : []);
        setAdjusted(Array.isArray(adjustedPayload.series) ? adjustedPayload.series : missingAdjustedRowsForTicker(ticker));
        setSnapshot(snapshotPayload.data ?? missingSnapshotForTicker(ticker));
        setFinancials(Array.isArray(financialsPayload.data) ? financialsPayload.data : []);
        setFunGraphs(normalizeFunGraphs(funGraphsPayload.data));
        setFiscalFitness(Array.isArray(fiscalFitnessPayload.data) ? fiscalFitnessPayload.data : fallbackFiscalFitness);
        setHealthCheck(healthCheckPayload.data ?? fallbackHealthCheck);
        setResearchReport(normalizeResearchReport(researchReportPayload.data));
        setResearchMetadata(normalizeResearchMetadata(researchMetadataPayload.data));
        setPerformance(normalizePerformance(performancePayload.data));
        setUseOfCash(Array.isArray(useOfCashPayload.data) ? useOfCashPayload.data : fallbackUseOfCash);
        setScreener(screenerPayload.data ?? []);
        setPortfolio(portfolioPayload.data ?? emptyPortfolio);
        if (!watchlistTouched.current) {
          setWatchlist(watchlistPayload.data ?? fallbackWatchlist);
        }
        setAuditRows(auditPayload.data ?? []);
        setForecastMeta(valuationPayload.meta?.forecast ?? fallbackForecastMeta);
        setRecessionBands(Array.isArray(valuationPayload.meta?.recession_bands) ? valuationPayload.meta.recession_bands : []);
        setPricePoints(normalizePricePoints(valuationPayload.meta?.price_points, valuationPayload.data));
        setForecastEvidence(forecastEvidencePayload.data ?? fallbackForecastEvidence);
        setAnalystScorecard(normalizeAnalystScorecard(analystScorecardPayload.data) ?? fallbackAnalystScorecard);
        setSourceReadiness(sourceReadinessFromValuationPayload(readinessPayload, valuationPayload));
        setKrCacheCoverage(krCacheCoveragePayload);
        setSourceCoverage(coveragePayload);
        setPriorityUniverse(priorityUniversePayload);
        setKrCacheUniverse(krCacheUniversePayload);
        setMacroSeries(macroSeriesPayload.data);
        setMacroSeriesMeta(macroSeriesPayload.meta);
        setIndustrySeries(industrySeriesPayload.data);
        setIndustrySeriesMeta(industrySeriesPayload.meta);
        if (!chartLayoutsTouched.current) {
          setChartLayouts(chartLayoutsPayload.data?.items ?? fallbackChartLayouts);
        }
        setStatus("live");
      } catch {
        if (!isCurrentRequest()) {
          return;
        }
        if (isKrPriorityCoverageTicker(ticker)) {
          setValuation([]);
          setAdjusted(missingAdjustedRowsForTicker(ticker));
          setSnapshot(missingSnapshotForTicker(ticker));
          setFinancials([]);
          setFunGraphs(null);
          setFiscalFitness([]);
          setHealthCheck(missingHealthCheckForTicker(ticker));
          setResearchReport(null);
          setResearchMetadata(fallbackResearchMetadata);
          setPerformance(null);
          setUseOfCash([]);
          setScreener([]);
          setPortfolio(emptyPortfolio);
          if (!watchlistTouched.current) {
            setWatchlist(fallbackWatchlist);
          }
          setAuditRows(missingAuditRowsForTicker(ticker));
          setForecastMeta(missingForecastMetaForTicker(ticker));
          setRecessionBands([]);
          setPricePoints([]);
          setForecastEvidence(missingForecastEvidenceForTicker(ticker));
          setAnalystScorecard(missingAnalystScorecardForTicker(ticker));
          setSourceReadiness({
            ...fallbackReadiness,
            status: "source_backed_required",
            data_backend: "not_loaded",
            data_mode: "source_backed_required",
            checks: [
              {
                name: "priority_ticker_source_trace_gate",
                ok: false,
                required: true,
                detail: "OpenDART/pykrx/marcap ingestion must run before financial values are displayed."
              }
            ]
          });
          setKrCacheCoverage(null);
          setSourceCoverage(fallbackCoverage);
          setPriorityUniverse(fallbackPriorityUniverse);
          setKrCacheUniverse(null);
          setMacroSeries([]);
          setMacroSeriesMeta(fallbackSourceSeriesMeta);
          setIndustrySeries([]);
          setIndustrySeriesMeta(fallbackSourceSeriesMeta);
          setStatus("missing_source");
          return;
        }
        setValuation(fallbackValuation);
        setAdjusted(fallbackAdjusted);
        setSnapshot(fallbackSnapshot);
        setFinancials(fallbackFinancials);
        setFunGraphs(null);
        setFiscalFitness(fallbackFiscalFitness);
        setHealthCheck(fallbackHealthCheck);
        setResearchReport(null);
        setResearchMetadata(fallbackResearchMetadata);
        setPerformance(null);
        setUseOfCash(fallbackUseOfCash);
        setScreener([]);
        setPortfolio(emptyPortfolio);
        setWatchlist(fallbackWatchlist);
        setAuditRows([]);
        setForecastMeta(fallbackForecastMeta);
        setRecessionBands(fallbackRecessionBands);
        setPricePoints(fallbackPricePoints);
        setForecastEvidence(fallbackForecastEvidence);
        setAnalystScorecard(fallbackAnalystScorecard);
        setSourceReadiness(fallbackReadiness);
        setKrCacheCoverage(null);
        setSourceCoverage(fallbackCoverage);
        setPriorityUniverse(fallbackPriorityUniverse);
        setKrCacheUniverse(null);
        setMacroSeries([]);
        setMacroSeriesMeta(fallbackSourceSeriesMeta);
        setIndustrySeries([]);
        setIndustrySeriesMeta(fallbackSourceSeriesMeta);
        setStatus("fallback");
      }
    }
    load();
    return () => {
      mounted = false;
    };
  }, [
    ticker,
    valuationDataQueryString,
    screenerMaxPer,
    screenerMinRoe,
    screenerMinEpsCagr,
    screenerMaxDebt,
    screenerMinMarketCap,
    screenerMinMarketCapUsd,
    screenerRelativeDiscount,
    screenerRequireRoeGtRoic,
    ownerSession.loading,
    ownerSession.authenticated
  ]);

  const displayValuation = useMemo(
    () => filterValuationByRange(valuation, rangeStartYear, rangeEndYear),
    [rangeEndYear, rangeStartYear, valuation]
  );
  const displayValuationYears = useMemo(
    () => new Set(displayValuation.map((row) => row.fiscal_year)),
    [displayValuation]
  );
  const displayAuditRows = useMemo(
    () => auditRows.filter((row) => displayValuationYears.has(row.fiscal_year)),
    [auditRows, displayValuationYears]
  );
  const displayPricePoints = useMemo(
    () => filterPricePointsByValuationRange(pricePoints, displayValuation),
    [displayValuation, pricePoints]
  );
  const displayRangeSummary = useMemo(
    () => buildDisplayRangeSummary(displayValuation, rangeMode),
    [displayValuation, rangeMode]
  );
  useEffect(() => {
    setReturnSelectionYears((state) => state.filter((year) => displayValuationYears.has(year)));
  }, [displayValuationYears]);
  useEffect(() => {
    if (!displayValuation.length || displayValuationYears.has(selectedYear)) {
      return;
    }
    const nextSelectedRow = latestReportedValuationRow(displayValuation) ?? displayValuation.at(-1);
    if (nextSelectedRow) {
      setSelectedYear(nextSelectedRow.fiscal_year);
    }
  }, [displayValuation, displayValuationYears, selectedYear]);
  const selected = adjusted.find((row) => row.fiscal_year === selectedYear) ?? adjusted.at(-1);
  const latest = displayValuation.at(-1) ?? valuation.at(-1);
  const s1Count = adjusted.filter((row) => row.method === "S1_SEC_RECONCILIATION").length;
  const s2Count = adjusted.filter((row) => row.method === "S2_XBRL_SPECIAL_ITEMS").length;
  const s4Count = adjusted.filter((row) => row.method === "S4_GAAP_FALLBACK").length;
  const securityMeta = securities[ticker as keyof typeof securities];
  const searchSecurities = useMemo(
    () =>
      tickers.map((item) => {
        const meta = securities[item as keyof typeof securities];
        return {
          ticker: item,
          label: meta.label,
          market: meta.market,
          currency: meta.currency
        };
      }),
    []
  );
  const searchWorkspaces = useMemo(
    () => workspaceCards.map((workspace) => ({ key: workspace.key, label: workspace.label, detail: workspace.detail })),
    []
  );
  const chartTransactions = useMemo(
    () => portfolio.holdings.find((holding) => holding.ticker === ticker)?.transactions ?? [],
    [portfolio, ticker]
  );
  const latestActualYear = useMemo(() => latestHistoricalYear(valuation), [valuation]);
  const chartReturnSelection = useMemo(
    () => buildChartReturnSelection(displayValuation, returnSelectionYears),
    [displayValuation, returnSelectionYears]
  );
  const latestReportedRow = useMemo(() => latestReportedValuationRow(valuation), [valuation]);
  const selectedValuationRow = useMemo(
    () => displayValuation.find((row) => row.fiscal_year === selectedYear) ?? latestReportedRow ?? latest,
    [displayValuation, latest, latestReportedRow, selectedYear]
  );
  const terminalForecastRow = useMemo(
    () => valuation.filter((row) => row.forecast_flag).slice(0, forecastYears).at(-1) ?? latest,
    [forecastYears, latest, valuation]
  );
  const sourceTraceGateBlocksTicker = isKrPriorityCoverageTicker(ticker) && !isSourceSatisfiedMode(sourceReadiness.data_mode);
  const dataMatchesTicker = snapshot.ticker === ticker && !sourceTraceGateBlocksTicker;
  const mobileEvidenceAuditRow = useMemo(
    () => valuationAuditRowFor(selectedValuationRow, auditRows, "metric") ?? valuationAuditRowFor(latestReportedRow, auditRows, "metric"),
    [auditRows, latestReportedRow, selectedValuationRow]
  );
  const metricAuditRow = useMemo(
    () => valuationAuditRowFor(latestReportedRow, auditRows, "metric"),
    [auditRows, latestReportedRow]
  );
  const forecastReturnAuditRow = useMemo(
    () => valuationAuditRowFor(terminalForecastRow, auditRows, "total_return_cagr_pct"),
    [auditRows, terminalForecastRow]
  );
  const askConsensusEvidence = useMemo(
    () =>
      selectedForecastConsensusEvidence({
        forecastEvidence,
        forecastMeta,
        forecastCase,
        auditRows
      }),
    [auditRows, forecastCase, forecastEvidence, forecastMeta]
  );
  const askSourceCount = useMemo(
    () => auditRows.filter((row) => row.fact_id.startsWith(`${ticker}-`)).length,
    [auditRows, ticker]
  );
  const askNarrative = useMemo(
    () =>
      buildAskNarrative({
        ticker,
        snapshot,
        latestReportedRow,
        terminalForecastRow,
        forecastYears,
        askSourceCount,
        dataMatchesTicker,
        metricAuditRow,
        forecastReturnAuditRow,
        askConsensusEvidence
      }),
    [askConsensusEvidence, askSourceCount, dataMatchesTicker, forecastReturnAuditRow, forecastYears, latestReportedRow, metricAuditRow, snapshot, terminalForecastRow, ticker]
  );
  const visualizationCoverageRows = useMemo(
    () =>
      buildVisualizationCoverageRows({
        valuation,
        pricePoints,
        auditRows,
        forecastYears,
        visibility,
        chartTransactions,
        performanceRows: performance?.rows.length ?? 0,
        sourceMode: sourceReadiness.data_mode
      }),
    [auditRows, chartTransactions, forecastYears, performance?.rows.length, pricePoints, sourceReadiness.data_mode, valuation, visibility]
  );

  function runAskPrompt(prompt: string) {
    const trimmed = prompt.trim();
    if (!trimmed) {
      setAskStatus("Enter a ticker, valuation question, forecast request, or source audit request.");
      return;
    }
    const instruction = parseAskInstruction(trimmed, latestReportedRow?.fiscal_year ?? latestActualYear ?? undefined);
    const matchedTicker = instruction.ticker;
    const nextTicker = matchedTicker ?? ticker;
    const nextTab = instruction.tab;
    if (matchedTicker && matchedTicker !== ticker) {
      selectTicker(matchedTicker);
    }
    if (instruction.forecastMode) {
      setForecastMode(instruction.forecastMode);
    }
    if (instruction.forecastCase) {
      setForecastCase(instruction.forecastCase);
    }
    if (instruction.forecastYears) {
      setForecastYears(instruction.forecastYears);
    }
    if (instruction.growth !== undefined) {
      setGrowth(instruction.growth);
    }
    if (instruction.targetMultiple !== undefined) {
      setTargetMultiple(instruction.targetMultiple);
    }
    if (instruction.manualEpsValues) {
      setManualEps(instruction.manualEpsValues);
    }
    if (instruction.visibility && Object.keys(instruction.visibility).length) {
      setVisibility((state) => ({
        ...state,
        ...instruction.visibility
      }));
    }
    setAskPrompt(trimmed);
    setActiveTab(nextTab);
    setAskStatus(
      `${nextTicker} opened in ${nextTab}. ${
        instruction.applied.length ? `Applied: ${instruction.applied.join(", ")}. ` : ""
      }Numbers remain source-traced; AI notes do not create financial values.`
    );
  }

  function submitAskPrompt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    runAskPrompt(askPrompt);
  }

  function applyRangeMode(mode: string) {
    setRangeMode(mode);
    if (mode === "max") {
      setRangeStartYear("");
      setRangeEndYear("");
      return;
    }
    if (mode === "custom") {
      if (!rangeEndYear && latestActualYear) {
        setRangeEndYear(String(latestActualYear));
      }
      return;
    }
    const years = Number(mode);
    const endYear = latestActualYear ?? new Date().getFullYear();
    setRangeEndYear(String(endYear));
    setRangeStartYear(String(endYear - years + 1));
  }

  function selectChartYear(year: number) {
    setSelectedYear(year);
    setReturnSelectionYears((state) => {
      if (!state.length || state.length >= 2) {
        return [year];
      }
      if (state[0] === year) {
        return [year];
      }
      return [state[0], year].sort((left, right) => left - right);
    });
  }

  async function importPortfolioCsv() {
    setPortfolioImportStatus("importing");
    try {
      const response = await withTimeout(
        fetch("/api/v1/portfolio/import", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            csv_text: portfolioCsv,
            persist: true,
            replace_existing: true
          })
        }),
        API_TIMEOUT_MS
      );
      if (!response.ok) {
        throw new Error("portfolio import failed");
      }
      const payload = await response.json();
      setPortfolio(payload.data ?? emptyPortfolio);
      setPortfolioImportStatus(`imported ${payload.data?.holdings?.length ?? 0} holdings`);
    } catch {
      setPortfolioImportStatus("import failed");
    }
  }

  async function addWatchlistItem() {
    watchlistTouched.current = true;
    setWatchlistStatus("saving");
    try {
      const response = await withTimeout(
        fetch("/api/v1/watchlist/items", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ticker: watchlistTicker,
            note: watchlistNote,
            persist: true
          })
        }),
        API_TIMEOUT_MS
      );
      if (!response.ok) {
        throw new Error("watchlist add failed");
      }
      const payload = await response.json();
      setWatchlist(ensureWatchlistItem(payload.data ?? watchlist, watchlistTicker, watchlistNote));
      setWatchlistStatus(`saved ${watchlistTicker.toUpperCase()}`);
    } catch {
      setWatchlistStatus("save failed");
    }
  }

  async function removeWatchlistItem(removeTicker: string) {
    watchlistTouched.current = true;
    setWatchlistStatus("removing");
    try {
      const response = await withTimeout(
        fetch(`/api/v1/watchlist/items/${encodeURIComponent(removeTicker)}`, {
          method: "DELETE"
        }),
        API_TIMEOUT_MS
      );
      if (!response.ok) {
        throw new Error("watchlist remove failed");
      }
      const payload = await response.json();
      setWatchlist(removeWatchlistItemLocal(payload.data ?? watchlist, removeTicker));
      setWatchlistStatus(`removed ${removeTicker.toUpperCase()}`);
    } catch {
      setWatchlistStatus("remove failed");
    }
  }

  async function saveCurrentChartLayout() {
    chartLayoutsTouched.current = true;
    const name = chartLayoutName.trim() || `${ticker} layout`;
    setChartLayoutStatus("saving");
    try {
      const response = await withTimeout(
        fetch("/api/v1/chart-layouts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            company_id: ticker,
            metric,
            forecast_mode: forecastMode,
            forecast_case: forecastCase,
            forecast_years: forecastYears,
            start_year: rangeStartYear.trim() ? Number(rangeStartYear) : null,
            end_year: rangeEndYear.trim() ? Number(rangeEndYear) : null,
            normal_multiple_years: normalMultipleYears,
            user_growth_rate: String(growth),
            target_multiple: forecastMode === "custom" ? String(targetMultiple) : null,
            manual_eps_values: manualEps.slice(0, forecastYears).join(","),
            visibility: {
              price: visibility.price,
              metric_area: visibility.metricArea,
              fair_value: visibility.fairValue,
              normal_multiple: visibility.normalMultiple,
              current_valuation: visibility.currentValuation,
              custom_valuation: visibility.customValuation,
              dividend_floor: visibility.dividendFloor,
              payout_ratio: visibility.payoutRatio,
              dividend_yield: visibility.dividendYield,
              recession_bands: visibility.recessionBands,
              forecast: visibility.forecast,
              scenario_lines: visibility.scenarioLines
            },
            hidden_scenario_lines: hiddenScenarioLines
          })
        }),
        API_TIMEOUT_MS
      );
      if (!response.ok) {
        throw new Error("chart layout save failed");
      }
      const payload = await response.json();
      const saved = payload.data as ChartLayout;
      setChartLayouts((items) => [saved, ...items.filter((item) => item.id !== saved.id)]);
      setSelectedChartLayoutId(saved.id);
      setChartLayoutStatus(`saved ${saved.name}`);
    } catch {
      setChartLayoutStatus("save failed");
    }
  }

  function applyChartLayout(layoutId: string) {
    setSelectedChartLayoutId(layoutId);
    const layout = chartLayouts.find((item) => item.id === layoutId);
    if (!layout) {
      return;
    }
    const config = layout.config;
    if (config.company_id && securities[config.company_id as keyof typeof securities]) {
      setTicker(config.company_id);
    }
    setMetric(config.metric || "adjusted_operating");
    setForecastMode(config.forecast_mode || "custom");
    setForecastCase(config.forecast_case || "median");
    setForecastYears(Math.min(5, Math.max(1, Number(config.forecast_years || 5))));
    setRangeMode(config.start_year || config.end_year ? "custom" : "max");
    setRangeStartYear(config.start_year ? String(config.start_year) : "");
    setRangeEndYear(config.end_year ? String(config.end_year) : "");
    setNormalMultipleYears(Math.min(20, Math.max(1, Number(config.normal_multiple_years || 5))));
    if (config.user_growth_rate !== null) {
      setGrowth(Number(config.user_growth_rate));
    }
    if (config.target_multiple !== null) {
      setTargetMultiple(Number(config.target_multiple));
    }
    setManualEps(expandManualEps(config.manual_eps_values));
    setVisibility({
      price: config.visibility?.price ?? true,
      metricArea: config.visibility?.metric_area ?? true,
      fairValue: config.visibility?.fair_value ?? true,
      normalMultiple: config.visibility?.normal_multiple ?? true,
      currentValuation: config.visibility?.current_valuation ?? true,
      customValuation: config.visibility?.custom_valuation ?? false,
      dividendFloor: config.visibility?.dividend_floor ?? true,
      payoutRatio: config.visibility?.payout_ratio ?? true,
      dividendYield: config.visibility?.dividend_yield ?? false,
      recessionBands: config.visibility?.recession_bands ?? true,
      forecast: config.visibility?.forecast ?? true,
      scenarioLines: config.visibility?.scenario_lines ?? true
    });
    setHiddenScenarioLines(config.hidden_scenario_lines ?? []);
    setChartLayoutName(layout.name);
    setChartLayoutStatus(`loaded ${layout.name}`);
  }

  function openProductTour(stepIndex = 0) {
    const normalizedStep = Math.min(Math.max(stepIndex, 0), productTourSteps.length - 1);
    setProductTourStep(normalizedStep);
    setActiveTab(productTourSteps[normalizedStep].tab);
    setProductTourOpen(true);
  }

  function moveProductTour(direction: 1 | -1) {
    const nextStep = Math.min(Math.max(productTourStep + direction, 0), productTourSteps.length - 1);
    setProductTourStep(nextStep);
    setActiveTab(productTourSteps[nextStep].tab);
  }

  if (ownerSession.loading) {
    return <AuthScreen title="Loading protected workspace" detail="Checking owner session..." />;
  }

  if (!ownerSession.authenticated) {
    return (
      <AuthScreen
        title="LUXON private terminal"
        detail="Sign in with an allowlisted GitHub account to access the valuation workspace and protected API."
        actionLabel="Sign in with GitHub"
        actionHref="/api/auth/signin/github"
      />
    );
  }

  if (routeError) {
    return (
      <AuthScreen
        title="Unsupported security"
        detail={`${routeError} No source-backed values were displayed.`}
      />
    );
  }

  return (
    <main className="terminal-shell">
      <aside className="side-nav" aria-label="LUXON workspace navigation">
        <BrandMark />
        {fundRailItems.map(({ label, tab, ariaLabel, title }) => (
          <button
            key={label}
            type="button"
            className={activeTab === tab ? "active" : ""}
            onClick={() => setActiveTab(tab)}
            aria-label={ariaLabel}
            title={title}
          >
            {label}
          </button>
        ))}
      </aside>
      <section className={`workspace ${activeTab === "Historical" ? "workspace-historical" : ""}`}>
        <header className="topbar">
          <div className="terminal-wordmark">
            <BrandMark />
            <div>
              <strong>LUXON</strong>
              <span>Investment Terminal</span>
            </div>
          </div>
          <nav className="primary-nav" aria-label="Primary workflow">
            {primaryWorkflowTabs.map(({ tab, label }) => (
              <button key={tab} type="button" className={activeTab === tab ? "active" : ""} onClick={() => setActiveTab(tab)}>
                {label}
              </button>
            ))}
          </nav>
          <SearchOverlay
            selectedTicker={ticker}
            securities={searchSecurities}
            workspaces={searchWorkspaces}
            onSelectTicker={selectTicker}
            onSelectWorkspace={setActiveTab}
          />
          <div className="deployment-status" data-testid="topbar-deployment-status" aria-label="Deployment and data mode">
            <span className="private">Local private</span>
            <span className="source">Source-traced</span>
          </div>
          <div className="security-title">
            <strong>{snapshot.name || ticker}</strong>
            <span>{ticker} - {securityMeta.market} - {securityMeta.currency} - LUXON research workspace</span>
          </div>
          <div className="owner-chip">{ownerSession.email ?? "local dev"}</div>
          <button type="button" className="tour-launch-button" onClick={() => openProductTour()} aria-label="Open product tour">
            <HelpCircle size={16} />
            Tour
          </button>
          <div className={`status-pill ${status}`}>
            {status === "live"
              ? "API live"
              : status === "fallback"
                ? "fixture fallback"
                : status === "missing_source"
                  ? "source required"
                  : "loading"}
          </div>
        </header>
        {productTourOpen ? (
          <ProductTourModal
            stepIndex={productTourStep}
            onClose={() => setProductTourOpen(false)}
            onPrevious={() => moveProductTour(-1)}
            onNext={() => moveProductTour(1)}
          />
        ) : null}

        <CompanyHeaderPanel
          ticker={ticker}
          snapshot={snapshot}
          securityMeta={securityMeta}
          activeTab={activeTab}
          tabs={tabs}
          dataMatchesTicker={dataMatchesTicker}
          latestReportedRow={latestReportedRow}
          sourceReadinessMode={sourceReadiness.data_mode}
          onSelectTab={selectWorkspaceTab}
        />

        <section
          className={`simple-hero ${activeTab === "Summary" ? "simple-hero-primary" : ""}`}
          aria-label="Simple research workflow"
        >
          <div className="ask-shell">
            <div className="assistant-badge">
              <BrandMark />
              <span>FUND AI Underwriter</span>
            </div>
            <h1>Ask, forecast, then verify the source.</h1>
            <p>
              One workspace for 1-5Y forecasts, valuation lines, and audit-backed evidence.
            </p>
            <form className="ask-card" onSubmit={submitAskPrompt} data-testid="ask-underwriter-form">
              <label htmlFor="ask-underwriter-input">Valuation question</label>
              <textarea
                id="ask-underwriter-input"
                aria-label="Valuation question"
                value={askPrompt}
                onChange={(event) => setAskPrompt(event.target.value)}
                rows={2}
              />
              <button type="submit">Analyze</button>
              <span>{askStatus}</span>
            </form>
            <div className="suggested-prompts" aria-label="Suggested valuation prompts">
              {[
                "Is current P/E below normal multiple?",
                "Build 1Y-5Y bear/base/bull forecast",
                "Show the EPS source trace"
              ].map((prompt) => (
                <button key={prompt} type="button" onClick={() => runAskPrompt(`${ticker}: ${prompt}`)}>
                  {prompt}
                </button>
              ))}
            </div>
            <div className="quick-tickers quick-tickers-stacked" aria-label="Seed universe">
              {priorityCoverageGroups.map((group) => (
                <div
                  key={group.market}
                  className="quick-ticker-group"
                  data-testid={`quick-tickers-${group.market.toLowerCase()}-priority`}
                >
                <div className="quick-ticker-group-heading">
                  <strong>{group.label}</strong>
                  <span>10 tickers / source_trace required / rank promoted from market-cap evidence</span>
                </div>
                <div className="quick-ticker-row">
                  {group.tickers.map((item) => (
                    <button
                      key={item}
                      type="button"
                      className={item === ticker ? "active" : ""}
                      data-testid={`quick-ticker-${group.market.toLowerCase()}-priority-${item}`}
                      onClick={() => selectTicker(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
                </div>
              ))}
              <div className="quick-ticker-group" data-testid="quick-tickers-regression">
                <div className="quick-ticker-group-heading">
                  <strong>Regression / cross-market fixtures</strong>
                  <span>Non-priority sector patterns stay available for parity tests</span>
                </div>
                <div className="quick-ticker-row">
                  {regressionSeedTickers.map((item) => (
                    <button
                      key={item}
                      type="button"
                      className={item === ticker ? "active" : ""}
                      data-testid={`quick-ticker-regression-${item}`}
                      onClick={() => selectTicker(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="workspace-map" aria-label="Terminal workspace map" data-testid="workspace-map">
              {workspaceCards.map((workspace, index) => (
                <article key={workspace.key} className={activeTab === workspace.key ? "active" : ""}>
                  <div>
                    <strong>{workspace.label}</strong>
                    <span>{workspace.detail}</span>
                  </div>
                  <button
                    type="button"
                    aria-label={`Open workspace card ${index + 1}`}
                    onClick={() => setActiveTab(workspace.key)}
                  >
                    Open
                  </button>
                </article>
              ))}
            </div>
            <p className="source-note">AI notes can explain scenarios, but financial numbers come only from filings, feeds, user input, or deterministic formulas.</p>
            <div className="ask-brief" aria-label="Deterministic underwriting brief" data-testid="ask-underwriter-brief">
              <div>
                <strong>{ticker}</strong>
                <span>{dataMatchesTicker ? snapshot.name || securityMeta.label : `${securityMeta.label} data loading`}</span>
              </div>
              <dl>
                <div><dt>Price</dt><dd>{dataMatchesTicker ? `${snapshot.current_price} ${snapshot.currency}` : "-"}</dd></div>
                <div><dt>P/E</dt><dd>{dataMatchesTicker ? snapshot.per ?? "-" : "-"}</dd></div>
                <div><dt>EPS method</dt><dd>{dataMatchesTicker ? snapshot.eps_method : "loading"}</dd></div>
                <div><dt>Latest EPS</dt><dd>{dataMatchesTicker ? `${latestReportedRow?.metric ?? "-"} ${latestReportedRow?.fiscal_year ?? ""}` : "-"}</dd></div>
                <div><dt>{forecastYears}Y target</dt><dd>{dataMatchesTicker ? `${terminalForecastRow?.price ?? "-"} ${snapshot.currency}` : "-"}</dd></div>
                <div><dt>Audit facts</dt><dd>{dataMatchesTicker ? askSourceCount : "loading"}</dd></div>
              </dl>
              <div className="ask-answer" data-testid="ask-underwriter-answer">
                <div>
                  <span>Source-traced answer</span>
                  <strong>{askNarrative.verdict}</strong>
                </div>
                <ul>
                  {askNarrative.bullets.map((bullet) => (
                    <li key={bullet}>{bullet}</li>
                  ))}
                </ul>
                <div className="ask-evidence-ledger" aria-label="Ask evidence ledger">
                  {askNarrative.evidence.map((item) => (
                    <div key={item.label} data-testid={`ask-evidence-${auditTestIdPart(item.label.toLowerCase()).replace(/_/g, "-")}`}>
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                      <em>{item.method} / {item.quality}</em>
                      {item.href ? <a href={item.href}>Open fact</a> : <small>pending source</small>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
          <div className="hero-side">
            <aside className="simple-flow-card">
              <h2>Flow</h2>
              {[
                { label: "1. Ask or search", tab: "Historical" },
                { label: "2. Review answer", tab: "Summary" },
                { label: "3. Adjust 1Y-5Y", tab: "Forecasting" },
                { label: "4. Open evidence", tab: "Data Audit" }
              ].map((step) => (
                <button
                  key={step.label}
                  type="button"
                  className={activeTab === step.tab ? "active" : ""}
                  onClick={() => setActiveTab(step.tab)}
                >
                  {step.label}
                </button>
              ))}
            </aside>
            <aside className="forecast-mini-card" data-testid="forecast-mini-card">
              <h2>Forecast 1Y-5Y</h2>
              <p>Consensus, user values, and AI scenario notes stay separate.</p>
              <button type="button" className={forecastMode === "consensus" ? "active" : ""} onClick={() => setForecastMode("consensus")}>
                Consensus
              </button>
              <button type="button" className={forecastMode === "custom" ? "active" : ""} onClick={() => setForecastMode("custom")}>
                User input
              </button>
              <button type="button" className={forecastMode === "ai_review" ? "active" : ""} onClick={() => setForecastMode("ai_review")}>
                AI notes
              </button>
              <dl>
                <div><dt>Mode</dt><dd>{forecastMode}</dd></div>
                <div><dt>Case</dt><dd>{forecastCase}</dd></div>
                <div><dt>EPS CAGR</dt><dd>{growth}%</dd></div>
                <div><dt>Target P/E</dt><dd>{targetMultiple}x</dd></div>
                <div><dt>Terminal year</dt><dd>{forecastYears}Y view</dd></div>
                <div><dt>Manual EPS</dt><dd>{manualEps.slice(0, forecastYears).filter((value) => value.trim()).length}/{forecastYears}</dd></div>
                <div><dt>Scenario lines</dt><dd>{visibility.scenarioLines ? "on" : "off"}</dd></div>
              </dl>
            </aside>
            <aside className="visualization-coverage-card" data-testid="visualization-coverage-card" aria-label="Visualization coverage">
              <h2>Visualization coverage</h2>
              <p>Visualization is a core dashboard layer now, not later polish; production confidence depends on source-backed rows.</p>
              <div>
                {visualizationCoverageRows.map((row, index) => (
                  <button
                    key={row.label}
                    type="button"
                    aria-label={`Open visual coverage row ${index + 1}`}
                    className={row.targetTab === activeTab ? "active" : ""}
                    onClick={() => setActiveTab(row.targetTab)}
                    data-testid={`visual-coverage-${auditTestIdPart(row.label.toLowerCase())}`}
                  >
                    <span>{row.label}</span>
                    <strong>{row.coverage}</strong>
                    <em>{row.status}</em>
                  </button>
                ))}
              </div>
            </aside>
          </div>
        </section>

        <section className="terminal-kpis">
          <Metric label="Price" value={`${snapshot.current_price} ${snapshot.currency}`} />
          <Metric label="Market cap" value={formatMarketCap(snapshot.market_cap, snapshot.currency)} />
          <Metric label="P/E" value={snapshot.per ?? "-"} />
          <Metric label="Dividend yield" value={formatPercent(snapshot.dividend_yield)} />
          <Metric label="EPS CAGR" value={formatPercent(snapshot.eps_cagr)} />
          <Metric label="ROE" value={formatPercent(snapshot.roe)} />
          <Metric label="Debt/Equity" value={formatNumber(snapshot.debt_ratio)} />
          <Metric label="Source mode" value={sourceReadiness.data_mode} />
        </section>

        <HistoricalControlsPanel
          metric={metric}
          forecastMode={forecastMode}
          forecastCase={forecastCase}
          forecastYears={forecastYears}
          rangeMode={rangeMode}
          rangeStartYear={rangeStartYear}
          rangeEndYear={rangeEndYear}
          normalMultipleYears={normalMultipleYears}
          growth={growth}
          targetMultiple={targetMultiple}
          chartSettingsOpen={chartSettingsOpen}
          displayRangeSummary={displayRangeSummary}
          chartLayoutName={chartLayoutName}
          selectedChartLayoutId={selectedChartLayoutId}
          chartLayouts={chartLayouts}
          chartLayoutStatus={chartLayoutStatus}
          disabledReasonForMetric={getMetricDisabledReason}
          onMetricChange={setMetric}
          onForecastModeChange={setForecastMode}
          onForecastCaseChange={setForecastCase}
          onForecastYearsChange={setForecastYears}
          onApplyRangeMode={applyRangeMode}
          onRangeStartYearChange={(value) => {
            setRangeMode("custom");
            setRangeStartYear(value);
          }}
          onRangeEndYearChange={(value) => {
            setRangeMode("custom");
            setRangeEndYear(value);
          }}
          onNormalMultipleYearsChange={setNormalMultipleYears}
          onGrowthChange={setGrowth}
          onTargetMultipleChange={setTargetMultiple}
          onToggleChartSettings={() => setChartSettingsOpen((value) => !value)}
          onChartLayoutNameChange={setChartLayoutName}
          onSaveCurrentChartLayout={saveCurrentChartLayout}
          onApplyChartLayout={applyChartLayout}
        />

        {activeTab === "Forecasting" ? (
          <ForecastLab
            ticker={ticker}
            valuation={valuation}
            auditRows={auditRows}
            auditQueryString={valuationDataQueryString}
            forecastMeta={forecastMeta}
            forecastEvidence={forecastEvidence}
            forecastYears={forecastYears}
            forecastMode={forecastMode}
            onForecastModeChange={setForecastMode}
            manualEps={manualEps}
            onManualEpsChange={(index, value) => setManualEps((state) => state.map((item, itemIndex) => itemIndex === index ? value : item))}
            hiddenScenarioLines={hiddenScenarioLines}
            onToggleScenarioLine={(label) => setHiddenScenarioLines((state) => (
              state.includes(label) ? state.filter((item) => item !== label) : [...state, label]
            ))}
            onFocusAuditFact={focusDataAuditFact}
            onOpenDataAudit={() => setActiveTab("Data Audit")}
            krCacheCoverage={krCacheCoverage}
            krCacheUniverse={krCacheUniverse}
          />
        ) : null}

        {activeTab === "Summary" ? (
          <SummaryPanel
            snapshot={snapshot}
            selected={selected}
            auditRows={auditRows}
            valuation={valuation}
            forecastMeta={forecastMeta}
            pricePoints={pricePoints}
            onSelectAuditYear={setSelectedYear}
            onFocusAuditFact={focusDataAuditFact}
            onOpenDataAudit={() => setActiveTab("Data Audit")}
          />
        ) : activeTab === "Performance" ? (
          <PerformancePanel
            ticker={ticker}
            performance={performance}
            auditRows={auditRows}
            krCacheCoverage={krCacheCoverage}
            krCacheUniverse={krCacheUniverse}
          />
        ) : activeTab === "Research Report" ? (
          <ResearchReportPanel
            report={researchReport}
            researchMetadata={researchMetadata}
            auditRows={auditRows}
            ticker={ticker}
            metric={metric}
            forecastMode={forecastMode}
            forecastCase={forecastCase}
            forecastYears={forecastYears}
            rangeStartYear={rangeStartYear}
            rangeEndYear={rangeEndYear}
            normalMultipleYears={normalMultipleYears}
            growth={growth}
            targetMultiple={targetMultiple}
            manualEps={manualEps}
            visibility={visibility}
            hiddenScenarioLines={hiddenScenarioLines}
          />
        ) : activeTab === "Consensus" ? (
          <ConsensusPanel ticker={ticker} />
        ) : activeTab === "Peers" ? (
          <PeersPanel ticker={ticker} />
        ) : activeTab === "System" ? (
          <ProviderStatusPanel />
        ) : activeTab === "Financials" ? (
          <FinancialsPanel
            ticker={ticker}
            financials={financials}
            auditRows={auditRows}
            krCacheCoverage={krCacheCoverage}
            krCacheUniverse={krCacheUniverse}
          />
        ) : activeTab === "Fun Graphs" ? (
          <FunGraphsPanel funGraphs={funGraphs} auditRows={auditRows} />
        ) : activeTab === "Fiscal Fitness" ? (
          <FiscalFitnessPanel rows={fiscalFitness} auditRows={auditRows} />
        ) : activeTab === "Health Check" ? (
          <HealthCheckPanel healthCheck={healthCheck} auditRows={auditRows} />
        ) : activeTab === "Use of Cash" ? (
          <UseOfCashPanel rows={useOfCash} auditRows={auditRows} />
        ) : activeTab === "Screener" ? (
          <ScreenerPanel
            rows={screener}
            auditRows={auditRows}
            maxPer={screenerMaxPer}
            minRoe={screenerMinRoe}
            minEpsCagr={screenerMinEpsCagr}
            maxDebt={screenerMaxDebt}
            minMarketCap={screenerMinMarketCap}
            minMarketCapUsd={screenerMinMarketCapUsd}
            relativeDiscount={screenerRelativeDiscount}
            requireRoeGtRoic={screenerRequireRoeGtRoic}
            onMaxPerChange={setScreenerMaxPer}
            onMinRoeChange={setScreenerMinRoe}
            onMinEpsCagrChange={setScreenerMinEpsCagr}
            onMaxDebtChange={setScreenerMaxDebt}
            onMinMarketCapChange={setScreenerMinMarketCap}
            onMinMarketCapUsdChange={setScreenerMinMarketCapUsd}
            onRelativeDiscountChange={setScreenerRelativeDiscount}
            onRequireRoeGtRoicChange={setScreenerRequireRoeGtRoic}
          />
        ) : activeTab === "Watchlist" ? (
          <WatchlistPanel
            watchlist={watchlist}
            auditRows={auditRows}
            tickerInput={watchlistTicker}
            noteInput={watchlistNote}
            status={watchlistStatus}
            onTickerInputChange={setWatchlistTicker}
            onNoteInputChange={setWatchlistNote}
            onAddItem={addWatchlistItem}
            onRemoveItem={removeWatchlistItem}
            onSelectTicker={selectTicker}
          />
        ) : activeTab === "Portfolio" ? (
          <PortfolioPanel
            portfolio={portfolio}
            auditRows={auditRows}
            csvText={portfolioCsv}
            importStatus={portfolioImportStatus}
            onCsvChange={setPortfolioCsv}
            onImport={importPortfolioCsv}
          />
        ) : activeTab === "Data Audit" ? (
          <DataAuditPanel
            rows={auditRows}
            auditQueryString={valuationDataQueryString}
            macroSeries={macroSeries}
            macroMeta={macroSeriesMeta}
            industrySeries={industrySeries}
            industryMeta={industrySeriesMeta}
            krCacheCoverage={krCacheCoverage}
            focusedFactId={dataAuditFocusFactId}
            focusedFactFamily={dataAuditFocusFactFamily}
            onFocusedFactIdChange={focusDataAuditFact}
            onFocusedFactFamilyChange={setDataAuditFocusFactFamily}
          />
        ) : activeTab === "Analyst Scorecard" ? (
          <AnalystScorecardPanel
            ticker={ticker}
            scorecard={analystScorecard}
            evidence={forecastEvidence}
            auditRows={auditRows}
          />
        ) : (
          <section className="main-grid">
            <HistoricalMapPanel
              ticker={ticker}
              activeTab={activeTab}
              valuation={displayValuation}
              auditRows={displayAuditRows}
              auditQueryString={valuationDataQueryString}
              selectedYear={selectedYear}
              visibility={visibility}
              forecastMeta={forecastMeta}
              recessionBands={recessionBands}
              pricePoints={displayPricePoints}
              krCacheCoverage={krCacheCoverage}
              latest={latest}
              forecastMode={forecastMode}
              normalMultipleYears={normalMultipleYears}
              displayRangeSummary={displayRangeSummary}
              targetMultiple={targetMultiple}
              transactions={chartTransactions}
              returnSelectionYears={returnSelectionYears}
              returnSelection={chartReturnSelection}
              hiddenScenarioLines={hiddenScenarioLines}
              settingsOpen={chartSettingsOpen}
              onSelectYear={selectChartYear}
              onSetReturnSelectionYears={(years) => setReturnSelectionYears(years)}
              onSelectAuditYear={setSelectedYear}
              onFocusAuditFact={focusDataAuditFact}
              onOpenDataAudit={() => setActiveTab("Data Audit")}
              onSettingsOpenChange={setChartSettingsOpen}
              onToggle={(key) => setVisibility((state) => ({ ...state, [key]: !state[key] }))}
            />
            <AuditPanel
              ticker={ticker}
              valuation={displayValuation}
              auditRows={displayAuditRows}
              auditQueryString={valuationDataQueryString}
              recessionBands={recessionBands}
              visibility={visibility}
              targetMultiple={targetMultiple}
              selected={selected}
              s1Count={s1Count}
              s2Count={s2Count}
              s4Count={s4Count}
              readiness={sourceReadiness}
              krCacheCoverage={krCacheCoverage}
              krCacheUniverse={krCacheUniverse}
              coverage={sourceCoverage}
              priorityUniverse={priorityUniverse}
              onSelectTicker={selectTicker}
              onFocusAuditFact={focusDataAuditFact}
              onOpenDataAudit={() => setActiveTab("Data Audit")}
            />
          </section>
        )}
      </section>
      <MobileEvidenceSummary
        ticker={ticker}
        activeTab={activeTab}
        snapshot={snapshot}
        valuationRow={selectedValuationRow}
        auditRow={mobileEvidenceAuditRow}
        sourceReadinessMode={sourceReadiness.data_mode}
        onOpenDataAudit={() => setActiveTab("Data Audit")}
      />
      <MobileBottomTabs activeTab={activeTab} onSelectTab={selectWorkspaceTab} />
    </main>
  );
}

function ensureWatchlistItem(
  watchlist: WatchlistSummary,
  ticker: string,
  note: string | null
): WatchlistSummary {
  const normalized = ticker.trim().toUpperCase();
  if (!normalized || watchlist.items.some((item) => item.ticker === normalized)) {
    return watchlist;
  }
  return {
    ...watchlist,
    items: [
      ...watchlist.items,
      {
        ticker: normalized,
        name: normalized,
        market: null,
        country: null,
        currency: null,
        current_price: null,
        per: null,
        dividend_yield: null,
        eps_cagr: null,
        quality_status: "user_provided",
        note,
        source_trace: {
          source_type: "user_watchlist",
          source_document_id: `${normalized.toLowerCase()}-watchlist-ui`,
          filing_id: `${normalized.toLowerCase()}-watchlist-ui`,
          period: "current",
          unit: "ticker",
          currency: "mixed",
          quality_status: "user_provided",
          formula: "optimistic UI watchlist item after successful API mutation"
        }
      }
    ]
  };
}

function removeWatchlistItemLocal(watchlist: WatchlistSummary, ticker: string): WatchlistSummary {
  const normalized = ticker.trim().toUpperCase();
  return {
    ...watchlist,
    items: watchlist.items.filter((item) => item.ticker !== normalized)
  };
}

function expandManualEps(value: string | null | undefined): string[] {
  const parts = String(value ?? "").split(",");
  return Array.from({ length: 5 }, (_, index) => parts[index]?.trim() ?? "");
}

function AuditPanel({
  ticker,
  valuation,
  auditRows,
  auditQueryString,
  recessionBands,
  visibility,
  targetMultiple,
  selected,
  s1Count,
  s2Count,
  s4Count,
  readiness,
  krCacheCoverage,
  krCacheUniverse,
  coverage,
  priorityUniverse,
  onSelectTicker,
  onFocusAuditFact,
  onOpenDataAudit
}: {
  ticker: string;
  valuation: ValuationRow[];
  auditRows: AuditRow[];
  auditQueryString: string;
  recessionBands: RecessionBand[];
  visibility: LineVisibility;
  targetMultiple: number;
  selected?: AdjustedRow;
  s1Count: number;
  s2Count: number;
  s4Count: number;
  readiness: SourceReadiness;
  krCacheCoverage: KrValuationCacheCoverage | null;
  krCacheUniverse: KrValuationCacheUniverseCoverage | null;
  coverage: SourceCoverage;
  priorityUniverse: PriorityUniverse;
  onSelectTicker: (ticker: string) => void;
  onFocusAuditFact: (factId: string) => void;
  onOpenDataAudit: () => void;
}) {
  const warnings = [...(selected?.flags ?? []), ...(selected?.warnings ?? [])];
  const missingRequired = readiness.checks.filter((check) => check.required && !check.ok);
  const currentCoverage = coverage.tickers.find((row) => row.ticker === ticker);
  const minForecastYears = coverage.requirements.min_forecast_years;
  const currentBaseForecastYears = currentCoverage?.counts.consensus_valuation_years ?? 0;
  const currentBaseForecastSnapshots = currentCoverage?.counts.consensus_valuation_snapshots ?? 0;
  const currentMultiple = currentValuationMultiple(valuation);
  const latestDividendRatios = latestDividendRatioMetrics(valuation);
  const graphKeyRow = latestReportedValuationRow(valuation);
  const graphKeyItems = graphKeyAuditItems(
    graphKeyRow,
    auditRows,
    visibility,
    targetMultiple,
    currentMultiple,
    latestDividendRatios,
    recessionBands.length
  );
  return (
    <EvidenceRail
      selected={selected}
      warnings={warnings}
      s1Count={s1Count}
      s2Count={s2Count}
      s4Count={s4Count}
      readiness={readiness}
      krCacheCoverage={krCacheCoverage}
      krCacheUniverse={krCacheUniverse}
      missingRequiredNames={missingRequired.map((check) => check.name)}
      coverage={coverage}
      priorityUniverse={priorityUniverse}
      currentCoverage={currentCoverage}
      minForecastYears={minForecastYears}
      currentBaseForecastYears={currentBaseForecastYears}
      currentBaseForecastSnapshots={currentBaseForecastSnapshots}
      graphKeyItems={graphKeyItems}
      factQueryString={auditQueryString}
      buildFactHref={auditFactHref}
      onSelectTicker={onSelectTicker}
      onInspectAuditFact={(factId) => {
        onFocusAuditFact(factId);
        onOpenDataAudit();
      }}
      onInspectGraphKeyFact={(factId) => {
        onFocusAuditFact(factId);
        onOpenDataAudit();
      }}
    />
  );
}

function graphKeyAuditItems(
  row: ValuationRow | undefined,
  auditRows: AuditRow[],
  visibility: LineVisibility,
  targetMultiple: number,
  currentMultiple: number | null,
  dividendRatios: { payoutRatioPct: number | null; dividendYieldPct: number | null },
  recessionBandCount: number
): GraphKeyLedgerItem[] {
  const valueFor = (raw: string | number | null | undefined) => formatNumber(raw);
  const multipleFor = (raw: string | number | null | undefined) => {
    const value = Number(raw);
    return Number.isFinite(value) ? `${value.toFixed(1)}x` : "-";
  };
  const auditFor = (factName: string) => valuationAuditRowFor(row, auditRows, factName);
  const chartKeyAuditFor = (factName: string) => chartKeyAuditRowFor(row, auditRows, factName);
  const item = (
    key: string,
    label: string,
    swatchClass: string,
    visible: boolean,
    value: string,
    formula: string,
    auditRow?: AuditRow,
    sourceLabel?: string
  ): GraphKeyLedgerItem => ({
    key,
    label,
    swatchClass,
    visible,
    value,
    formula: auditRow?.source_trace.formula ?? auditRow?.formula ?? formula,
    sourceLabel: sourceLabel ?? graphKeySourceLabel(auditRow),
    auditRow
  });

  return [
    item("price", "Price", "black", visibility.price, valueFor(row?.price), "valuation.price from price series", auditFor("price")),
    item("metric-area", "EPS metric", "green", visibility.metricArea, valueFor(row?.metric), "selected valuation metric", auditFor("metric")),
    item(
      "fair-value",
      "Fair value",
      "orange",
      visibility.fairValue,
      `${valueFor(row?.fair_value_price)} @ ${multipleFor(row?.fair_multiple)}`,
      "fair_value_price = metric * fair_multiple",
      auditFor("fair_value_price")
    ),
    item(
      "normal-multiple",
      "Normal multiple",
      "blue",
      visibility.normalMultiple,
      multipleFor(row?.normal_multiple),
      "normal_multiple = trimmed average price / metric over selected window",
      auditFor("normal_multiple")
    ),
    item(
      "current-valuation",
      "Current valuation",
      "current",
      visibility.currentValuation,
      currentMultiple ? multipleFor(currentMultiple) : "-",
      "current valuation line = selected metric * current price-to-metric multiple",
      chartKeyAuditFor("current_multiple")
    ),
    item(
      "custom-valuation",
      "Custom valuation",
      "custom",
      visibility.customValuation,
      visibility.customValuation ? multipleFor(targetMultiple) : "-",
      "custom valuation line = selected metric * user target multiple",
      chartKeyAuditFor("custom_multiple"),
      "user setting"
    ),
    item("dividend-floor", "Dividend floor", "yellow", visibility.dividendFloor, valueFor(row?.dividend), "valuation.dividend", auditFor("dividend")),
    item(
      "payout-ratio",
      "Payout ratio",
      "payout",
      visibility.payoutRatio,
      formatMaybePercent(dividendRatios.payoutRatioPct),
      "dividend / metric",
      chartKeyAuditFor("payout_ratio_pct")
    ),
    item(
      "dividend-yield",
      "Dividend yield",
      "yield",
      visibility.dividendYield,
      formatMaybePercent(dividendRatios.dividendYieldPct),
      "dividend / price",
      chartKeyAuditFor("dividend_yield_pct")
    ),
    item(
      "recession-bands",
      "Recession bands",
      "recession",
      visibility.recessionBands,
      `${recessionBandCount} bands`,
      "macro recession intervals shaded as chart background",
      undefined,
      "macro series"
    )
  ];
}

function latestReportedValuationRow(rows: ValuationRow[]) {
  return [...rows].reverse().find((row) => !row.forecast_flag) ?? rows.at(-1);
}

function selectedForecastConsensusEvidence({
  forecastEvidence,
  forecastMeta,
  forecastCase,
  auditRows
}: {
  forecastEvidence: ForecastEvidence;
  forecastMeta: ForecastMeta;
  forecastCase: string;
  auditRows: AuditRow[];
}): AskConsensusEvidence | undefined {
  const supportedCases = new Set(["low", "median", "high"]);
  const preferredCase = [forecastCase, forecastMeta.case, forecastMeta.consensus?.case, "median"].find(
    (item): item is string => Boolean(item && supportedCases.has(item))
  );
  const selectedCase =
    forecastEvidence.cases.find((row) => row.case === preferredCase) ??
    forecastEvidence.cases.find((row) => row.case === "median") ??
    forecastEvidence.cases[0];
  if (!selectedCase) {
    return undefined;
  }

  const factName = `forecast_snapshot.${selectedCase.case}.estimate_eps`;
  const auditRow = auditRows.find(
    (row) => row.fiscal_year === forecastEvidence.forecast_year && row.fact_name === factName
  );
  const trace = selectedCase.source_trace ?? {};
  return {
    caseLabel: selectedCase.case,
    fiscalYear: forecastEvidence.forecast_year,
    estimateEps: selectedCase.estimate_eps,
    growthRatePct: selectedCase.growth_rate_pct,
    analystCount: forecastMeta.consensus?.analyst_count ?? forecastMeta.analyst_count ?? null,
    method: auditRow?.method ?? String(trace.source_type ?? forecastMeta.source ?? "forecast_consensus_snapshot"),
    quality: auditRow?.quality_status ?? String(trace.quality_status ?? forecastMeta.consensus?.quality_status ?? forecastEvidence.meta?.quality_status ?? "pending_audit_row"),
    href: auditRow ? auditFactHref(auditRow.fact_id) : undefined
  };
}

function buildAskNarrative({
  ticker,
  snapshot,
  latestReportedRow,
  terminalForecastRow,
  forecastYears,
  askSourceCount,
  dataMatchesTicker,
  metricAuditRow,
  forecastReturnAuditRow,
  askConsensusEvidence
}: {
  ticker: string;
  snapshot: Snapshot;
  latestReportedRow: ValuationRow | undefined;
  terminalForecastRow: ValuationRow | undefined;
  forecastYears: number;
  askSourceCount: number;
  dataMatchesTicker: boolean;
  metricAuditRow: AuditRow | undefined;
  forecastReturnAuditRow: AuditRow | undefined;
  askConsensusEvidence: AskConsensusEvidence | undefined;
}): AskNarrative {
  if (!dataMatchesTicker) {
    return {
      verdict: `${ticker} data is loading`,
      bullets: [
        "The selected ticker and loaded financial dataset do not match yet.",
        "No valuation conclusion is shown until matching source-traced rows are loaded."
      ],
      evidence: [
        {
          label: "Ticker guard",
          value: `${ticker} != ${snapshot.ticker}`,
          method: "ui_data_integrity_guard",
          quality: "stale_data_blocked"
        }
      ]
    };
  }

  const latestMetric = toNumberOrNull(latestReportedRow?.metric);
  const latestPrice = toNumberOrNull(latestReportedRow?.price);
  const latestNormalMultiple = toNumberOrNull(latestReportedRow?.normal_multiple);
  const currentMultiple = latestMetric !== null && latestPrice !== null && latestMetric !== 0 ? latestPrice / latestMetric : null;
  const terminalReturn = toNumberOrNull(terminalForecastRow?.total_return_cagr_pct);
  const terminalPrice = terminalForecastRow?.price ?? null;
  const multipleGapPct = currentMultiple && latestNormalMultiple
    ? ((currentMultiple - latestNormalMultiple) / latestNormalMultiple) * 100
    : null;
  const valuationPhrase = multipleGapPct === null
    ? "normal multiple comparison unavailable"
    : multipleGapPct > 0
      ? `${formatMaybePercent(multipleGapPct)} above normal multiple`
      : `${formatMaybePercent(Math.abs(multipleGapPct))} below normal multiple`;
  const returnPhrase = terminalReturn === null
    ? "forecast return unavailable"
    : `${formatMaybePercent(terminalReturn)} dividend-incl CAGR`;
  const verdict = terminalReturn !== null
    ? `${ticker}: ${returnPhrase}`
    : `${ticker}: ${valuationPhrase}`;
  const consensusEvidenceIsManual = Boolean(
    askConsensusEvidence?.quality?.toLowerCase().includes("manual_forecast_assumption") ||
    askConsensusEvidence?.method?.toLowerCase().includes("manual")
  );
  const forecastEvidenceLabel = consensusEvidenceIsManual ? "Manual Forecast EPS" : "Consensus EPS";
  const consensusBullet = askConsensusEvidence
    ? consensusEvidenceIsManual
      ? `Reference manual forecast assumption is ${askConsensusEvidence.caseLabel} FY${askConsensusEvidence.fiscalYear} EPS ${formatNumber(askConsensusEvidence.estimateEps)} with ${formatMaybeGrowth(askConsensusEvidence.growthRatePct)} growth.`
      : `Reference consensus snapshot is ${askConsensusEvidence.caseLabel} FY${askConsensusEvidence.fiscalYear} EPS ${formatNumber(askConsensusEvidence.estimateEps)} with ${formatMaybeGrowth(askConsensusEvidence.growthRatePct)} growth.`
    : "Consensus snapshot evidence is pending; use manual EPS or deterministic formulas until a source-traced estimate is loaded.";

  return {
    verdict,
    bullets: [
      `Latest reported EPS/metric is ${latestReportedRow?.metric ?? "-"} for FY${latestReportedRow?.fiscal_year ?? "-"}.`,
      `Current valuation is ${currentMultiple ? `${currentMultiple.toFixed(1)}x` : "-"} versus normal ${latestReportedRow?.normal_multiple ?? "-"}x, ${valuationPhrase}.`,
      `${forecastYears}Y terminal forecast price is ${terminalPrice ?? "-"} ${snapshot.currency}; value comes from the current forecast dataset, not an LLM.`,
      consensusBullet
    ],
    evidence: [
      {
        label: "EPS metric",
        value: latestReportedRow?.metric ?? "-",
        method: metricAuditRow?.method ?? snapshot.eps_method,
        quality: metricAuditRow?.quality_status ?? snapshot.source_note,
        href: metricAuditRow ? `/api/data-audit/${metricAuditRow.fact_id}` : undefined
      },
      {
        label: "Forecast CAGR",
        value: terminalReturn !== null ? formatMaybePercent(terminalReturn) : "-",
        method: forecastReturnAuditRow?.method ?? terminalForecastRow?.forecast_source ?? "deterministic_forecast",
        quality: forecastReturnAuditRow?.quality_status ?? "pending_audit_row",
        href: forecastReturnAuditRow ? `/api/data-audit/${forecastReturnAuditRow.fact_id}` : undefined
      },
      ...(askConsensusEvidence
        ? [
            {
              label: forecastEvidenceLabel,
              value: `${formatNumber(askConsensusEvidence.estimateEps)} FY${askConsensusEvidence.fiscalYear} ${askConsensusEvidence.caseLabel}`,
              method: askConsensusEvidence.method,
              quality: askConsensusEvidence.quality,
              href: askConsensusEvidence.href
            }
          ]
        : []),
      {
        label: "Audit coverage",
        value: `${askSourceCount} facts`,
        method: "data_audit_fact_count",
        quality: askSourceCount > 0 ? "available" : "pending"
      }
    ]
  };
}

function buildVisualizationCoverageRows({
  valuation,
  pricePoints,
  auditRows,
  forecastYears,
  visibility,
  chartTransactions,
  performanceRows,
  sourceMode
}: {
  valuation: ValuationRow[];
  pricePoints: PricePoint[];
  auditRows: AuditRow[];
  forecastYears: number;
  visibility: LineVisibility;
  chartTransactions: Array<{ date: string; side: string; quantity: string; price: string }>;
  performanceRows: number;
  sourceMode: string;
}) {
  const historicalRows = valuation.filter((row) => !row.forecast_flag).length;
  const forecastRows = valuation.filter((row) => row.forecast_flag).length;
  const visibleLayers = Object.values(visibility).filter(Boolean).length;
  return [
    {
      label: "Historical map",
      targetTab: "Historical",
      coverage: `${historicalRows} FY / ${pricePoints.length} price pts`,
      status: sourceMode
    },
    {
      label: "Forecast fan",
      targetTab: "Forecasting",
      coverage: `${Math.min(forecastYears, forecastRows)}/${forecastYears} years`,
      status: visibility.forecast && visibility.scenarioLines ? "scenario lines on" : "visual layer toggled"
    },
    {
      label: "Line controls",
      targetTab: "Historical",
      coverage: `${visibleLayers}/${Object.keys(visibility).length} visible`,
      status: "user controlled"
    },
    {
      label: "Data Audit",
      targetTab: "Data Audit",
      coverage: `${auditRows.length} facts`,
      status: auditRows.length ? "click-through ready" : "source rows pending"
    },
    {
      label: "Performance",
      targetTab: "Performance",
      coverage: `${performanceRows} return rows`,
      status: performanceRows ? "return chart ready" : "awaiting source rows"
    },
    {
      label: "Trade overlays",
      targetTab: "Portfolio",
      coverage: `${chartTransactions.length} transactions`,
      status: chartTransactions.length ? "overlay enabled" : "CSV import ready"
    }
  ] as const;
}

function valuationAuditRowFor(row: ValuationRow | undefined, auditRows: AuditRow[], factName: string) {
  if (!row) {
    return undefined;
  }
  const scope = row.forecast_flag ? "forecast" : "valuation";
  return auditRows.find(
    (auditRow) =>
      auditRow.fiscal_year === row.fiscal_year &&
      auditRow.fact_name === `${scope}.${factName}`
  );
}

function dataAuditFamilyForFactRow(row: AuditRow) {
  const factName = row.fact_name ?? "";
  const policy = row.policy ?? "";
  if (factName.startsWith("forecast.") || factName.startsWith("forecast_snapshot.") || policy.startsWith("forecast")) {
    return "forecast";
  }
  if (factName.startsWith("price_point.") || policy === "price_points") {
    return "price_points";
  }
  if (
    factName.startsWith("valuation.") ||
    factName.startsWith("chart_key.") ||
    policy === "valuation_map" ||
    policy === "chart_key"
  ) {
    return "valuation_derived";
  }
  if (factName.startsWith("kr_warehouse.") && factName.toLowerCase().includes("price")) {
    return "warehouse_price";
  }
  if (factName.startsWith("kr_warehouse.")) {
    return "warehouse_metric";
  }
  if (factName.startsWith("financial_fact.") || policy === "adjusted_operating") {
    return "xbrl_source";
  }
  return "other";
}

function chartKeyAuditRowFor(row: ValuationRow | undefined, auditRows: AuditRow[], factName: string) {
  if (!row) {
    return undefined;
  }
  return auditRows.find(
    (auditRow) =>
      auditRow.fiscal_year === row.fiscal_year &&
      auditRow.fact_name === `chart_key.${factName}`
  );
}

function graphKeySourceLabel(row?: AuditRow) {
  if (!row) {
    return "deterministic";
  }
  const source = row.source_trace.source_type ?? row.method;
  const quality = row.source_trace.quality_status ?? row.quality_status;
  return `${source} / ${quality}`;
}

function CompanyHeaderPanel({
  ticker,
  snapshot,
  securityMeta,
  activeTab,
  tabs: workspaceTabs,
  dataMatchesTicker,
  latestReportedRow,
  sourceReadinessMode,
  onSelectTab
}: {
  ticker: string;
  snapshot: Snapshot;
  securityMeta: { label: string; market: string; currency: string };
  activeTab: string;
  tabs: readonly string[];
  dataMatchesTicker: boolean;
  latestReportedRow: ValuationRow | undefined;
  sourceReadinessMode: string;
  onSelectTab: (tab: string) => void;
}) {
  const displayedName = dataMatchesTicker
    ? snapshot.name || securityMeta.label
    : `${securityMeta.label} data loading`;
  const displayedCurrency = dataMatchesTicker ? snapshot.currency : securityMeta.currency;
  const displayedPrice = dataMatchesTicker ? formatNumber(snapshot.current_price) : "-";
  const rawPriceChange =
    (snapshot as Snapshot & { price_change_pct?: string | number | null; change_pct?: string | number | null })
      .price_change_pct ??
    (snapshot as Snapshot & { change_pct?: string | number | null }).change_pct ??
    null;
  const priceChangeValue = toNumberOrNull(rawPriceChange);
  const priceChangeText =
    dataMatchesTicker && priceChangeValue !== null
      ? `${priceChangeValue >= 0 ? "+" : ""}${formatPercent(priceChangeValue)}`
      : dataMatchesTicker
        ? "source-backed"
        : "pending";
  const priceChangeClass = priceChangeValue !== null && priceChangeValue < 0 ? "negative" : "positive";
  const rawScore =
    (snapshot as Snapshot & { nexus_score?: string | number | null; fg_score?: string | number | null }).nexus_score ??
    (snapshot as Snapshot & { fg_score?: string | number | null }).fg_score ??
    null;
  const confidenceScore = toNumberOrNull(snapshot.confidence);
  const normalizedConfidenceScore =
    confidenceScore !== null && confidenceScore <= 1 ? confidenceScore * 100 : confidenceScore;
  const scoreText =
    dataMatchesTicker && rawScore !== null && rawScore !== undefined
      ? `LUXON score ${formatNumber(rawScore)} / 100`
      : dataMatchesTicker && normalizedConfidenceScore !== null
        ? `LUXON score ${Math.round(normalizedConfidenceScore)} / 100`
        : `Data mode ${sourceReadinessMode}`;
  const latestMetricText = latestReportedRow
    ? `FY${latestReportedRow.fiscal_year} ${formatNumber(latestReportedRow.metric)} EPS`
    : "latest EPS pending";
  const primaryTabs = workspaceTabs.filter((tab) =>
    [
      "Summary",
      "Historical",
      "Performance",
      "Forecasting",
      "Analyst Scorecard",
      "Fun Graphs",
      "Fiscal Fitness",
      "Financials"
    ].includes(tab)
  );
  const visibleTabs = primaryTabs.includes(activeTab)
    ? primaryTabs
    : [...primaryTabs.slice(0, -1), activeTab];
  const extendedTabs = workspaceTabs.filter((tab) => !visibleTabs.includes(tab));
  const isSourceRequired = !isSourceSatisfiedMode(sourceReadinessMode);
  const sourceGateTitle = isSourceRequired ? `${securityMeta.market} E2E source gate` : "Source-backed render path";
  const sourceGateBody = isSourceRequired
    ? "Financial values stay blank until OpenDART, pykrx, and marcap rows pass source_trace validation."
    : "This security is using source-backed rows and deterministic valuation formulas.";

  return (
    <section className="company-header-panel" data-testid="company-header-panel" aria-label="Company header panel">
      <div className="figma-source-badge">Source-traced data · deterministic valuation</div>
      <div className="company-header-main">
        <div className="company-identity">
          <h1>
            {displayedName} <span>({ticker}:{securityMeta.market})</span>
          </h1>
          <div className="company-price-row">
            <strong>{displayedCurrency} {displayedPrice}</strong>
            <span className={`price-change-chip ${priceChangeClass}`}>{priceChangeText}</span>
            <em>At close: source-backed snapshot</em>
            <span className="nexus-score-chip">{scoreText}</span>
          </div>
        </div>
        <div className="company-header-actions">
          <button type="button" className="portfolio-action" onClick={() => onSelectTab("Portfolio")}>
            Add to Portfolio
          </button>
          <button
            type="button"
            className="settings-action"
            onClick={() => onSelectTab("Historical")}
            aria-label="Open chart settings workspace"
          >
            <Settings2 size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className={`company-source-gate ${isSourceRequired ? "pending" : "ready"}`} data-testid="company-source-gate">
        <div>
          <strong>{sourceGateTitle}</strong>
          <span>{sourceGateBody}</span>
        </div>
        <dl>
          <div>
            <dt>Data mode</dt>
            <dd>{sourceReadinessMode}</dd>
          </div>
          <div>
            <dt>Next source</dt>
            <dd>{securityMeta.market === "KR" ? "OpenDART + pykrx + marcap" : "source-backed ingestion"}</dd>
          </div>
          <div>
            <dt>UI rule</dt>
            <dd>no source_trace, no number</dd>
          </div>
        </dl>
      </div>
      <nav className="company-tab-strip tabs" aria-label="Company workspace tabs">
        {visibleTabs.map((tab) => (
          <button
            key={tab}
            type="button"
            className={tab === activeTab ? "active" : ""}
            aria-pressed={tab === activeTab}
            onClick={() => onSelectTab(tab)}
          >
            {tab}
          </button>
        ))}
      </nav>
      <nav className="company-extra-tab-strip" aria-label="Extended terminal workspaces">
        {extendedTabs.map((tab) => (
          <button
            key={tab}
            type="button"
            className={tab === activeTab ? "active" : ""}
            aria-pressed={tab === activeTab}
            onClick={() => onSelectTab(tab)}
          >
            {tab}
          </button>
        ))}
      </nav>
      <div className="company-header-trace">
        <span>{latestMetricText}</span>
        <span>{snapshot.eps_method}</span>
        <span>{formatConfidence(snapshot.confidence)} confidence</span>
        <span>{sourceReadinessMode}</span>
      </div>
    </section>
  );
}

function MobileBottomTabs({
  activeTab,
  onSelectTab
}: {
  activeTab: string;
  onSelectTab: (tab: string) => void;
}) {
  return (
    <nav className="mobile-bottom-nav" data-testid="mobile-bottom-tabs" aria-label="Mobile workspace tabs">
      {mobileWorkflowTabs.map(({ tab, label }) => (
        <button
          key={tab}
          type="button"
          className={activeTab === tab ? "active" : ""}
          data-testid={`mobile-tab-${tab.replace(/\s+/g, "-")}`}
          aria-current={activeTab === tab ? "page" : undefined}
          onClick={() => onSelectTab(tab)}
        >
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}

function MobileEvidenceSummary({
  ticker,
  activeTab,
  snapshot,
  valuationRow,
  auditRow,
  sourceReadinessMode,
  onOpenDataAudit
}: {
  ticker: string;
  activeTab: string;
  snapshot: Snapshot;
  valuationRow: ValuationRow | undefined;
  auditRow: AuditRow | undefined;
  sourceReadinessMode: string;
  onOpenDataAudit: () => void;
}) {
  const trace = (auditRow?.source_trace ?? snapshot.source_trace ?? valuationRow?.source_trace ?? {}) as Record<string, unknown>;
  const method = auditRow?.method ?? snapshot.eps_method ?? stringTraceValue(trace, "method") ?? stringTraceValue(trace, "source_type") ?? "-";
  const confidence = auditRow?.confidence ?? snapshot.confidence ?? stringTraceValue(trace, "confidence") ?? "-";
  const source =
    stringTraceValue(trace, "source_document_id") ??
    stringTraceValue(trace, "filing_id") ??
    stringTraceValue(trace, "accession_number") ??
    stringTraceValue(trace, "source_type") ??
    sourceReadinessMode;
  const quality = auditRow?.quality_status ?? stringTraceValue(trace, "quality_status") ?? snapshot.source_note;
  const period = auditRow?.fiscal_year ?? valuationRow?.fiscal_year;
  return (
    <aside className="mobile-evidence-summary" data-testid="mobile-evidence-summary" aria-label="Mobile source evidence summary">
      <div>
        <strong>{ticker} {period ? `FY${period}${valuationRow?.forecast_flag ? "E" : ""}` : activeTab}</strong>
        <span data-testid="mobile-evidence-method">Method {method} - Confidence {formatConfidence(confidence)} - Source {source}</span>
      </div>
      <dl>
        <div>
          <dt>Confidence</dt>
          <dd>{formatConfidence(confidence)}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd title={source}>{source}</dd>
        </div>
        <div>
          <dt>Quality</dt>
          <dd title={quality}>{quality}</dd>
        </div>
      </dl>
      <button type="button" data-testid="mobile-evidence-open-audit" onClick={onOpenDataAudit}>
        Audit
      </button>
    </aside>
  );
}

function SummaryPanel({
  snapshot,
  selected,
  auditRows,
  valuation,
  forecastMeta,
  pricePoints,
  onSelectAuditYear,
  onFocusAuditFact,
  onOpenDataAudit
}: {
  snapshot: Snapshot;
  selected?: AdjustedRow;
  auditRows: AuditRow[];
  valuation: ValuationRow[];
  forecastMeta: ForecastMeta;
  pricePoints: PricePoint[];
  onSelectAuditYear: (year: number) => void;
  onFocusAuditFact: (factId: string) => void;
  onOpenDataAudit: () => void;
}) {
  const [selectedSnapshotFact, setSelectedSnapshotFact] = useState("current_price");
  const selectedSnapshotAuditRow = auditRows.find(
    (row) =>
      row.fact_id.startsWith(`${snapshot.ticker}-`) &&
      row.fact_name === `snapshot.${selectedSnapshotFact}`
  );
  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Company Terminal</h1>
          <p>Search, market, currency, valuation, profitability, leverage, and source status in one compact view.</p>
        </div>
      </div>
      <div className="summary-grid">
        <Metric label="Market" value={`${snapshot.market} / ${snapshot.country}`} />
        <Metric label="Currency" value={snapshot.currency} />
        <SummaryAuditMetric
          label="Current price"
          value={snapshot.current_price}
          factName="current_price"
          onSelect={setSelectedSnapshotFact}
        />
        <SummaryAuditMetric
          label="Market cap"
          value={formatMarketCap(snapshot.market_cap, snapshot.currency)}
          factName="market_cap"
          onSelect={setSelectedSnapshotFact}
        />
        <SummaryAuditMetric
          label="Listed shares"
          value={formatNumber(snapshot.listed_shares)}
          factName="listed_shares"
          onSelect={setSelectedSnapshotFact}
        />
        <SummaryAuditMetric label="PER" value={snapshot.per ?? "-"} factName="per" onSelect={setSelectedSnapshotFact} />
        <SummaryAuditMetric
          label="Dividend yield"
          value={formatPercent(snapshot.dividend_yield)}
          factName="dividend_yield"
          onSelect={setSelectedSnapshotFact}
        />
        <SummaryAuditMetric
          label="EPS CAGR"
          value={formatPercent(snapshot.eps_cagr)}
          factName="eps_cagr"
          onSelect={setSelectedSnapshotFact}
        />
        <SummaryAuditMetric label="ROE" value={formatPercent(snapshot.roe)} factName="roe" onSelect={setSelectedSnapshotFact} />
        <SummaryAuditMetric label="ROIC" value={formatPercent(snapshot.roic)} factName="roic" onSelect={setSelectedSnapshotFact} />
        <SummaryAuditMetric
          label="Debt ratio"
          value={formatNumber(snapshot.debt_ratio)}
          factName="debt_ratio"
          onSelect={setSelectedSnapshotFact}
        />
        <Metric label="EPS method" value={snapshot.eps_method} />
        <Metric label="Confidence" value={formatConfidence(snapshot.confidence)} />
        <Metric label="Data mode" value={snapshot.source_note} />
      </div>
      <SummaryValuationPreview
        snapshot={snapshot}
        valuation={valuation}
        forecastMeta={forecastMeta}
        pricePoints={pricePoints}
        auditRows={auditRows}
        onSelectAuditYear={onSelectAuditYear}
        onFocusAuditFact={onFocusAuditFact}
        onOpenDataAudit={onOpenDataAudit}
      />
      <div className="source-box">
        <strong>Source Trace</strong>
        <code>{JSON.stringify(snapshot.source_trace ?? selected?.source_trace ?? {}, null, 2)}</code>
      </div>
      <SelectedAuditTrace
        row={selectedSnapshotAuditRow}
        fallbackTrace={snapshot.source_trace ?? selected?.source_trace}
        fallbackLabel={`snapshot.${selectedSnapshotFact}`}
      />
    </section>
  );
}

type SummaryPreviewMarker = {
  row: ValuationRow;
  auditRow: AuditRow | undefined;
  x: string;
  y: string;
  scope: "valuation" | "forecast";
};

type SummaryPreviewFact = "metric" | "price" | "fair_value_price" | "normal_multiple" | "dividend";

const summaryPreviewFactOptions: Array<{ key: SummaryPreviewFact; label: string }> = [
  { key: "metric", label: "Metric" },
  { key: "price", label: "Price" },
  { key: "fair_value_price", label: "Fair value" },
  { key: "normal_multiple", label: "Normal multiple" },
  { key: "dividend", label: "Dividend" }
];

function SummaryValuationPreview({
  snapshot,
  valuation,
  forecastMeta,
  pricePoints,
  auditRows,
  onSelectAuditYear,
  onFocusAuditFact,
  onOpenDataAudit
}: {
  snapshot: Snapshot;
  valuation: ValuationRow[];
  forecastMeta: ForecastMeta;
  pricePoints: PricePoint[];
  auditRows: AuditRow[];
  onSelectAuditYear: (year: number) => void;
  onFocusAuditFact: (factId: string) => void;
  onOpenDataAudit: () => void;
}) {
  const [selectedPreviewFact, setSelectedPreviewFact] = useState<SummaryPreviewFact>("metric");
  const latestReported = useMemo(() => latestReportedValuationRow(valuation), [valuation]);
  const currentMultiple = useMemo(() => currentValuationMultiple(valuation), [valuation]);
  const chartMax = useMemo(
    () => maxChartValue(valuation, forecastMeta, currentMultiple, null, pricePoints),
    [currentMultiple, forecastMeta, pricePoints, valuation]
  );
  const linePoints = useMemo(
    () => buildLinePoints(valuation, chartMax, currentMultiple, null, pricePoints),
    [chartMax, currentMultiple, pricePoints, valuation]
  );
  const metricAreaPath = useMemo(() => buildMetricAreaPath(valuation, chartMax), [chartMax, valuation]);
  const firstForecastIndex = valuation.findIndex((row) => row.forecast_flag);
  const forecastX = firstForecastIndex >= 0 && valuation.length > 0
    ? ((firstForecastIndex / valuation.length) * 100).toFixed(2)
    : null;
  const latestAuditRow = latestReported
    ? auditRows.find((row) => row.fiscal_year === latestReported.fiscal_year && row.fact_name === `valuation.${selectedPreviewFact}`)
    : undefined;
  const latestMetric = toNumberOrNull(latestReported?.metric);
  const latestPrice = toNumberOrNull(latestReported?.price);
  const latestFair = toNumberOrNull(latestReported?.fair_value_price);
  const selectedPreviewFactLabel =
    summaryPreviewFactOptions.find((option) => option.key === selectedPreviewFact)?.label ?? "Metric";
  const marginOfSafety = latestPrice !== null && latestFair !== null && latestFair > 0
    ? ((latestFair - latestPrice) / latestFair) * 100
    : null;
  const latestMethod = latestAuditRow?.method ?? snapshot.eps_method;
  const latestQuality = latestAuditRow?.quality_status ?? snapshot.source_note;
  const previewMarkers = useMemo<SummaryPreviewMarker[]>(
    () =>
      valuation
        .map((row, index) => {
          const markerValue = summaryPreviewFactValue(row, selectedPreviewFact);
          if (markerValue === null || !Number.isFinite(markerValue)) {
            return null;
          }
          const scope: SummaryPreviewMarker["scope"] = row.forecast_flag ? "forecast" : "valuation";
          const auditRow = auditRows.find(
            (candidate) =>
              candidate.fiscal_year === row.fiscal_year &&
              candidate.fact_name === `${scope}.${selectedPreviewFact}`
          );
          const x = valuation.length <= 1 ? 50 : ((index + 0.5) / valuation.length) * 100;
          const y = 100 - Math.min(95, Math.max(5, (markerValue / chartMax) * 82));
          return {
            row,
            auditRow,
            x: x.toFixed(2),
            y: y.toFixed(2),
            scope
          };
        })
        .filter((item): item is SummaryPreviewMarker => item !== null),
    [auditRows, chartMax, selectedPreviewFact, valuation]
  );
  const selectPreviewYear = (marker: SummaryPreviewMarker) => {
    onSelectAuditYear(marker.row.fiscal_year);
    if (marker.auditRow?.fact_id) {
      onFocusAuditFact(marker.auditRow.fact_id);
    }
    onOpenDataAudit();
  };

  return (
    <section className="summary-valuation-preview" data-testid="summary-valuation-preview">
      <div className="summary-preview-copy">
        <span>Valuation map preview</span>
        <strong>{snapshot.ticker} price vs source-traced metric</strong>
        <p>
          Reported history, valuation references, and forward scenario are rendered from the same audited valuation dataset.
        </p>
        <div className="summary-preview-facts">
          <Metric label="Latest FY metric" value={latestMetric !== null ? latestMetric.toFixed(2) : "-"} />
          <Metric label="Normal P/E" value={latestReported?.normal_multiple ? `${latestReported.normal_multiple}x` : "-"} />
          <Metric label="MoS" value={formatMaybePercent(marginOfSafety)} />
          <Metric label="Audit target" value={selectedPreviewFactLabel} />
        </div>
        <div className="summary-preview-audit-controls" aria-label="Summary preview audit target">
          {summaryPreviewFactOptions.map((option) => (
            <button
              key={option.key}
              type="button"
              className={selectedPreviewFact === option.key ? "active" : ""}
              aria-pressed={selectedPreviewFact === option.key}
              data-testid={`summary-preview-audit-fact-${auditTestIdPart(option.key)}`}
              onClick={() => setSelectedPreviewFact(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      <div className="summary-preview-chart-wrap">
        <svg
          className="summary-preview-chart"
          viewBox="0 0 100 100"
          role="img"
          aria-label="Summary valuation map preview"
        >
          <defs>
            <linearGradient id="summaryMetricFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#2E9E6B" stopOpacity="0.62" />
              <stop offset="100%" stopColor="#2E9E6B" stopOpacity="0.36" />
            </linearGradient>
          </defs>
          <rect x="0" y="0" width="100" height="100" className="summary-preview-bg" />
          {[20, 40, 60, 80].map((line) => (
            <line key={line} x1="0" x2="100" y1={line} y2={line} className="summary-preview-grid" />
          ))}
          {forecastX ? (
            <rect
              data-testid="summary-preview-forecast-region"
              x={forecastX}
              y="0"
              width={(100 - Number(forecastX)).toFixed(2)}
              height="100"
              className="summary-preview-forecast"
            />
          ) : null}
          {metricAreaPath ? (
            <path data-testid="summary-preview-metric-area" d={metricAreaPath} className="summary-preview-metric-area" />
          ) : null}
          {linePoints.normal ? (
            <polyline data-testid="summary-preview-normal-line" points={linePoints.normal} className="summary-preview-normal-line" />
          ) : null}
          {linePoints.fair ? (
            <polyline data-testid="summary-preview-fair-line" points={linePoints.fair} className="summary-preview-fair-line" />
          ) : null}
          {linePoints.dividend ? (
            <polyline data-testid="summary-preview-dividend-line" points={linePoints.dividend} className="summary-preview-dividend-line" />
          ) : null}
          {linePoints.price ? (
            <polyline data-testid="summary-preview-price-line" points={linePoints.price} className="summary-preview-price-line" />
          ) : null}
          {previewMarkers.map((marker) => (
            <g
              key={`${marker.scope}-${marker.row.fiscal_year}`}
              role="button"
              tabIndex={0}
              aria-label={`Audit ${marker.scope} ${selectedPreviewFactLabel} FY${marker.row.fiscal_year}`}
              data-testid={`summary-preview-marker-${marker.row.fiscal_year}`}
              className={`summary-preview-year-marker ${marker.row.forecast_flag ? "forecast" : ""}`}
              onClick={() => selectPreviewYear(marker)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  selectPreviewYear(marker);
                }
              }}
            >
              <title>{`FY${marker.row.fiscal_year} ${marker.scope}.${selectedPreviewFact}`}</title>
              <circle cx={marker.x} cy={marker.y} r={marker.row.forecast_flag ? "1.75" : "1.45"} />
            </g>
          ))}
        </svg>
        <div className="summary-preview-legend" aria-label="Summary valuation preview legend">
          <span><i className="legend-price" /> Price</span>
          <span><i className="legend-fundamental" /> Metric area</span>
          <span><i className="legend-normal" /> Normal multiple</span>
          <span><i className="legend-fair" /> Fair value</span>
          <span><i className="legend-dividend" /> Dividend</span>
        </div>
        <small>
          {latestMethod} / {latestQuality} / {latestAuditRow?.source_trace?.source_document_id ?? "source_trace"}
        </small>
      </div>
    </section>
  );
}

function summaryPreviewFactValue(row: ValuationRow, fact: SummaryPreviewFact) {
  if (fact === "metric") {
    return toNumberOrNull(row.fair_value_price) ?? scaledMetricValue(row, "fair_multiple");
  }
  if (fact === "price") {
    return toNumberOrNull(row.price);
  }
  if (fact === "fair_value_price") {
    return toNumberOrNull(row.fair_value_price) ?? scaledMetricValue(row, "fair_multiple");
  }
  if (fact === "normal_multiple") {
    return scaledMetricValue(row, "normal_multiple");
  }
  if (fact === "dividend") {
    const dividend = toNumberOrNull(row.dividend);
    return dividend === null ? null : dividend * 15;
  }
  return null;
}

function scaledMetricValue(row: ValuationRow, multipleKey: "fair_multiple" | "normal_multiple") {
  const metric = toNumberOrNull(row.metric);
  const multiple = toNumberOrNull(row[multipleKey]);
  if (metric === null || multiple === null) {
    return null;
  }
  return metric * multiple;
}

function SummaryAuditMetric({
  label,
  value,
  factName,
  onSelect
}: {
  label: string;
  value: string;
  factName: string;
  onSelect: (factName: string) => void;
}) {
  return (
    <div className="metric-chip">
      <span>{label}</span>
      <button
        className="audit-cell-button"
        type="button"
        data-testid={`summary-audit-cell-${auditTestIdPart(factName)}`}
        aria-label={`Audit summary ${factName}`}
        onClick={() => onSelect(factName)}
      >
        {value}
      </button>
    </div>
  );
}

function WatchlistPanel({
  watchlist,
  auditRows,
  tickerInput,
  noteInput,
  status,
  onTickerInputChange,
  onNoteInputChange,
  onAddItem,
  onRemoveItem,
  onSelectTicker
}: {
  watchlist: WatchlistSummary;
  auditRows: AuditRow[];
  tickerInput: string;
  noteInput: string;
  status: string;
  onTickerInputChange: (value: string) => void;
  onNoteInputChange: (value: string) => void;
  onAddItem: () => void;
  onRemoveItem: (ticker: string) => void;
  onSelectTicker: (ticker: string) => void;
}) {
  const [selectedWatchlistCell, setSelectedWatchlistCell] = useState(() => ({
    ticker: watchlist.items[0]?.ticker ?? "",
    factName: "current_price"
  }));
  const selectedWatchlistItem =
    watchlist.items.find((row) => row.ticker === selectedWatchlistCell.ticker) ??
    watchlist.items[0];
  const selectedWatchlistAuditRow = selectedWatchlistItem
    ? auditRows.find(
        (row) =>
          row.fact_id.startsWith(`${selectedWatchlistItem.ticker}-`) &&
          row.fact_name === `watchlist.${selectedWatchlistCell.factName}`
      )
    : undefined;
  const selectWatchlistCell = (ticker: string, factName: string) => {
    setSelectedWatchlistCell({ ticker, factName });
  };
  return (
    <section className="single-panel">
      <div className="panel-header padded">
        <div>
          <h1>Watchlist</h1>
          <p>Owner-scoped watchlist with source-backed snapshot metrics and provenance labels.</p>
        </div>
        <div className="facts-row">
          <Metric label="List" value={watchlist.name} />
          <Metric label="Items" value={String(watchlist.items.length)} />
          <Metric label="Quality" value={String(watchlist.source_trace?.quality_status ?? "source_backed")} />
        </div>
      </div>
      <div className="watchlist-controls" aria-label="Watchlist controls">
        <label>
          Ticker
          <input
            aria-label="Watchlist ticker"
            value={tickerInput}
            onChange={(event) => onTickerInputChange(event.target.value.toUpperCase())}
          />
        </label>
        <label>
          Note
          <input
            aria-label="Watchlist note"
            value={noteInput}
            onChange={(event) => onNoteInputChange(event.target.value)}
          />
        </label>
        <button type="button" onClick={onAddItem}>Add</button>
        <span>{status}</span>
      </div>
      <table className="terminal-table wide">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Name</th>
            <th>Market</th>
            <th>Price</th>
            <th>P/E</th>
            <th>Div Yld</th>
            <th>EPS CAGR</th>
            <th>Quality</th>
            <th>Note</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {watchlist.items.map((row) => (
            <tr key={row.ticker}>
              <td>
                <button className="link-button" type="button" onClick={() => onSelectTicker(row.ticker)}>
                  {row.ticker}
                </button>
              </td>
              <td>{row.name}</td>
              <td>{row.market ?? row.country ?? "-"}</td>
              <td>
                <WatchlistAuditCellButton row={row} factName="current_price" onSelect={selectWatchlistCell}>
                  {row.current_price ?? "-"}
                </WatchlistAuditCellButton>
              </td>
              <td>
                <WatchlistAuditCellButton row={row} factName="per" onSelect={selectWatchlistCell}>
                  {row.per ?? "-"}
                </WatchlistAuditCellButton>
              </td>
              <td>
                <WatchlistAuditCellButton row={row} factName="dividend_yield" onSelect={selectWatchlistCell}>
                  {formatPercent(row.dividend_yield)}
                </WatchlistAuditCellButton>
              </td>
              <td>
                <WatchlistAuditCellButton row={row} factName="eps_cagr" onSelect={selectWatchlistCell}>
                  {formatPercent(row.eps_cagr)}
                </WatchlistAuditCellButton>
              </td>
              <td>{row.quality_status ?? String(row.source_trace?.quality_status ?? "-")}</td>
              <td>{row.note ?? "-"}</td>
              <td>
                <button className="link-button" type="button" onClick={() => onRemoveItem(row.ticker)}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="source-box">
        <strong>Watchlist trace</strong>
        <code>{JSON.stringify(watchlist.source_trace ?? {}, null, 2)}</code>
      </div>
      <SelectedAuditTrace
        row={selectedWatchlistAuditRow}
        fallbackTrace={selectedWatchlistItem?.source_trace ?? watchlist.source_trace}
        fallbackLabel={`watchlist.${selectedWatchlistCell.factName}`}
      />
    </section>
  );
}

function WatchlistAuditCellButton({
  row,
  factName,
  onSelect,
  children
}: {
  row: WatchlistSummary["items"][number];
  factName: string;
  onSelect: (ticker: string, factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`watchlist-audit-cell-${row.ticker}-${auditTestIdPart(factName)}`}
      aria-label={`Audit watchlist ${row.ticker} ${factName}`}
      onClick={() => onSelect(row.ticker, factName)}
    >
      {children}
    </button>
  );
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("request timeout")), timeoutMs);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        window.clearTimeout(timer);
        reject(error);
      }
    );
  });
}

function ProductTourModal({
  stepIndex,
  onClose,
  onPrevious,
  onNext
}: {
  stepIndex: number;
  onClose: () => void;
  onPrevious: () => void;
  onNext: () => void;
}) {
  const step = productTourSteps[stepIndex];
  const isFirst = stepIndex === 0;
  const isLast = stepIndex === productTourSteps.length - 1;
  return (
    <div className="product-tour-backdrop" role="presentation">
      <section className="product-tour-modal" role="dialog" aria-modal="true" aria-labelledby="product-tour-title">
        <button className="product-tour-close" type="button" onClick={onClose} aria-label="Close product tour">
          <X size={16} />
        </button>
        <span className="product-tour-kicker">LUXON tour</span>
        <h2 id="product-tour-title">{step.title}</h2>
        <p>{step.body}</p>
        <div className="product-tour-progress" aria-label="Product tour progress">
          {productTourSteps.map((item, index) => (
            <span key={item.title} className={index === stepIndex ? "active" : ""} />
          ))}
        </div>
        <div className="product-tour-meta">
          <span>{step.tab}</span>
          <span>{stepIndex + 1} of {productTourSteps.length}</span>
        </div>
        <div className="product-tour-actions">
          <button type="button" onClick={onPrevious} disabled={isFirst}>
            Previous
          </button>
          <button type="button" className="primary" onClick={isLast ? onClose : onNext}>
            {isLast ? "Done" : "Next"}
          </button>
        </div>
      </section>
    </div>
  );
}

function AuthScreen({
  title,
  detail,
  actionLabel,
  actionHref
}: {
  title: string;
  detail: string;
  actionLabel?: string;
  actionHref?: string;
}) {
  return (
    <main className="auth-shell">
      <section className="auth-panel">
        <BrandMark />
        <h1>{title}</h1>
        <p>{detail}</p>
        {actionHref ? (
          <a className="auth-button" href={actionHref}>
            <LogIn size={16} />
            {actionLabel}
          </a>
        ) : null}
      </section>
    </main>
  );
}

function formatMarketCap(raw: string | number | null | undefined, currency: string | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  const suffix = currency ? ` ${currency}` : "";
  if (!Number.isFinite(value)) {
    return `${String(raw)}${suffix}`;
  }
  return `${new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 2,
    notation: "compact"
  }).format(value)}${suffix}`;
}

function formatNumber(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return String(raw);
  }
  if (Math.abs(value) >= 1000) {
    return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatMaybeGrowth(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return String(raw);
  }
  return `${value.toFixed(1)}%`;
}

function normalizeResearchReport(raw: unknown): ResearchReport | null {
  if (!isRecord(raw) || !Array.isArray(raw.sections) || !Array.isArray(raw.executive_summary)) {
    return null;
  }
  const sections = raw.sections
    .map(normalizeResearchReportSection)
    .filter((section): section is ResearchReportSection => section !== null);
  if (!sections.length) {
    return null;
  }
  return {
    ticker: String(raw.ticker ?? ""),
    title: String(raw.title ?? "Source-Audited Research Report"),
    fiscal_year: toNumberOrNull(raw.fiscal_year),
    data_mode: String(raw.data_mode ?? "unknown"),
    executive_summary: raw.executive_summary.map((item) => String(item)),
    sections,
    audit_facts: Array.isArray(raw.audit_facts)
      ? raw.audit_facts.filter(isRecord).map((fact) => ({
          fact_name: String(fact.fact_name ?? ""),
          value: typeof fact.value === "number" || typeof fact.value === "string" ? fact.value : null,
          fiscal_year: toNumberOrNull(fact.fiscal_year) ?? 0,
          source_trace: isRecord(fact.source_trace) ? fact.source_trace : {}
        }))
      : [],
    flags: toStringArray(raw.flags),
    quality_status: String(raw.quality_status ?? "unknown"),
    source_trace: isRecord(raw.source_trace) ? raw.source_trace : {}
  };
}

function normalizeResearchReportSection(raw: unknown): ResearchReportSection | null {
  if (!isRecord(raw) || !Array.isArray(raw.evidence)) {
    return null;
  }
  return {
    section_key: String(raw.section_key ?? raw.title ?? "section"),
    title: String(raw.title ?? "Section"),
    verdict: String(raw.verdict ?? "not_scored"),
    bullets: toStringArray(raw.bullets),
    evidence: raw.evidence.filter(isRecord).map((item) => ({
      label: String(item.label ?? "Evidence"),
      value: typeof item.value === "number" || typeof item.value === "string" ? item.value : null,
      unit: String(item.unit ?? "reported"),
      source_trace: isRecord(item.source_trace) ? item.source_trace : {}
    })),
    flags: toStringArray(raw.flags),
    quality_status: String(raw.quality_status ?? "unknown"),
    source_trace: isRecord(raw.source_trace) ? raw.source_trace : {}
  };
}

function normalizeResearchMetadata(raw: unknown): ResearchMetadata {
  if (!isRecord(raw)) {
    return fallbackResearchMetadata;
  }
  const items = Array.isArray(raw.items)
    ? raw.items.filter(isRecord).map((item) => ({
        source: String(item.source ?? ""),
        source_label: String(item.source_label ?? item.source ?? "research_metadata"),
        ticker: String(item.ticker ?? raw.ticker ?? ""),
        identifier: String(item.identifier ?? ""),
        title: String(item.title ?? "Research metadata"),
        link: String(item.link ?? ""),
        description: String(item.description ?? ""),
        source_url: String(item.source_url ?? ""),
        source_document_id: String(item.source_document_id ?? ""),
        content_hash: String(item.content_hash ?? ""),
        content_type: String(item.content_type ?? ""),
        item_count: toNumberOrNull(item.item_count) ?? 0,
        financial_numbers_allowed: item.financial_numbers_allowed === true,
        terms_note: String(item.terms_note ?? ""),
        source_note: String(item.source_note ?? ""),
        source_trace: isRecord(item.source_trace) ? item.source_trace : {}
      }))
    : [];
  return {
    ticker: String(raw.ticker ?? ""),
    data_mode: String(raw.data_mode ?? "source_backed_required"),
    policy: String(raw.policy ?? "metadata_only_no_financial_numbers"),
    quality_status: String(raw.quality_status ?? (items.length ? "source_backed_research_metadata" : "missing_source_backed_data")),
    items,
    source_trace: isRecord(raw.source_trace) ? raw.source_trace : {},
    meta: isRecord(raw.meta) ? raw.meta : {}
  };
}

function normalizePerformance(raw: unknown): PerformanceSummary | null {
  if (!isRecord(raw) || !Array.isArray(raw.rows)) {
    return null;
  }
  return {
    ticker: String(raw.ticker ?? ""),
    currency: String(raw.currency ?? ""),
    initial_investment: String(raw.initial_investment ?? ""),
    rows: raw.rows.filter(isRecord).map((row) => ({
      start_year: toNumberOrNull(row.start_year) ?? 0,
      end_year: toNumberOrNull(row.end_year) ?? 0,
      years: toNumberOrNull(row.years) ?? 0,
      start_price: String(row.start_price ?? ""),
      end_price: String(row.end_price ?? ""),
      shares_purchased: String(row.shares_purchased ?? ""),
      initial_investment: String(row.initial_investment ?? ""),
      ending_value: String(row.ending_value ?? ""),
      dividends_received: String(row.dividends_received ?? ""),
      reinvested_shares: String(row.reinvested_shares ?? row.shares_purchased ?? ""),
      reinvested_dividends: String(row.reinvested_dividends ?? row.dividends_received ?? ""),
      reinvested_ending_value: String(row.reinvested_ending_value ?? row.ending_value ?? ""),
      capital_gain: String(row.capital_gain ?? ""),
      total_gain: String(row.total_gain ?? ""),
      reinvested_total_gain: String(row.reinvested_total_gain ?? row.total_gain ?? ""),
      price_return_pct: String(row.price_return_pct ?? ""),
      dividend_return_pct: String(row.dividend_return_pct ?? ""),
      total_return_pct: String(row.total_return_pct ?? ""),
      reinvested_total_return_pct: String(row.reinvested_total_return_pct ?? row.total_return_pct ?? ""),
      annualized_price_return_pct: String(row.annualized_price_return_pct ?? ""),
      annualized_total_return_pct: String(row.annualized_total_return_pct ?? ""),
      reinvested_annualized_total_return_pct: String(row.reinvested_annualized_total_return_pct ?? row.annualized_total_return_pct ?? ""),
      quality_status: String(row.quality_status ?? "unknown"),
      flags: toStringArray(row.flags),
      source_trace: isRecord(row.source_trace) ? row.source_trace : {}
    })),
    summary: normalizeScalarRecord(raw.summary),
    quality_status: String(raw.quality_status ?? "unknown"),
    flags: toStringArray(raw.flags),
    source_trace: isRecord(raw.source_trace) ? raw.source_trace : {}
  };
}

function normalizeFunGraphs(raw: unknown): FunGraphs | null {
  if (!isRecord(raw) || !Array.isArray(raw.metrics) || !isRecord(raw.summary)) {
    return null;
  }
  const metrics = raw.metrics
    .filter(isRecord)
    .map((metric) => ({
      metric_key: String(metric.metric_key ?? ""),
      label: String(metric.label ?? metric.metric_key ?? "Metric"),
      unit: String(metric.unit ?? "reported"),
      statement: String(metric.statement ?? "financial"),
      formula: String(metric.formula ?? ""),
      points: Array.isArray(metric.points)
        ? metric.points.filter(isRecord).map((point) => ({
            fiscal_year: toNumberOrNull(point.fiscal_year) ?? 0,
            value: point.value === null || point.value === undefined ? null : String(point.value),
            method: String(point.method ?? "source_trace"),
            confidence: point.confidence === null || point.confidence === undefined ? null : String(point.confidence),
            quality_status: String(point.quality_status ?? "unknown"),
            flags: toStringArray(point.flags),
            source_trace: isRecord(point.source_trace) ? point.source_trace : {}
          }))
        : [],
      quality_status: String(metric.quality_status ?? "unknown"),
      flags: toStringArray(metric.flags)
    }))
    .filter((metric) => metric.metric_key && metric.points.length);
  if (!metrics.length) {
    return null;
  }
  return {
    ticker: String(raw.ticker ?? ""),
    currency: String(raw.currency ?? ""),
    metrics,
    summary: {
      latest_year: toNumberOrNull(raw.summary.latest_year),
      metric_count: toNumberOrNull(raw.summary.metric_count) ?? metrics.length,
      point_count: toNumberOrNull(raw.summary.point_count) ?? metrics.reduce((sum, metric) => sum + metric.points.length, 0),
      quality_status: String(raw.summary.quality_status ?? "unknown"),
      flags: toStringArray(raw.summary.flags)
    },
    source_trace: isRecord(raw.source_trace) ? raw.source_trace : {}
  };
}

function normalizeAnalystScorecard(raw: unknown): AnalystScorecard | null {
  if (!isRecord(raw) || !Array.isArray(raw.rows) || !isRecord(raw.summary)) {
    return null;
  }
  return {
    ticker: String(raw.ticker ?? ""),
    status: String(raw.status ?? "pending_actual_overlap"),
    rows: raw.rows.filter(isRecord).map((row) => ({
      fiscal_year: toNumberOrNull(row.fiscal_year) ?? 0,
      actual_eps: scalarToString(row.actual_eps),
      estimate_1y_prior: scalarToString(row.estimate_1y_prior),
      estimate_2y_prior: scalarToString(row.estimate_2y_prior),
      error_1y_pct: scalarToString(row.error_1y_pct),
      error_2y_pct: scalarToString(row.error_2y_pct),
      result_1y: String(row.result_1y ?? "not_available"),
      result_2y: String(row.result_2y ?? "not_available"),
      quality_status: String(row.quality_status ?? "unknown"),
      flags: toStringArray(row.flags),
      source_trace: isRecord(row.source_trace) ? row.source_trace : {}
    })),
    summary: {
      hit_rate_1y_pct: String(raw.summary.hit_rate_1y_pct ?? "0.00"),
      hit_rate_2y_pct: String(raw.summary.hit_rate_2y_pct ?? "0.00"),
      scored_years: toNumberOrNull(raw.summary.scored_years) ?? 0,
      required_source: String(raw.summary.required_source ?? "point_in_time_consensus_snapshots"),
      quality_status: String(raw.summary.quality_status ?? "unknown"),
      flags: toStringArray(raw.summary.flags)
    },
    quality_status: String(raw.quality_status ?? "unknown"),
    flags: toStringArray(raw.flags),
    source_trace: isRecord(raw.source_trace) ? raw.source_trace : {}
  };
}

function normalizePricePoints(raw: unknown, fallbackRows: ValuationRow[] = fallbackValuation): PricePoint[] {
  if (!Array.isArray(raw)) {
    return buildAnnualPricePoints(fallbackRows);
  }
  const rows = raw
    .filter(isRecord)
    .map((point) => {
      const date = String(point.date ?? "");
      const rawClosePrice = point.close_price ?? point.price;
      const closePrice = toNumberOrNull(rawClosePrice);
      const fiscalYear = Number(point.fiscal_year);
      if (!date || closePrice === null || closePrice <= 0 || !Number.isFinite(fiscalYear)) {
        return null;
      }
      return {
        date,
        fiscal_year: fiscalYear,
        close_price: String(rawClosePrice),
        currency: scalarToString(point.currency),
        frequency: scalarToString(point.frequency),
        source_trace: isRecord(point.source_trace) ? point.source_trace : {}
      };
    })
    .filter((point): point is PricePoint => point !== null)
    .sort((left, right) => left.date.localeCompare(right.date));
  return rows.length ? rows : buildAnnualPricePoints(fallbackRows);
}

function buildAnnualPricePoints(rows: ValuationRow[]): PricePoint[] {
  return rows
    .filter((row) => !row.forecast_flag && Number(row.price) > 0)
    .map((row) => ({
      date: `${row.fiscal_year}-12-31`,
      fiscal_year: row.fiscal_year,
      close_price: row.price,
      currency: null,
      frequency: "annual",
      source_trace: row.source_trace ?? {}
    }));
}

function parseRangeYear(value: string) {
  const year = Number(value);
  if (!Number.isInteger(year) || year < 1900 || year > 2200) {
    return null;
  }
  return year;
}

function filterValuationByRange(rows: ValuationRow[], startYearValue: string, endYearValue: string) {
  const startYear = parseRangeYear(startYearValue);
  const endYear = parseRangeYear(endYearValue);
  if (startYear === null && endYear === null) {
    return rows;
  }
  const latestActual = latestHistoricalYear(rows);
  const filtered = rows.filter((row) => {
    const fiscalYear = Number(row.fiscal_year);
    if (!Number.isFinite(fiscalYear)) {
      return false;
    }
    const isForwardEstimate = row.forecast_flag && latestActual !== null && fiscalYear > latestActual;
    if (isForwardEstimate) {
      return true;
    }
    if (startYear !== null && fiscalYear < startYear) {
      return false;
    }
    if (endYear !== null && fiscalYear > endYear) {
      return false;
    }
    return true;
  });
  return filtered.length ? filtered : rows;
}

function filterPricePointsByValuationRange(points: PricePoint[], rows: ValuationRow[]) {
  const actualYears = new Set(rows.filter((row) => !row.forecast_flag).map((row) => row.fiscal_year));
  if (!actualYears.size) {
    return [];
  }
  return points.filter((point) => actualYears.has(point.fiscal_year));
}

function buildDisplayRangeSummary(rows: ValuationRow[], rangeMode: string) {
  const reportedRows = rows.filter((row) => !row.forecast_flag);
  const forecastRows = rows.filter((row) => row.forecast_flag);
  const firstYear = reportedRows[0]?.fiscal_year ?? rows[0]?.fiscal_year;
  const lastReportedYear = reportedRows.at(-1)?.fiscal_year ?? firstYear;
  const label = rangeMode === "custom" ? "CUSTOM" : rangeMode === "max" ? "MAX" : `${rangeMode}Y`;
  if (!rows.length || firstYear === undefined || lastReportedYear === undefined) {
    return `${label} | no source-backed rows`;
  }
  const forecastSuffix = forecastRows.length ? ` + ${forecastRows.length} forecast` : "";
  return `${label} | ${firstYear}-${lastReportedYear} | ${reportedRows.length} reported${forecastSuffix}`;
}

function scalarToString(value: unknown): string | null {
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

function normalizeScalarRecord(value: unknown): Record<string, string | number | null> {
  if (!isRecord(value)) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      typeof item === "string" || typeof item === "number" || item === null ? item : String(item)
    ])
  );
}

function normalizeNumberRecord(value: unknown): Record<string, number> {
  if (!isRecord(value)) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, toNumberOrNull(item) ?? 0])
  );
}

function normalizeStringArrayRecord(value: unknown): Record<string, string[]> {
  if (!isRecord(value)) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, toStringArray(item)])
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringTraceValue(trace: Record<string, unknown>, key: string): string | undefined {
  const value = trace[key];
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return undefined;
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function toNumberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatAnyValue(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  return Number.isFinite(value) ? formatNumber(value) : String(raw);
}

function formatPercent(raw: string | number | null | undefined) {
  const formatted = formatNumber(raw);
  return formatted === "-" ? "-" : `${formatted}%`;
}

function formatConfidence(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : String(raw);
}
