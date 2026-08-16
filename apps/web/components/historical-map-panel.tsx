"use client";

import type { KeyboardEvent, MouseEvent, PointerEvent, ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { Metric, Toggle } from "./terminal-primitives";
import {
  buildLinePoints,
  buildMetricAreaPath,
  buildRatioLinePoints,
  buildRecessionRects,
  buildScenarioLinePoints,
  buildTradeOverlayPoints,
  currentValuationMultiple,
  forecastCalculationLines,
  formatMaybePercent,
  isYearInReturnRange,
  latestDividendRatioMetrics,
  maxChartValue
} from "../lib/terminal-chart";
import type {
  AuditRow,
  ChartReturnSelection,
  ForecastMeta,
  KrValuationCacheCoverage,
  LineVisibility,
  PortfolioTransactionView,
  PricePoint,
  RecessionBand,
  ValuationRow
} from "../lib/terminal-types";

export function HistoricalMapPanel({
  ticker,
  activeTab,
  valuation,
  auditRows,
  auditQueryString,
  selectedYear,
  visibility,
  forecastMeta,
  recessionBands,
  pricePoints,
  krCacheCoverage,
  latest,
  forecastMode,
  normalMultipleYears,
  displayRangeSummary,
  targetMultiple,
  transactions,
  returnSelectionYears,
  returnSelection,
  hiddenScenarioLines,
  settingsOpen: controlledSettingsOpen,
  onSelectYear,
  onSetReturnSelectionYears,
  onSelectAuditYear,
  onFocusAuditFact,
  onOpenDataAudit,
  onSettingsOpenChange,
  onToggle
}: {
  ticker: string;
  activeTab: string;
  valuation: ValuationRow[];
  auditRows: AuditRow[];
  auditQueryString: string;
  selectedYear: number;
  visibility: LineVisibility;
  forecastMeta: ForecastMeta;
  recessionBands: RecessionBand[];
  pricePoints: PricePoint[];
  krCacheCoverage: KrValuationCacheCoverage | null;
  latest?: ValuationRow;
  forecastMode: string;
  normalMultipleYears: number;
  displayRangeSummary: string;
  targetMultiple: number;
  transactions: PortfolioTransactionView[];
  returnSelectionYears: number[];
  returnSelection: ChartReturnSelection | null;
  hiddenScenarioLines: string[];
  settingsOpen?: boolean;
  onSelectYear: (year: number) => void;
  onSetReturnSelectionYears: (years: number[]) => void;
  onSelectAuditYear: (year: number) => void;
  onFocusAuditFact: (factId: string, factFamily?: string | null) => void;
  onOpenDataAudit: () => void;
  onSettingsOpenChange?: (open: boolean) => void;
  onToggle: (key: keyof LineVisibility) => void;
}) {
  const [selectedAuditFactName, setSelectedAuditFactName] = useState("metric");
  const [hoveredYear, setHoveredYear] = useState<number | null>(null);
  const [navigatorDrag, setNavigatorDrag] = useState<{ startYear: number; currentYear: number } | null>(null);
  const [internalSettingsOpen, setInternalSettingsOpen] = useState(false);
  const settingsOpen = controlledSettingsOpen ?? internalSettingsOpen;
  const setSettingsOpen = onSettingsOpenChange ?? setInternalSettingsOpen;
  const encodedTicker = encodeURIComponent(ticker);
  const chartSvgExportHref = `/api/v1/charts/valuation-map/${encodedTicker}.svg?${auditQueryString}`;
  const chartPngExportHref = `/api/v1/charts/valuation-map/${encodedTicker}.png?${auditQueryString}`;
  const currentMultiple = useMemo(() => currentValuationMultiple(valuation), [valuation]);
  const customMultiple = visibility.customValuation && targetMultiple > 0 ? targetMultiple : null;
  const selectedValuationRow = useMemo(
    () => valuation.find((row) => row.fiscal_year === selectedYear) ?? valuation.at(-1),
    [valuation, selectedYear]
  );
  const activeChartRow = useMemo(
    () => valuation.find((row) => row.fiscal_year === (hoveredYear ?? selectedYear)) ?? selectedValuationRow,
    [hoveredYear, selectedValuationRow, selectedYear, valuation]
  );
  const selectedAuditScope = selectedValuationRow?.forecast_flag ? "forecast" : "valuation";
  const activeAuditScope = activeChartRow?.forecast_flag ? "forecast" : "valuation";
  const selectedAuditRows = useMemo(
    () =>
      auditRows.filter(
        (row) =>
          row.fiscal_year === selectedValuationRow?.fiscal_year &&
          (row.fact_name ?? "").startsWith(`${selectedAuditScope}.`)
      ),
    [auditRows, selectedAuditScope, selectedValuationRow?.fiscal_year]
  );
  const activeMetricAuditRow = useMemo(
    () =>
      auditRows.find(
        (row) =>
          row.fiscal_year === activeChartRow?.fiscal_year &&
          row.fact_name === `${activeAuditScope}.metric`
      ),
    [activeAuditScope, activeChartRow?.fiscal_year, auditRows]
  );
  const selectedAuditFact =
    selectedAuditRows.find((row) => row.fact_name === `${selectedAuditScope}.${selectedAuditFactName}`) ??
    selectedAuditRows.find((row) => row.fact_name === `${selectedAuditScope}.metric`) ??
    selectedAuditRows[0];
  const selectedAuditFallbackRow = selectedValuationRow ?? activeChartRow;
  const selectedAuditDrawerRows = useMemo(() => {
    const preferred = ["metric", "price", "normal_multiple", "fair_value_price", "yoy", "dividend"];
    const used = new Set<string>();
    const ordered: AuditRow[] = [];
    for (const suffix of preferred) {
      const match = selectedAuditRows.find((row) => auditFactSuffix(row.fact_name) === suffix);
      if (match && !used.has(match.fact_id)) {
        ordered.push(match);
        used.add(match.fact_id);
      }
    }
    for (const row of selectedAuditRows) {
      if (!used.has(row.fact_id)) {
        ordered.push(row);
        used.add(row.fact_id);
      }
    }
    return ordered.slice(0, 8);
  }, [selectedAuditRows]);
  const chartMax = useMemo(() => maxChartValue(valuation, forecastMeta, currentMultiple, customMultiple, pricePoints), [valuation, forecastMeta, currentMultiple, customMultiple, pricePoints]);
  const linePoints = useMemo(() => buildLinePoints(valuation, chartMax, currentMultiple, customMultiple, pricePoints), [valuation, chartMax, currentMultiple, customMultiple, pricePoints]);
  const payoutRatioPoints = useMemo(() => buildRatioLinePoints(valuation, (row) => Number(row.dividend) / Number(row.metric) * 100, 100), [valuation]);
  const dividendYieldPoints = useMemo(() => buildRatioLinePoints(valuation, (row) => Number(row.dividend) / Number(row.price) * 100, 8), [valuation]);
  const recessionRects = useMemo(() => buildRecessionRects(valuation, recessionBands), [valuation, recessionBands]);
  const latestDividendRatios = useMemo(() => latestDividendRatioMetrics(valuation), [valuation]);
  const metricAreaPath = useMemo(() => buildMetricAreaPath(valuation, chartMax), [valuation, chartMax]);
  const scenarioLinePoints = useMemo(
    () => buildScenarioLinePoints(valuation, forecastCalculationLines(forecastMeta, valuation), chartMax),
    [valuation, forecastMeta, chartMax]
  );
  const visibleScenarioLinePoints = useMemo(
    () => scenarioLinePoints.filter((line) => !hiddenScenarioLines.includes(line.label)),
    [scenarioLinePoints, hiddenScenarioLines]
  );
  const visibleScenarioLineLabels = useMemo(
    () =>
      visibleScenarioLinePoints
        .map((line) => {
          const position = scenarioLineLabelPosition(line.points);
          return position ? { ...position, label: line.label } : null;
        })
        .filter((label): label is { label: string; x: string; y: string } => Boolean(label)),
    [visibleScenarioLinePoints]
  );
  const tradeOverlayPoints = useMemo(
    () => buildTradeOverlayPoints(valuation, transactions, chartMax, pricePoints),
    [valuation, transactions, chartMax, pricePoints]
  );
  const annualPriceBands = useMemo(
    () => buildAnnualPriceBands(valuation, pricePoints),
    [pricePoints, valuation]
  );
  const forecastYearCount = useMemo(() => resolveForecastYears(forecastMeta, valuation), [forecastMeta, valuation]);
  const navigatorDragging = navigatorDrag ? navigatorDrag.currentYear !== navigatorDrag.startYear : false;
  const layerAuditRows = useMemo(
    () =>
      buildChartLayerAuditRows({
        visibility,
        auditRows,
        valuation,
        pricePoints,
        recessionBands,
        recessionRects,
        forecastMeta,
        forecastYearCount,
        selectedAuditRows,
        scenarioLineCount: scenarioLinePoints.length,
        visibleScenarioLineCount: visibleScenarioLinePoints.length,
        tradeOverlayCount: tradeOverlayPoints.length
      }),
    [
      auditRows,
      forecastMeta,
      pricePoints,
      recessionBands,
      recessionRects,
      selectedAuditRows,
      forecastYearCount,
      scenarioLinePoints.length,
      tradeOverlayPoints.length,
      valuation,
      visibility,
      visibleScenarioLinePoints.length
    ]
  );
  const chartSettingsLayerRows = useMemo<Array<{
    key: keyof LineVisibility;
    label: string;
    testId: string;
    auditKey: string;
    visible: boolean;
  }>>(
    () => [
      { key: "price", label: "Price", testId: "price", auditKey: "price", visible: visibility.price },
      { key: "metricArea", label: "Metric area", testId: "metric-area", auditKey: "metric-area", visible: visibility.metricArea },
      { key: "normalMultiple", label: "Normal P/E", testId: "normal-pe", auditKey: "normal-multiple", visible: visibility.normalMultiple },
      { key: "fairValue", label: "Fair value", testId: "fair-value", auditKey: "fair-value", visible: visibility.fairValue },
      { key: "forecast", label: "Forecast", testId: "forecast", auditKey: "forecast", visible: visibility.forecast },
      { key: "scenarioLines", label: "Scenario lines", testId: "scenario-lines", auditKey: "scenario-lines", visible: visibility.scenarioLines },
      { key: "currentValuation", label: "Current valuation", testId: "current-valuation", auditKey: "current-valuation", visible: visibility.currentValuation },
      { key: "customValuation", label: "Custom valuation", testId: "custom-valuation", auditKey: "custom-valuation", visible: visibility.customValuation },
      { key: "dividendFloor", label: "Dividend floor", testId: "dividend-floor", auditKey: "dividend", visible: visibility.dividendFloor },
      { key: "payoutRatio", label: "Payout ratio", testId: "payout-ratio", auditKey: "payout-ratio", visible: visibility.payoutRatio },
      { key: "dividendYield", label: "Dividend yield", testId: "dividend-yield", auditKey: "dividend-yield", visible: visibility.dividendYield },
      { key: "recessionBands", label: "Recession bands", testId: "recession-bands", auditKey: "recessions", visible: visibility.recessionBands }
    ],
    [visibility]
  );
  const visibleLayerCount = chartSettingsLayerRows.filter((row) => row.visible).length;
  const chartSettingsSourceBackedCount = layerAuditRows.filter(
    (row) =>
      row.visible &&
      !["forecast", "scenario-lines", "transactions"].includes(row.key) &&
      row.quality !== "pending"
  ).length;
  const chartSettingsForecastLedger = layerAuditRows.filter((row) => row.key === "forecast" || row.key === "scenario-lines");
  const chartSettingsOffLayerCount = chartSettingsLayerRows.length - visibleLayerCount;
  const selectedTrace = selectedAuditFact?.source_trace ?? selectedAuditFallbackRow?.source_trace ?? {};
  const selectedAuditEvidence = {
    source: traceRecordText(selectedTrace, "source", traceRecordText(selectedTrace, "source_type", "source_trace")),
    sourceDocument: traceRecordText(selectedTrace, "source_document_id", "-"),
    filing: traceRecordText(selectedTrace, "filing_id", traceRecordText(selectedTrace, "accession_number", "-")),
    availableAt: traceRecordText(selectedTrace, "available_at", "-"),
    period: traceRecordText(selectedTrace, "period", "-"),
    unit: traceRecordText(selectedTrace, "unit", "-"),
    currency: traceRecordText(selectedTrace, "currency", "-"),
    formula: selectedAuditFact?.formula ?? traceRecordText(selectedTrace, "formula", "source_trace fallback")
  };
  const evidenceMethod =
    selectedAuditFact?.method ??
    traceRecordText(selectedTrace, "method", traceTextOptional(selectedAuditFallbackRow, "method", "source_trace"));
  const evidenceConfidence =
    selectedAuditFact?.confidence ??
    traceRecordText(selectedTrace, "confidence", traceTextOptional(selectedAuditFallbackRow, "confidence", "-"));
  const selectedDividendEvidence = useMemo(
    () => buildDividendSourceEvidence(selectedValuationRow),
    [selectedValuationRow]
  );
  const methodCounts = useMemo(() => countNormalizationMethods(auditRows), [auditRows]);
  const sourceTraceCoverage = useMemo(() => buildValuationSourceTraceCoverage(valuation), [valuation]);
  const krCacheContract = useMemo(() => buildKrCacheContract(krCacheCoverage), [krCacheCoverage]);
  const historicalReadout = useMemo(
    () =>
      buildHistoricalMapReadout({
        valuation,
        auditRows,
        forecastMeta,
        forecastYearCount,
        selectedRow: selectedValuationRow,
        sourceTraceCoverage
      }),
    [auditRows, forecastMeta, forecastYearCount, selectedValuationRow, sourceTraceCoverage, valuation]
  );
  const chartSourceLocked = valuation.length === 0;
  const evidenceFlags = useMemo(() => summarizeEvidenceFlags(selectedAuditRows, selectedAuditFact), [selectedAuditFact, selectedAuditRows]);
  const sourceDocumentStatus = selectedAuditEvidence.sourceDocument === "-" ? "missing_source_document" : "source_document_id present";
  const selectedDecisionCards = useMemo(
    () => buildSelectedDecisionCards(selectedValuationRow, {
      evidenceFlags,
      sourceDocumentStatus
    }),
    [evidenceFlags, selectedValuationRow, sourceDocumentStatus]
  );
  const evidenceWaterfallRows = [
    { label: "Source", value: selectedAuditEvidence.source },
    { label: "Normalized fact", value: selectedAuditFact?.fact_name ?? `${selectedAuditScope}.metric` },
    { label: "Formula", value: selectedAuditEvidence.formula },
    { label: "Display value", value: selectedAuditFact?.value ? formatNumber(selectedAuditFact.value) : formatNumber(selectedValuationRow?.metric) }
  ];
  const chartInspector = useMemo(() => {
    if (!activeChartRow) {
      return null;
    }
    const rowIndex = valuation.findIndex((row) => row.fiscal_year === activeChartRow.fiscal_year);
    if (rowIndex < 0) {
      return null;
    }
    const price = Number(activeChartRow.price || activeChartRow.fair_value_price);
    const safePrice = Number.isFinite(price) && price > 0 ? price : Number(activeChartRow.fair_value_price);
    const x = valuation.length <= 1 ? 50 : ((rowIndex + 0.5) / valuation.length) * 100;
    const y = 100 - Math.min(95, Math.max(5, ((Number.isFinite(safePrice) ? safePrice : 0) / chartMax) * 82));
    const trace = activeMetricAuditRow?.source_trace ?? activeChartRow.source_trace ?? {};
    return {
      x,
      y,
      edgeRight: x > 68,
      method: activeMetricAuditRow?.method ?? traceText(activeChartRow, "method", activeChartRow.forecast_source ?? "source_trace"),
      confidence: activeMetricAuditRow?.confidence ?? traceText(activeChartRow, "confidence", "-"),
      quality: activeMetricAuditRow?.quality_status ?? traceText(activeChartRow, "quality_status", "pending_audit_row"),
      source: traceRecordText(trace, "source", traceRecordText(trace, "source_type", activeChartRow.forecast_source ?? "source_trace")),
      sourceDocument: traceRecordText(trace, "source_document_id", "-"),
      filing: traceRecordText(trace, "filing_id", traceRecordText(trace, "accession_number", "-")),
      availableAt: traceRecordText(trace, "available_at", "-"),
      formula: activeMetricAuditRow?.formula ?? traceRecordText(trace, "formula", "-")
    };
  }, [activeChartRow, activeMetricAuditRow, chartMax, valuation]);

  useEffect(() => {
    if (!navigatorDrag) {
      return undefined;
    }
    const clearNavigatorDrag = () => setNavigatorDrag(null);
    window.addEventListener("pointerup", clearNavigatorDrag);
    window.addEventListener("pointercancel", clearNavigatorDrag);
    return () => {
      window.removeEventListener("pointerup", clearNavigatorDrag);
      window.removeEventListener("pointercancel", clearNavigatorDrag);
    };
  }, [navigatorDrag]);

  function selectAuditFact(
    event: MouseEvent<HTMLButtonElement>,
    year: number,
    factName: string
  ) {
    event.stopPropagation();
    setSelectedAuditFactName(factName);
    onSelectAuditYear(year);
  }

  function selectChartPoint(year: number) {
    setSelectedAuditFactName("metric");
    onSelectYear(year);
  }

  function setNavigatorRange(startYear: number, endYear: number) {
    setSelectedAuditFactName("metric");
    setHoveredYear(endYear);
    onSelectYear(endYear);
    onSetReturnSelectionYears(
      startYear === endYear ? [startYear] : [startYear, endYear].sort((left, right) => left - right)
    );
  }

  function handleNavigatorPointerDown(event: PointerEvent<HTMLButtonElement>, year: number) {
    if (event.button !== 0) {
      return;
    }
    setHoveredYear(year);
    setNavigatorDrag({ startYear: year, currentYear: year });
  }

  function handleNavigatorPointerEnter(year: number) {
    if (!navigatorDrag || navigatorDrag.currentYear === year) {
      return;
    }
    setNavigatorDrag({ startYear: navigatorDrag.startYear, currentYear: year });
    setNavigatorRange(navigatorDrag.startYear, year);
  }

  function handleNavigatorPointerUp(year: number) {
    if (navigatorDrag && navigatorDrag.startYear !== year) {
      setNavigatorRange(navigatorDrag.startYear, year);
    }
    setNavigatorDrag(null);
  }

  function metricAuditFactForYear(year: number) {
    const row = valuation.find((entry) => entry.fiscal_year === year);
    const scope = row?.forecast_flag ? "forecast" : "valuation";
    return (
      auditRows.find((auditRow) => auditRow.fiscal_year === year && auditRow.fact_name === `${scope}.metric`) ??
      auditRows.find(
        (auditRow) =>
          auditRow.fiscal_year === year &&
          (auditRow.fact_name ?? "").startsWith(`${scope}.`) &&
          /adjusted_operating_eps|metric|eps/i.test(auditRow.fact_name ?? "")
      ) ??
      auditRows.find((auditRow) => auditRow.fiscal_year === year && (auditRow.fact_name ?? "").startsWith(`${scope}.`))
    );
  }

  function auditFamilyForAuditRow(row?: AuditRow) {
    const factName = row?.fact_name ?? "";
    const policy = row?.policy ?? "";
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
    return null;
  }

  function openChartPointAudit(year: number) {
    const auditFact = metricAuditFactForYear(year);
    setSelectedAuditFactName("metric");
    setHoveredYear(year);
    onSelectAuditYear(year);
    if (auditFact?.fact_id) {
      onFocusAuditFact(auditFact.fact_id, auditFamilyForAuditRow(auditFact));
    }
    onOpenDataAudit();
  }

  function moveChartPoint(event: KeyboardEvent<HTMLButtonElement>, direction: -1 | 1 | "first" | "last") {
    const currentYear = Number(event.currentTarget.dataset.yearColumn);
    const currentIndex = valuation.findIndex((row) => row.fiscal_year === currentYear);
    if (currentIndex < 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const nextIndex = direction === "first"
      ? 0
      : direction === "last"
        ? valuation.length - 1
        : Math.min(valuation.length - 1, Math.max(0, currentIndex + direction));
    const nextRow = valuation[nextIndex];
    if (!nextRow) {
      return;
    }
    setHoveredYear(nextRow.fiscal_year);
    selectChartPoint(nextRow.fiscal_year);
    const nextButton = event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(
      `[data-year-column="${nextRow.fiscal_year}"]`
    );
    nextButton?.focus();
  }

  function handleChartPointKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Enter" && event.shiftKey) {
      const year = Number(event.currentTarget.dataset.yearColumn);
      if (Number.isFinite(year)) {
        event.preventDefault();
        event.stopPropagation();
        openChartPointAudit(year);
      }
    } else if (event.key === "ArrowLeft") {
      moveChartPoint(event, -1);
    } else if (event.key === "ArrowRight") {
      moveChartPoint(event, 1);
    } else if (event.key === "Home") {
      moveChartPoint(event, "first");
    } else if (event.key === "End") {
      moveChartPoint(event, "last");
    }
  }

  function openAuditWorkspace(factId?: string) {
    const focusAuditRow = factId
      ? auditRows.find((row) => row.fact_id === factId)
      : selectedAuditFact;
    const focusFactId = focusAuditRow?.fact_id ?? factId ?? selectedAuditFact?.fact_id;
    if (focusFactId) {
      onFocusAuditFact(focusFactId, factId ? auditFamilyForAuditRow(focusAuditRow) : "all");
    }
    setSettingsOpen(false);
    onOpenDataAudit();
  }

  return (
    <section className="chart-panel">
      <div className="panel-header chart-hero-header">
        <div className="chart-hero-copy">
          <span className="chart-hero-eyebrow">LUXON Valuation Workspace</span>
          <h1>{activeTab === "Forecasting" ? "Forecast Calculator" : "Historical Valuation Map"}</h1>
          <p>Reported history, 1-5Y forward range, valuation overlays, and audit lineage in one workspace.</p>
        </div>
        <div className="facts-row chart-hero-kpis">
          <Metric label="Latest forecast price" value={latest?.price ?? "-"} />
          <Metric label="Forecast source" value={latest?.forecast_source ?? forecastMode} />
          <Metric label="Total CAGR" value={latest?.total_return_cagr_pct ? `${Number(latest.total_return_cagr_pct).toFixed(1)}%` : "-"} />
          <Metric label="Displayed range" value={displayRangeSummary} />
          <Metric label="Normal window" value={`${forecastMeta.normal_multiple?.window_years ?? normalMultipleYears}FY`} />
          <Metric label="Current multiple" value={currentMultiple ? `${currentMultiple.toFixed(1)}x` : "-"} />
          <Metric label="Custom multiple" value={visibility.customValuation ? `${targetMultiple.toFixed(1)}x` : "off"} />
          <Metric label="Payout / yield" value={`${formatMaybePercent(latestDividendRatios.payoutRatioPct)} / ${formatMaybePercent(latestDividendRatios.dividendYieldPct)}`} />
        </div>
      </div>

      <section className="historical-map-readout" data-testid="historical-map-readout" aria-label="Historical map source and forecast readout">
        <article data-testid="historical-readout-actual">
          <span>Actual history</span>
          <strong>{historicalReadout.actualCountLabel}</strong>
          <small>{historicalReadout.actualRangeLabel}</small>
        </article>
        <article data-testid="historical-readout-forecast">
          <span>Forecast runway</span>
          <strong>{historicalReadout.forecastCountLabel}</strong>
          <small>{historicalReadout.forecastRangeLabel}</small>
        </article>
        <article data-testid="historical-readout-source">
          <span>Source trace</span>
          <strong>{historicalReadout.sourceCoverageLabel}</strong>
          <small>{historicalReadout.sourceStatusLabel}</small>
        </article>
        <article data-testid="historical-readout-method">
          <span>Selected method</span>
          <strong title={historicalReadout.selectedMethodLabel}>{historicalReadout.selectedMethodLabel}</strong>
          <small title={historicalReadout.selectedSourceLabel}>{historicalReadout.selectedSourceLabel}</small>
        </article>
        <button type="button" data-testid="historical-readout-audit" onClick={() => openAuditWorkspace()}>
          Open Data Audit
        </button>
      </section>

      {krCacheContract ? (
        <section className="historical-kr-source-contract" data-testid="historical-kr-source-contract" aria-label="KR source-backed valuation contract">
          <div className="historical-kr-source-contract-heading">
            <span>KR E2E Source Contract</span>
            <strong data-testid="historical-kr-source-contract-status">{krCacheContract.statusLabel}</strong>
          </div>
          <dl>
            <div data-testid="historical-kr-source-contract-numbers">
              <dt>Financial numbers</dt>
              <dd>{krCacheContract.numbersLabel}</dd>
            </div>
            <div data-testid="historical-kr-source-contract-years">
              <dt>Valuation years</dt>
              <dd>{krCacheContract.valuationYearsLabel}</dd>
            </div>
            <div data-testid="historical-kr-source-contract-missing">
              <dt>Missing source years</dt>
              <dd>{krCacheContract.missingYearsLabel}</dd>
            </div>
            <div data-testid="historical-kr-source-contract-backend">
              <dt>Backend</dt>
              <dd>{krCacheContract.backendLabel}</dd>
            </div>
          </dl>
          <small data-testid="historical-kr-source-contract-flags">{krCacheContract.flagsLabel}</small>
        </section>
      ) : null}

      <div className="chart-settings-control-row">
        <div className="chart-workflow-chips" data-testid="chart-workflow-chips" aria-label="Historical map workflow">
          <span>Price vs fundamentals</span>
          <span>1-5Y forecast runway</span>
          <span>Layer toggles</span>
          <span>Source audit ready</span>
        </div>
        <div className="chart-settings-actions">
          <button
            type="button"
            className="chart-settings-toggle"
            data-testid="chart-settings-toggle"
            aria-expanded={settingsOpen}
            aria-controls="chart-settings-drawer"
            onClick={() => setSettingsOpen(!settingsOpen)}
          >
            Chart settings
          </button>
          <span>
            {visibleLayerCount}/{chartSettingsLayerRows.length} layers visible - selected{" "}
            {selectedValuationRow?.fiscal_year ?? "-"}
            {selectedValuationRow?.forecast_flag ? "E" : ""}
          </span>
        </div>
      </div>

      {settingsOpen ? (
        <section
          id="chart-settings-drawer"
          className="chart-settings-drawer"
          data-testid="chart-settings-drawer"
          aria-label="Chart settings drawer"
        >
          <div className="chart-settings-drawer-header">
            <div>
              <span>Chart settings</span>
              <strong>Layer visibility, forecast context, and audit route</strong>
            </div>
            <button type="button" data-testid="chart-settings-open-audit" onClick={() => openAuditWorkspace()}>
              Open Data Audit
            </button>
          </div>
          <div className="chart-settings-summary">
            <Metric label="Selected year" value={`${selectedValuationRow?.fiscal_year ?? "-"}${selectedValuationRow?.forecast_flag ? "E" : ""}`} />
            <Metric label="Displayed range" value={displayRangeSummary} />
            <Metric label="Normal window" value={`${forecastMeta.normal_multiple?.window_years ?? normalMultipleYears}FY`} />
            <Metric label="Forecast mode" value={forecastMode} />
            <Metric label="Forecast years" value={`${forecastYearCount}Y`} />
            <Metric label="Target P/E" value={`${targetMultiple.toFixed(1)}x`} />
            <Metric label="Audit facts" value={`${selectedAuditRows.length}`} />
          </div>
          <p className="chart-settings-source-guard" data-testid="chart-settings-source-guard">
            Layer toggles only change visibility. Values stay bound to source_trace rows or deterministic display formulas.
          </p>
          <div className="chart-settings-layer-ledger" data-testid="chart-settings-layer-ledger">
            <div className="chart-settings-layer-ledger-header">
              <span>Layer Ledger</span>
              <strong>Visibility is separate from source eligibility.</strong>
            </div>
            <div className="chart-settings-layer-ledger-grid">
              <article data-testid="chart-settings-layer-source-backed">
                <span>Source-backed on</span>
                <strong>
                  {chartSettingsSourceBackedCount}/{layerAuditRows.length}
                </strong>
                <small>Price, metric, multiple, fair value, dividend, and macro overlays keep source evidence.</small>
              </article>
              <article data-testid="chart-settings-layer-forecast">
                <span>Forecast assumptions</span>
                <strong>{forecastYearCount}Y</strong>
                <small>
                  {chartSettingsForecastLedger.map((row) => `${row.label}: ${row.quality}`).join(" / ")}
                </small>
              </article>
              <article data-testid="chart-settings-layer-audit-route">
                <span>Audit route</span>
                <strong>{selectedAuditRows.length} facts</strong>
                <small>Every visible chart layer can open the selected Data Audit context.</small>
              </article>
              <article data-testid="chart-settings-layer-off">
                <span>Off / locked</span>
                <strong>{chartSettingsOffLayerCount}</strong>
                <small>Hidden layers remain discoverable until a user or source_trace unlocks them.</small>
              </article>
            </div>
          </div>
          <div className="chart-settings-lines" aria-label="Chart settings layer switches">
            {chartSettingsLayerRows.map((row) => {
              const evidence = layerAuditRows.find((layer) => layer.key === row.auditKey) ?? chartSettingsFallbackEvidence(row.auditKey);
              return (
                <button
                  key={row.key}
                  type="button"
                  className={row.visible ? "on" : "off"}
                  data-testid={`chart-settings-line-${row.testId}`}
                  aria-pressed={row.visible}
                  onClick={() => onToggle(row.key)}
                >
                  <span>{row.label}</span>
                  <strong>{row.visible ? "on" : "off"}</strong>
                  <small data-testid={`chart-settings-evidence-${row.testId}`}>
                    {evidence.source} / {evidence.method} / {evidence.quality}
                  </small>
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

      {chartSourceLocked ? (
        <section className="historical-source-lock" data-testid="historical-source-lock" aria-label="Historical source lock">
          <div className="historical-source-lock-copy">
            <span>KR source-backed unlock required</span>
            <strong>Chart waits for source-traced rows.</strong>
            <p>
              The Historical Map UI is ready, but no price, EPS, normal multiple, fair value,
              dividend, or forecast line is rendered from fixture data.
            </p>
            <div className="historical-source-lock-actions">
              <button type="button" onClick={onOpenDataAudit}>Open Data Audit</button>
              <button type="button" onClick={() => setSettingsOpen(true)}>Review layer controls</button>
            </div>
          </div>
          <div className="historical-source-lock-preview" aria-label="Locked chart preview">
            <div className="historical-source-lock-canvas">
              <strong>source_trace gate</strong>
              <span>Run KR ingestion: OpenDART + pykrx + marcap</span>
            </div>
            <dl>
              <div><dt>Rows</dt><dd>0 source-backed</dd></div>
              <div><dt>Formula</dt><dd>blocked</dd></div>
              <div><dt>UI rule</dt><dd>no source_trace, no number</dd></div>
            </dl>
          </div>
        </section>
      ) : null}

      <section className="historical-high-low-strip" data-testid="historical-high-low-strip" aria-label="Annual high low price source table">
        <div className="historical-high-low-scroll">
          <table>
            <thead>
              <tr>
                <th>Year</th>
                {annualPriceBands.map((row) => (
                  <th key={`${row.fiscalYear}-year`}>
                    {row.fiscalYear}
                    {row.forecastFlag ? "E" : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>High</th>
                {annualPriceBands.map((row) => (
                  <td key={`${row.fiscalYear}-high`}>
                    {row.high === null ? (
                      <span>N/A</span>
                    ) : (
                      <PriceBandAuditButton row={row.valuationRow} kind="high" onSelect={selectAuditFact}>
                        {formatNumber(row.high)}
                      </PriceBandAuditButton>
                    )}
                  </td>
                ))}
              </tr>
              <tr>
                <th>Low</th>
                {annualPriceBands.map((row) => (
                  <td key={`${row.fiscalYear}-low`}>
                    {row.low === null ? (
                      <span>N/A</span>
                    ) : (
                      <PriceBandAuditButton row={row.valuationRow} kind="low" onSelect={selectAuditFact}>
                        {formatNumber(row.low)}
                      </PriceBandAuditButton>
                    )}
                  </td>
                ))}
              </tr>
              <tr>
                <th>Source</th>
                {annualPriceBands.map((row) => (
                  <td key={`${row.fiscalYear}-source`} title={`${row.sourceDocument} / ${row.quality}`}>
                    {row.sourceLabel}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
        <p>High/Low uses source-traced price rows only; clicking a value opens the selected year&apos;s price audit trace.</p>
      </section>

      <div className="historical-chart-workspace">
        <div className="chart-pan-shell" data-testid="mobile-chart-pan" aria-label="Horizontal chart pan">
          <div className="chart-canvas" role="img" aria-label="valuation map chart">
            <div className="axis-label">Price / value</div>
            <div className="bars" style={{ gridTemplateColumns: `repeat(${valuation.length}, minmax(0, 1fr))` }}>
              {valuation.map((row) => {
                const priceHeight = Math.max(8, (Number(row.price) / chartMax) * 82);
                const tradeMarkers = transactions.filter(
                  (transaction) => new Date(transaction.date).getFullYear() === row.fiscal_year
                );
                const inReturnRange = isYearInReturnRange(row.fiscal_year, returnSelectionYears);
                return (
                  <button
                    key={`${row.fiscal_year}-${row.forecast_flag}`}
                    className={`year-column ${row.forecast_flag && visibility.forecast ? "forecast" : ""} ${inReturnRange ? "return-range" : ""}`}
                    aria-label={`Select ${row.fiscal_year} return point`}
                    aria-keyshortcuts="ArrowLeft ArrowRight Home End Shift+Enter"
                    data-testid={`year-column-${row.fiscal_year}`}
                    data-year-column={row.fiscal_year}
                    onMouseEnter={() => setHoveredYear(row.fiscal_year)}
                    onMouseLeave={() => setHoveredYear(null)}
                    onFocus={() => setHoveredYear(row.fiscal_year)}
                    onBlur={() => setHoveredYear(null)}
                    onKeyDown={handleChartPointKeyDown}
                    onClick={() => selectChartPoint(row.fiscal_year)}
                  >
                    {visibility.price && !row.forecast_flag && <span className="price-point" style={{ bottom: `${priceHeight}%` }} />}
                    {visibility.fairValue && <span className="fair-point" style={{ bottom: `${Math.min(90, Number(row.fair_value_price) / chartMax * 82)}%` }} />}
                    {visibility.normalMultiple && <span className="normal-point" style={{ bottom: `${Math.min(90, Number(row.metric) * Number(row.normal_multiple ?? 0) / chartMax * 82)}%` }} />}
                    {visibility.currentValuation && currentMultiple && <span className="current-valuation-point" style={{ bottom: `${Math.min(90, Number(row.metric) * currentMultiple / chartMax * 82)}%` }} />}
                    {visibility.customValuation && customMultiple && <span className="custom-valuation-point" style={{ bottom: `${Math.min(90, Number(row.metric) * customMultiple / chartMax * 82)}%` }} />}
                    {visibility.dividendFloor && <span className="dividend-floor" style={{ height: `${Math.max(2, Number(row.dividend) * 6)}px` }} />}
                    {tradeMarkers.length ? (
                      <span className="trade-markers" aria-label={`${row.fiscal_year} portfolio transactions`}>
                        {tradeMarkers.map((transaction, index) => (
                          <span
                            key={`${transaction.date}-${transaction.side}-${index}`}
                            className={`trade-marker ${transaction.side}`}
                            title={`${transaction.side.toUpperCase()} ${transaction.quantity} @ ${transaction.price}`}
                          />
                        ))}
                      </span>
                    ) : null}
                    <small>{row.fiscal_year}{row.forecast_flag ? "E" : ""}</small>
                  </button>
                );
              })}
              {activeChartRow && chartInspector ? (
                <>
                  <span
                    className="chart-crosshair-vline"
                    data-testid="chart-crosshair-vline"
                    style={{ left: `${chartInspector.x}%` }}
                  />
                  <span
                    className="chart-crosshair-hline"
                    data-testid="chart-crosshair-hline"
                    style={{ top: `${chartInspector.y}%` }}
                  />
                  <div
                    className={`chart-hover-card ${chartInspector.edgeRight ? "edge-right" : ""}`}
                    data-testid="chart-hover-card"
                    style={{ left: `${chartInspector.x}%`, top: `${chartInspector.y}%` }}
                  >
                    <div className="chart-hover-card-title">
                      <strong>{activeChartRow.fiscal_year}{activeChartRow.forecast_flag ? "E" : ""}</strong>
                      <span>{activeChartRow.forecast_flag ? "Forecast" : "Reported"}</span>
                    </div>
                    <dl>
                      <div><dt>Price</dt><dd>{formatNumber(activeChartRow.price)}</dd></div>
                      <div><dt>Metric</dt><dd>{formatNumber(activeChartRow.metric)}</dd></div>
                      <div><dt>Fair</dt><dd>{formatNumber(activeChartRow.fair_value_price)}</dd></div>
                      <div><dt>Normal</dt><dd>{activeChartRow.normal_multiple ? `${formatNumber(activeChartRow.normal_multiple)}x` : "-"}</dd></div>
                      <div><dt>Div</dt><dd>{formatNumber(activeChartRow.dividend)}</dd></div>
                    </dl>
                    <div className="chart-hover-evidence" data-testid="chart-hover-evidence">
                      <span title={chartInspector.source}>Source {chartInspector.source}</span>
                      <span title={chartInspector.sourceDocument}>Doc {chartInspector.sourceDocument}</span>
                    </div>
                    <p>{chartInspector.method} / confidence {chartInspector.confidence}</p>
                    <em>{chartInspector.quality} / {chartInspector.availableAt}</em>
                  </div>
                </>
              ) : null}
            </div>
            <svg className="chart-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              {visibility.recessionBands && recessionRects.map((rect, index) => (
                <rect
                  key={`${rect.label}-${index}`}
                  data-testid={index === 0 ? "recession-band" : undefined}
                  className="recession-band"
                  x={rect.x}
                  y="0"
                  width={rect.width}
                  height="100"
                />
              ))}
              {visibility.metricArea && <path data-testid="metric-area-path" className="metric-area-path" d={metricAreaPath} />}
              {activeTab === "Forecasting" && visibility.scenarioLines && visibleScenarioLinePoints.map((line, index) => (
                <polyline
                  key={line.label}
                  data-testid={index === Math.floor(visibleScenarioLinePoints.length / 2) ? "forecast-scenario-line" : undefined}
                  className="chart-line scenario-line"
                  points={line.points}
                />
              ))}
              {activeTab === "Forecasting" && visibility.scenarioLines && visibleScenarioLineLabels.map((line) => (
                <text
                  key={`${line.label}-scenario-label`}
                  className="scenario-line-label"
                  data-testid={`forecast-scenario-label-${auditTestIdPart(line.label)}`}
                  x={line.x}
                  y={line.y}
                >
                  {line.label}
                </text>
              ))}
              {visibility.dividendFloor && <polyline data-testid="dividend-floor-line" className="chart-line dividend-line" points={linePoints.dividend} />}
              {visibility.fairValue && <polyline data-testid="fair-value-line" className="chart-line fair-line" points={linePoints.fair} />}
              {visibility.normalMultiple && <polyline data-testid="normal-multiple-line" className="chart-line normal-line" points={linePoints.normal} />}
              {visibility.currentValuation && <polyline data-testid="current-valuation-line" className="chart-line current-valuation-line" points={linePoints.current} />}
              {visibility.customValuation && <polyline data-testid="custom-valuation-line" className="chart-line custom-valuation-line" points={linePoints.custom} />}
              {visibility.payoutRatio && <polyline data-testid="payout-ratio-line" className="chart-line payout-ratio-line" points={payoutRatioPoints} />}
              {visibility.dividendYield && <polyline data-testid="dividend-yield-line" className="chart-line dividend-yield-line" points={dividendYieldPoints} />}
              {visibility.price && <polyline data-testid="price-line" data-price-points={pricePoints.length} className="chart-line price-line" points={linePoints.price} />}
              {tradeOverlayPoints.map((point, index) => (
                <g
                  key={`${point.transaction.date}-${point.transaction.side}-${index}`}
                  className={`portfolio-trade-overlay-marker ${point.transaction.side}`}
                  data-testid={index === 0 ? "portfolio-trade-overlay-marker" : undefined}
                  transform={`translate(${point.x} ${point.y})`}
                >
                  <title>{`${point.transaction.side.toUpperCase()} ${point.transaction.quantity} @ ${point.transaction.price} on ${point.transaction.date}`}</title>
                  <circle r="2.1" />
                  <path d={point.transaction.side === "sell" ? "M -2 -3 L 2 -3 L 0 -6 Z" : "M -2 3 L 2 3 L 0 6 Z"} />
                </g>
              ))}
            </svg>
          </div>
        </div>
        <aside className="historical-evidence-rail" data-testid="historical-evidence-rail" aria-label="Historical chart evidence rail">
          <div className="historical-evidence-rail-header">
            <span>Valuation Decision Rail</span>
            <strong>{selectedValuationRow?.fiscal_year ?? "-"}{selectedValuationRow?.forecast_flag ? "E" : ""} selected point</strong>
          </div>
          <p className="historical-evidence-contract" data-testid="historical-evidence-contract">
            Every plotted point opens source_trace. No value is AI-generated.
          </p>
          <div className="historical-decision-strip" data-testid="historical-decision-strip">
            {selectedDecisionCards.map((card) => (
              <div
                key={card.key}
                className={`decision-card ${card.tone}`}
                data-testid={`historical-decision-card-${card.key}`}
              >
                <span>{card.label}</span>
                <strong title={card.value}>{card.value}</strong>
                <small title={card.detail}>{card.detail}</small>
              </div>
            ))}
          </div>
          <div
            className={`historical-dividend-provenance${selectedDividendEvidence.zeroAssumption ? " zero-assumption" : ""}`}
            data-testid="historical-dividend-provenance"
          >
            <div className="historical-dividend-provenance-heading">
              <span>Dividend provenance</span>
              <strong data-testid="historical-dividend-provenance-value">
                {selectedDividendEvidence.value}
              </strong>
            </div>
            <dl>
              <div data-testid="historical-dividend-provenance-source">
                <dt>Source</dt>
                <dd title={selectedDividendEvidence.source}>{selectedDividendEvidence.source}</dd>
              </div>
              <div data-testid="historical-dividend-provenance-method">
                <dt>Method</dt>
                <dd title={selectedDividendEvidence.method}>{selectedDividendEvidence.method}</dd>
              </div>
              <div data-testid="historical-dividend-provenance-quality">
                <dt>Quality</dt>
                <dd title={selectedDividendEvidence.quality}>{selectedDividendEvidence.quality}</dd>
              </div>
            </dl>
            <small data-testid="historical-dividend-provenance-flag" title={selectedDividendEvidence.flags}>
              {selectedDividendEvidence.statusLabel}
            </small>
          </div>
          <div className="historical-source-contract-card" data-testid="historical-source-contract-card">
            <div className="historical-source-contract-heading">
              <span>Row source contract</span>
              <strong data-testid="historical-source-contract-status">{sourceTraceCoverage.statusLabel}</strong>
            </div>
            <dl>
              <div data-testid="historical-source-contract-total">
                <dt>Total rows</dt>
                <dd>{sourceTraceCoverage.completeRows}/{sourceTraceCoverage.totalRows}</dd>
              </div>
              <div data-testid="historical-source-contract-reported">
                <dt>Reported</dt>
                <dd>{sourceTraceCoverage.actualComplete}/{sourceTraceCoverage.actualRows}</dd>
              </div>
              <div data-testid="historical-source-contract-forecast">
                <dt>Forecast</dt>
                <dd>{sourceTraceCoverage.forecastComplete}/{sourceTraceCoverage.forecastRows}</dd>
              </div>
            </dl>
            <small data-testid="historical-source-contract-missing">{sourceTraceCoverage.firstMissingLabel}</small>
          </div>
          <dl className="historical-evidence-grid">
            <div data-testid="historical-evidence-method">
              <dt>Method</dt>
              <dd title={evidenceMethod}>{evidenceMethod}</dd>
            </div>
            <div data-testid="historical-evidence-confidence">
              <dt>Confidence</dt>
              <dd>{evidenceConfidence}</dd>
            </div>
            <div>
              <dt>S1 / S2 / S4</dt>
              <dd>{methodCounts.s1} / {methodCounts.s2} / {methodCounts.s4}</dd>
            </div>
            <div>
              <dt>Quality flags</dt>
              <dd title={evidenceFlags}>{evidenceFlags}</dd>
            </div>
            <div data-testid="historical-evidence-source-doc">
              <dt>source_document_id</dt>
              <dd title={selectedAuditEvidence.sourceDocument}>{sourceDocumentStatus}</dd>
            </div>
            <div data-testid="historical-evidence-available-at">
              <dt>available_at</dt>
              <dd title={selectedAuditEvidence.availableAt}>{selectedAuditEvidence.availableAt}</dd>
            </div>
            <div data-testid="historical-evidence-formula">
              <dt>Formula</dt>
              <dd title={selectedAuditEvidence.formula}>{selectedAuditEvidence.formula}</dd>
            </div>
          </dl>
          <div className="historical-evidence-waterfall" data-testid="historical-evidence-waterfall">
            <span>GAAP -&gt; Adjusted waterfall</span>
            {evidenceWaterfallRows.map((row) => (
              <div key={row.label}>
                <small>{row.label}</small>
                <strong title={row.value}>{row.value}</strong>
              </div>
            ))}
          </div>
          <div className="historical-evidence-actions">
            <button
              type="button"
              className="historical-evidence-open-audit"
              data-testid="historical-evidence-open-audit"
              onClick={() => openAuditWorkspace()}
            >
              Open Data Audit
            </button>
            <div className="historical-evidence-export-group" data-testid="historical-evidence-export" aria-label="Export SVG/PNG">
              <span>Export</span>
              <a
                className="historical-evidence-export"
                data-testid="historical-evidence-export-svg"
                href={chartSvgExportHref}
                download
              >
                SVG
              </a>
              <a
                className="historical-evidence-export"
                data-testid="historical-evidence-export-png"
                href={chartPngExportHref}
                download
              >
                PNG
              </a>
            </div>
          </div>
        </aside>
      </div>

      <div className="range-navigator" data-testid="range-navigator" aria-label="Fiscal year navigator">
        <div className="range-navigator-label">
          <strong>Range navigator</strong>
          <span>Click two fiscal years, or drag across the strip, to measure price and dividend return.</span>
        </div>
        <div
          className={`range-navigator-strip ${navigatorDragging ? "dragging" : ""}`}
          style={{ gridTemplateColumns: `repeat(${valuation.length}, minmax(0, 1fr))` }}
          data-dragging={navigatorDragging ? "true" : undefined}
        >
          <svg className="range-navigator-line" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {visibility.forecast && valuation.map((row, index) => (
              row.forecast_flag ? (
                <rect
                  key={`${row.fiscal_year}-navigator-forecast`}
                  className="range-navigator-forecast"
                  x={((index / Math.max(1, valuation.length)) * 100).toFixed(2)}
                  y="0"
                  width={(100 / Math.max(1, valuation.length)).toFixed(2)}
                  height="100"
                />
              ) : null
            ))}
            {visibility.price && (
              <polyline
                data-testid="range-navigator-line"
                className="range-navigator-price-line"
                points={linePoints.price}
              />
            )}
          </svg>
          {valuation.map((row) => {
            const inReturnRange = isYearInReturnRange(row.fiscal_year, returnSelectionYears);
            return (
              <button
                key={`${row.fiscal_year}-navigator`}
                type="button"
                className={`range-navigator-point ${row.forecast_flag && visibility.forecast ? "forecast" : ""} ${inReturnRange ? "return-range" : ""} ${navigatorDrag?.startYear === row.fiscal_year ? "drag-anchor" : ""}`}
                data-testid={`range-navigator-point-${row.fiscal_year}`}
                aria-current={selectedYear === row.fiscal_year ? "true" : undefined}
                aria-label={`Select ${row.fiscal_year} navigator point`}
                onMouseEnter={() => setHoveredYear(row.fiscal_year)}
                onMouseLeave={() => setHoveredYear(null)}
                onFocus={() => setHoveredYear(row.fiscal_year)}
                onBlur={() => setHoveredYear(null)}
                onPointerDown={(event) => handleNavigatorPointerDown(event, row.fiscal_year)}
                onPointerEnter={() => handleNavigatorPointerEnter(row.fiscal_year)}
                onPointerUp={() => handleNavigatorPointerUp(row.fiscal_year)}
                onPointerCancel={() => setNavigatorDrag(null)}
                onClick={() => selectChartPoint(row.fiscal_year)}
              >
                <span className="range-navigator-dot" />
                <small>{row.fiscal_year}{row.forecast_flag ? "E" : ""}</small>
              </button>
            );
          })}
        </div>
      </div>

      {returnSelection ? (
        <div className="selection-return" data-testid="selection-return">
          <Metric label="Selection" value={`${returnSelection.startYear}-${returnSelection.endYear}`} />
          <Metric label="Years" value={`${returnSelection.years}`} />
          <Metric label="Price CAGR" value={formatPercent(returnSelection.annualizedPriceReturnPct)} />
          <Metric label="Total CAGR" value={formatPercent(returnSelection.annualizedTotalReturnPct)} />
          <Metric label="Dividends" value={formatNumber(returnSelection.dividends)} />
        </div>
      ) : null}

      <div className="line-toggles">
        <Toggle label="Price" value={visibility.price} onChange={() => onToggle("price")} />
        <Toggle label="Metric area" value={visibility.metricArea} onChange={() => onToggle("metricArea")} />
        <Toggle label="Fair value" value={visibility.fairValue} onChange={() => onToggle("fairValue")} />
        <Toggle label="Normal multiple" value={visibility.normalMultiple} onChange={() => onToggle("normalMultiple")} />
        <Toggle label="Current valuation" value={visibility.currentValuation} onChange={() => onToggle("currentValuation")} />
        <Toggle label="Custom valuation" value={visibility.customValuation} onChange={() => onToggle("customValuation")} />
        <Toggle label="Dividend floor" value={visibility.dividendFloor} onChange={() => onToggle("dividendFloor")} />
        <Toggle label="Payout ratio" value={visibility.payoutRatio} onChange={() => onToggle("payoutRatio")} />
        <Toggle label="Dividend yield" value={visibility.dividendYield} onChange={() => onToggle("dividendYield")} />
        <Toggle label="Recession bands" value={visibility.recessionBands} onChange={() => onToggle("recessionBands")} />
        <Toggle label="Forecast area" value={visibility.forecast} onChange={() => onToggle("forecast")} />
        <Toggle label="Scenario lines" value={visibility.scenarioLines} onChange={() => onToggle("scenarioLines")} />
      </div>

      <section className="chart-layer-audit-strip" data-testid="chart-layer-audit-strip" aria-label="Chart layer audit strip">
        <div className="chart-layer-audit-header">
          <div>
            <span>Layer audit</span>
            <strong>Every visual layer is tied to source-traced rows or deterministic display math.</strong>
          </div>
          <em>{layerAuditRows.filter((row) => row.visible).length}/{layerAuditRows.length} visible</em>
        </div>
        <div className="chart-layer-audit-grid">
          {layerAuditRows.map((row) => (
            <article
              key={row.key}
              className={`chart-layer-audit-row ${row.visible ? "on" : "off"}`}
              data-testid={`chart-layer-audit-row-${row.key}`}
            >
              <div>
                <span>{row.label}</span>
                <strong>{row.visible ? "on" : "off"}</strong>
              </div>
              <p>{row.coverage}</p>
              {row.auditFactId ? (
                <div className="chart-layer-audit-actions">
                  <button
                    type="button"
                    data-testid={`chart-layer-audit-inspect-${row.key}`}
                    onClick={() => openAuditWorkspace(row.auditFactId)}
                  >
                    Inspect
                  </button>
                  <span title={row.auditFactName}>{row.auditFactName}</span>
                </div>
              ) : null}
              <dl>
                <div>
                  <dt>Source</dt>
                  <dd title={row.source}>{row.source}</dd>
                </div>
                <div>
                  <dt>Method</dt>
                  <dd title={row.method}>{row.method}</dd>
                </div>
                <div>
                  <dt>Quality</dt>
                  <dd title={row.quality}>{row.quality}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      </section>

      <section className="chart-audit-drawer" data-testid="chart-audit-drawer">
        <div className="chart-audit-drawer-header">
          <div>
            <span>Chart audit</span>
            <strong>
              {selectedValuationRow?.fiscal_year ?? "-"}
              {selectedValuationRow?.forecast_flag ? "E" : ""} source trace
            </strong>
          </div>
          <button type="button" data-testid="chart-audit-open-workspace" onClick={() => openAuditWorkspace()}>
            Open Data Audit
          </button>
        </div>
        <div className="chart-audit-drawer-grid">
          <div className="chart-audit-fact-list" aria-label="Selected year audit facts">
            {selectedAuditDrawerRows.map((row) => {
              const suffix = auditFactSuffix(row.fact_name);
              return (
                <button
                  key={row.fact_id}
                  type="button"
                  className={selectedAuditFact?.fact_id === row.fact_id ? "active" : ""}
                  data-testid={`chart-audit-drawer-fact-${auditTestIdPart(suffix)}`}
                  onClick={(event) => selectAuditFact(event, row.fiscal_year, suffix)}
                >
                  <span>{auditFactLabel(suffix)}</span>
                  <strong>{formatNumber(row.value)}</strong>
                  <em>{row.quality_status}</em>
                </button>
              );
            })}
          </div>
          <div className="chart-audit-selected" data-testid="chart-audit-drawer-selected">
            <span>{selectedAuditFact?.fact_name ?? `${selectedAuditScope}.source_trace`}</span>
            <strong>
              {selectedAuditFact?.method ?? traceTextOptional(selectedAuditFallbackRow, "method", "source_trace")}
              {" / confidence "}
              {selectedAuditFact?.confidence ?? traceTextOptional(selectedAuditFallbackRow, "confidence", "-")}
            </strong>
            <p>{selectedAuditEvidence.formula}</p>
            <dl className="chart-audit-evidence-strip" data-testid="chart-audit-evidence-strip">
              <div>
                <dt>Source</dt>
                <dd>{selectedAuditEvidence.source}</dd>
              </div>
              <div>
                <dt>Source doc</dt>
                <dd title={selectedAuditEvidence.sourceDocument}>{selectedAuditEvidence.sourceDocument}</dd>
              </div>
              <div>
                <dt>Filing</dt>
                <dd title={selectedAuditEvidence.filing}>{selectedAuditEvidence.filing}</dd>
              </div>
              <div>
                <dt>Available at</dt>
                <dd>{selectedAuditEvidence.availableAt}</dd>
              </div>
              <div>
                <dt>Period</dt>
                <dd>{selectedAuditEvidence.period}</dd>
              </div>
              <div>
                <dt>Unit / currency</dt>
                <dd>{selectedAuditEvidence.unit} / {selectedAuditEvidence.currency}</dd>
              </div>
            </dl>
            <em>{selectedAuditFact?.flags?.length ? selectedAuditFact.flags.join(", ") : "No quality flags for selected fact"}</em>
          </div>
        </div>
      </section>

      <table className="terminal-table">
        <thead>
          <tr>
            <th>FY</th>
            <th>EPS/metric</th>
            <th>Chg/Yr</th>
            <th>Dividend</th>
            <th>Fair value</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {valuation.map((row) => (
            <tr key={`${row.fiscal_year}-table`} onClick={() => selectChartPoint(row.fiscal_year)}>
              <td>{row.fiscal_year}{row.forecast_flag ? "E" : ""}</td>
              <td>
                <AuditCellButton row={row} factName="metric" onSelect={selectAuditFact}>
                  {formatNumber(row.metric)}
                </AuditCellButton>
              </td>
              <td>
                <AuditCellButton row={row} factName="yoy" onSelect={selectAuditFact}>
                  {row.yoy ? `${Number(row.yoy).toFixed(1)}%` : "-"}
                </AuditCellButton>
              </td>
              <td>
                <AuditCellButton row={row} factName="dividend" onSelect={selectAuditFact}>
                  {formatNumber(row.dividend)}
                </AuditCellButton>
              </td>
              <td>
                <AuditCellButton row={row} factName="fair_value_price" onSelect={selectAuditFact}>
                  {formatNumber(row.fair_value_price)}
                </AuditCellButton>
              </td>
              <td>{row.forecast_flag ? row.forecast_source : traceText(row, "source", traceText(row, "source_type", "source_trace"))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <SelectedAuditTrace
        row={selectedAuditFact}
        fallbackTrace={selectedValuationRow?.source_trace}
        factQueryString={auditQueryString}
      />
    </section>
  );
}

function AuditCellButton({
  row,
  factName,
  onSelect,
  children
}: {
  row: ValuationRow;
  factName: string;
  onSelect: (event: MouseEvent<HTMLButtonElement>, year: number, factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`audit-cell-${row.fiscal_year}-${factName}`}
      aria-label={`Audit ${row.fiscal_year} ${factName}`}
      onClick={(event) => onSelect(event, row.fiscal_year, factName)}
    >
      {children}
    </button>
  );
}

function PriceBandAuditButton({
  row,
  kind,
  onSelect,
  children
}: {
  row: ValuationRow;
  kind: "high" | "low";
  onSelect: (event: MouseEvent<HTMLButtonElement>, year: number, factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`high-low-audit-${row.fiscal_year}-${kind}`}
      aria-label={`Audit ${row.fiscal_year} price ${kind}`}
      onClick={(event) => onSelect(event, row.fiscal_year, "price")}
    >
      {children}
    </button>
  );
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

function formatPercent(raw: string | number | null | undefined) {
  const formatted = formatNumber(raw);
  return formatted === "-" ? "-" : `${formatted}%`;
}

function formatSignedPercent(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return String(raw);
  }
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatNumber(value)}%`;
}

function buildSelectedDecisionCards(
  row: ValuationRow | undefined,
  context: {
    evidenceFlags: string;
    sourceDocumentStatus: string;
  }
) {
  const price = numericOrNull(row?.price);
  const fairValue = numericOrNull(row?.fair_value_price);
  const normalMultiple = row?.normal_multiple ? `${formatNumber(row.normal_multiple)}x` : "-";
  const upsidePct = price && fairValue ? ((fairValue - price) / price) * 100 : null;
  const totalReturn = row?.total_return_cagr_pct ?? row?.price_cagr_pct ?? null;
  const marginOfSafety = row?.margin_of_safety_pct ?? null;
  const sourceLinked = context.sourceDocumentStatus === "source_document_id present";
  const pointLabel = row ? `${row.fiscal_year}${row.forecast_flag ? "E forecast" : " reported"}` : "No point selected";

  return [
    {
      key: "fair-value",
      label: "Fair value",
      value: formatNumber(row?.fair_value_price),
      detail: `orange line / ${pointLabel}`,
      tone: "fair"
    },
    {
      key: "upside",
      label: "Upside vs price",
      value: formatSignedPercent(upsidePct),
      detail: `price ${formatNumber(row?.price)}`,
      tone: upsidePct !== null && upsidePct < 0 ? "risk" : "positive"
    },
    {
      key: "normal-pe",
      label: "Normal P/E",
      value: normalMultiple,
      detail: "blue line reference",
      tone: "normal"
    },
    {
      key: "metric",
      label: "Metric",
      value: formatNumber(row?.metric),
      detail: row?.forecast_flag ? "forecast EPS/metric" : "reported EPS/metric",
      tone: "fundamental"
    },
    {
      key: "total-return",
      label: "Total CAGR",
      value: formatSignedPercent(totalReturn),
      detail: marginOfSafety ? `MoS ${formatSignedPercent(marginOfSafety)}` : "price + dividend if available",
      tone: totalReturn !== null && Number(totalReturn) < 0 ? "risk" : "positive"
    },
    {
      key: "source",
      label: "Source status",
      value: sourceLinked ? "linked" : "missing",
      detail: context.evidenceFlags,
      tone: sourceLinked ? "source" : "risk"
    }
  ];
}

function numericOrNull(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return null;
  }
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function auditFactSuffix(factName: string | undefined) {
  return String(factName ?? "metric").split(".").pop() ?? "metric";
}

function auditFactLabel(suffix: string) {
  const labels: Record<string, string> = {
    metric: "Metric",
    price: "Price",
    normal_multiple: "Normal",
    fair_value_price: "Fair value",
    yoy: "YoY",
    dividend: "Dividend"
  };
  return labels[suffix] ?? suffix.replace(/_/g, " ");
}

function auditTestIdPart(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function countNormalizationMethods(rows: AuditRow[]) {
  return rows.reduce(
    (counts, row) => {
      const method = `${row.method} ${row.source_trace?.method ?? ""}`.toUpperCase();
      if (method.includes("S1")) {
        counts.s1 += 1;
      }
      if (method.includes("S2")) {
        counts.s2 += 1;
      }
      if (method.includes("S3")) {
        counts.s3 += 1;
      }
      if (method.includes("S4") || method.includes("GAAP_FALLBACK")) {
        counts.s4 += 1;
      }
      return counts;
    },
    { s1: 0, s2: 0, s3: 0, s4: 0 }
  );
}

const valuationSourceTraceRequiredFields = [
  "source",
  "source_document_id",
  "filing_id",
  "period",
  "unit",
  "currency",
  "method",
  "formula"
] as const;

function buildValuationSourceTraceCoverage(rows: ValuationRow[]) {
  const actualRows = rows.filter((row) => !row.forecast_flag);
  const forecastRows = rows.filter((row) => row.forecast_flag);
  const completeRows = rows.filter((row) => missingValuationSourceTraceFields(row).length === 0);
  const actualComplete = actualRows.filter((row) => missingValuationSourceTraceFields(row).length === 0).length;
  const forecastComplete = forecastRows.filter((row) => missingValuationSourceTraceFields(row).length === 0).length;
  const firstMissingRow = rows.find((row) => missingValuationSourceTraceFields(row).length > 0);
  const firstMissingFields = firstMissingRow ? missingValuationSourceTraceFields(firstMissingRow) : [];
  return {
    totalRows: rows.length,
    completeRows: completeRows.length,
    actualRows: actualRows.length,
    actualComplete,
    forecastRows: forecastRows.length,
    forecastComplete,
    statusLabel: completeRows.length === rows.length ? "all rows storage-ready" : "source_trace gaps",
    firstMissingLabel: firstMissingRow
      ? `${firstMissingRow.fiscal_year}${firstMissingRow.forecast_flag ? "E" : ""}: missing ${firstMissingFields.join(", ")}`
      : "no missing storage fields"
  };
}

function buildHistoricalMapReadout({
  valuation,
  auditRows,
  forecastMeta,
  forecastYearCount,
  selectedRow,
  sourceTraceCoverage
}: {
  valuation: ValuationRow[];
  auditRows: AuditRow[];
  forecastMeta: ForecastMeta;
  forecastYearCount: number;
  selectedRow?: ValuationRow;
  sourceTraceCoverage: ReturnType<typeof buildValuationSourceTraceCoverage>;
}) {
  const actualRows = valuation.filter((row) => !row.forecast_flag);
  const forecastRows = valuation.filter((row) => row.forecast_flag);
  const firstActualYear = actualRows.at(0)?.fiscal_year;
  const lastActualYear = actualRows.at(-1)?.fiscal_year;
  const firstForecastYear = forecastRows.at(0)?.fiscal_year;
  const lastForecastYear = forecastRows.at(-1)?.fiscal_year;
  const selectedTrace = selectedRow?.source_trace ?? {};
  const selectedSource = traceRecordText(
    selectedTrace,
    "source",
    traceRecordText(selectedTrace, "source_type", selectedRow?.forecast_source ?? "source_trace")
  );

  return {
    actualCountLabel: `${actualRows.length} rows`,
    actualRangeLabel: firstActualYear && lastActualYear ? `${firstActualYear}-${lastActualYear}` : "waiting for source",
    forecastCountLabel: `${forecastRows.length}/${forecastYearCount}Y`,
    forecastRangeLabel: firstForecastYear && lastForecastYear ? `${firstForecastYear}E-${lastForecastYear}E` : forecastMeta.mode,
    sourceCoverageLabel: `${sourceTraceCoverage.completeRows}/${sourceTraceCoverage.totalRows}`,
    sourceStatusLabel: sourceTraceCoverage.statusLabel,
    selectedMethodLabel: traceRecordText(selectedTrace, "method", selectedRow?.forecast_source ?? "source_trace"),
    selectedSourceLabel: `${selectedSource} - ${auditRows.length} audit facts`
  };
}

function buildKrCacheContract(coverage: KrValuationCacheCoverage | null) {
  if (!coverage) {
    return null;
  }
  const marketMissing = coverage.missing_years.market_input;
  const metricMissing = coverage.missing_years.financial_metric;
  const qualityFlags = coverage.quality_flags.length
    ? coverage.quality_flags.join(", ")
    : coverage.full_coverage_ready
      ? "complete source-backed coverage"
      : "source-backed cache loaded";
  return {
    statusLabel: formatStatusText(coverage.coverage_status ?? coverage.cache_status ?? "source_backed_cache"),
    numbersLabel: (coverage.financial_numbers_allowed ?? coverage.valuation_ready) ? "allowed" : "blocked",
    valuationYearsLabel: formatYearList(coverage.coverage_years.valuation_points),
    missingYearsLabel: [
      marketMissing.length ? `market ${formatYearList(marketMissing)}` : "",
      metricMissing.length ? `metric ${formatYearList(metricMissing)}` : ""
    ].filter(Boolean).join(" / ") || "none",
    backendLabel: coverage.data_backend ?? "kr_valuation_input_cache",
    flagsLabel: qualityFlags
  };
}

function resolveForecastYears(forecastMeta: ForecastMeta, valuation: ValuationRow[]) {
  const explicitYears = Number(forecastMeta.years);
  if (Number.isFinite(explicitYears) && explicitYears > 0) {
    return explicitYears;
  }
  return valuation.filter((row) => row.forecast_flag).length;
}

function missingValuationSourceTraceFields(row: ValuationRow) {
  const trace = row.source_trace ?? {};
  return valuationSourceTraceRequiredFields.filter((field) => {
    const value = trace[field];
    return value === null || value === undefined || value === "";
  });
}

function summarizeEvidenceFlags(rows: AuditRow[], selectedRow: AuditRow | undefined) {
  const flags = new Set<string>();
  for (const row of rows) {
    for (const flag of row.flags ?? []) {
      flags.add(flag);
    }
    if (row.quality_status && row.quality_status !== "passed") {
      flags.add(row.quality_status);
    }
  }
  for (const flag of selectedRow?.flags ?? []) {
    flags.add(flag);
  }
  if (selectedRow?.quality_status && selectedRow.quality_status !== "passed") {
    flags.add(selectedRow.quality_status);
  }
  return flags.size ? Array.from(flags).slice(0, 4).join(", ") : "passed";
}

function formatStatusText(value: string) {
  return value.replace(/_/g, " ");
}

function buildDividendSourceEvidence(row: ValuationRow | undefined) {
  const trace = row?.source_trace ?? {};
  const metadataTrace = traceRecord(trace, "metadata");
  const dividendTrace: Record<string, unknown> =
    traceRecord(trace, "dividend_source_trace") ??
    traceRecord(metadataTrace, "dividend_source_trace") ??
    {};
  const dividendFlags = traceStringList(dividendTrace.quality_flags).concat(traceStringList(trace.quality_flags));
  const zeroAssumption =
    dividendFlags.includes("opendart_dash_no_cash_dividend_assumed_zero") ||
    /assumed_zero|dash.*zero|no_cash_dividend/i.test(
      [
        traceRecordText(dividendTrace, "method", ""),
        traceRecordText(dividendTrace, "formula", ""),
        traceRecordText(trace, "formula", "")
      ].join(" ")
    );
  const source = traceRecordText(
    dividendTrace,
    "source",
    row?.forecast_flag
      ? traceRecordText(trace, "source", row.forecast_source ?? "forecast policy")
      : traceRecordText(trace, "source", traceRecordText(trace, "source_type", "source_trace"))
  );
  const method = traceRecordText(
    dividendTrace,
    "method",
    row?.forecast_flag ? row.forecast_source ?? traceRecordText(trace, "method", "forecast_policy") : traceRecordText(trace, "method", "source_trace")
  );
  const quality = traceRecordText(
    dividendTrace,
    "quality_status",
    row?.forecast_flag
      ? "forecast"
      : traceRecordText(trace, "quality_status", traceRecordText(trace, "quality", "source_trace"))
  );
  const flags = dividendFlags.length ? Array.from(new Set(dividendFlags)).join(", ") : quality;
  return {
    value: formatNumber(row?.dividend),
    source,
    method,
    quality,
    flags,
    zeroAssumption,
    statusLabel: zeroAssumption
      ? "zero cash dividend traced from OpenDART dash disclosure"
      : row?.forecast_flag
        ? "forecast dividend follows selected scenario policy"
        : flags
  };
}

function formatYearList(years: number[]) {
  if (!years.length) {
    return "none";
  }
  if (years.length <= 4) {
    return years.join(", ");
  }
  return `${years[0]}-${years[years.length - 1]} (${years.length})`;
}

function scenarioLineLabelPosition(points: string) {
  const lastPoint = points.trim().split(/\s+/).at(-1);
  if (!lastPoint) {
    return null;
  }
  const [rawX, rawY] = lastPoint.split(",").map((value) => Number(value));
  if (!Number.isFinite(rawX) || !Number.isFinite(rawY)) {
    return null;
  }
  return {
    x: Math.min(98.5, rawX + 0.8).toFixed(2),
    y: Math.min(96, Math.max(4, rawY)).toFixed(2)
  };
}

type ChartLayerAuditRow = {
  key: string;
  label: string;
  visible: boolean;
  coverage: string;
  source: string;
  method: string;
  quality: string;
  auditFactId?: string;
  auditFactName?: string;
};

type AnnualPriceBand = {
  fiscalYear: number;
  forecastFlag: boolean;
  high: number | null;
  low: number | null;
  sourceLabel: string;
  sourceDocument: string;
  quality: string;
  valuationRow: ValuationRow;
};

function buildAnnualPriceBands(valuation: ValuationRow[], pricePoints: PricePoint[]): AnnualPriceBand[] {
  return valuation.map((row) => {
    const points = pricePoints.filter((point) => point.fiscal_year === row.fiscal_year);
    const prices = points
      .map((point) => Number(point.close_price))
      .filter((price) => Number.isFinite(price) && price > 0);
    if (!prices.length && !row.forecast_flag && Number(row.price) > 0) {
      prices.push(Number(row.price));
    }
    const trace = points.at(-1)?.source_trace ?? row.source_trace ?? {};
    const sourceDocument = traceRecordText(trace, "source_document_id", "-");
    const quality = traceRecordText(trace, "quality_status", traceRecordText(trace, "quality", "source_trace"));
    const sourceLabel = row.forecast_flag
      ? "forecast policy"
      : points.length
        ? `${points.length} price rows`
        : "valuation.price";
    return {
      fiscalYear: row.fiscal_year,
      forecastFlag: row.forecast_flag,
      high: prices.length ? Math.max(...prices) : null,
      low: prices.length ? Math.min(...prices) : null,
      sourceLabel,
      sourceDocument,
      quality,
      valuationRow: row
    };
  });
}

function buildChartLayerAuditRows({
  visibility,
  auditRows,
  valuation,
  pricePoints,
  recessionBands,
  recessionRects,
  forecastMeta,
  forecastYearCount,
  selectedAuditRows,
  scenarioLineCount,
  visibleScenarioLineCount,
  tradeOverlayCount
}: {
  visibility: LineVisibility;
  auditRows: AuditRow[];
  valuation: ValuationRow[];
  pricePoints: PricePoint[];
  recessionBands: RecessionBand[];
  recessionRects: Array<{ x: string; width: string; label: string }>;
  forecastMeta: ForecastMeta;
  forecastYearCount: number;
  selectedAuditRows: AuditRow[];
  scenarioLineCount: number;
  visibleScenarioLineCount: number;
  tradeOverlayCount: number;
}): ChartLayerAuditRow[] {
  const actualRows = valuation.filter((row) => !row.forecast_flag);
  const forecastRows = valuation.filter((row) => row.forecast_flag);
  const auditFor = (suffix: string) =>
    selectedAuditRows.find((row) => auditFactSuffix(row.fact_name) === suffix) ??
    auditRows.find((row) => auditFactSuffix(row.fact_name) === suffix) ??
    null;
  const auditCount = (suffix: string) =>
    auditRows.filter((row) => auditFactSuffix(row.fact_name) === suffix).length;
  const rowFor = (suffix: string) => layerEvidence(auditFor(suffix));
  const forecastMetricAudit =
    selectedAuditRows.find((row) => row.fact_name === "forecast.metric") ??
    auditRows.find((row) => row.fact_name === "forecast.metric") ??
    null;
  const scenarioAudit =
    auditRows.find((row) => (row.fact_name ?? "").startsWith("forecast_scenario.")) ??
    forecastMetricAudit;
  const transactionAudit = auditRows.find((row) => (row.fact_name ?? "").startsWith("portfolio_transaction.")) ?? null;
  const forecastTrace = forecastMeta.source_trace ?? {};
  const recessionTrace = recessionBands.find((band) => band.source_trace)?.source_trace ?? {};
  const priceTrace = pricePoints.at(-1)?.source_trace ?? {};

  return [
    {
      key: "price",
      label: "Price line",
      visible: visibility.price,
      coverage: pricePoints.length ? `${pricePoints.length} price points` : `${auditCount("price")} audit rows`,
      ...rowFor("price"),
      source: traceRecordText(priceTrace, "source", rowFor("price").source)
    },
    {
      key: "metric-area",
      label: "Metric area",
      visible: visibility.metricArea,
      coverage: `${actualRows.length} actual / ${forecastRows.length} forecast rows`,
      ...rowFor("metric")
    },
    {
      key: "fair-value",
      label: "Fair value",
      visible: visibility.fairValue,
      coverage: `${auditCount("fair_value_price")} fair-value audit rows`,
      ...rowFor("fair_value_price")
    },
    {
      key: "normal-multiple",
      label: "Normal multiple",
      visible: visibility.normalMultiple,
      coverage: `${auditCount("normal_multiple")} normal multiple rows`,
      ...rowFor("normal_multiple")
    },
    {
      key: "dividend",
      label: "Dividend floor",
      visible: visibility.dividendFloor,
      coverage: `${auditCount("dividend")} dividend audit rows`,
      ...rowFor("dividend")
    },
    {
      key: "forecast",
      label: "Forecast area",
      visible: visibility.forecast,
      coverage: `${forecastRows.length}/${forecastYearCount} forecast years`,
      source: traceRecordText(forecastTrace, "source", forecastMeta.source ?? "deterministic_formula"),
      method: traceRecordText(forecastTrace, "method", forecastMeta.mode),
      quality: traceRecordText(forecastTrace, "quality_status", forecastMeta.consensus?.quality_status ?? "deterministic"),
      auditFactId: forecastMetricAudit?.fact_id,
      auditFactName: forecastMetricAudit?.fact_name
    },
    {
      key: "scenario-lines",
      label: "Scenario lines",
      visible: visibility.scenarioLines,
      coverage: `${visibleScenarioLineCount}/${scenarioLineCount} visible overlays`,
      source: traceRecordText(forecastTrace, "source", forecastMeta.source ?? "deterministic_formula"),
      method: "display_overlay",
      quality: scenarioLineCount ? "deterministic" : "pending",
      auditFactId: scenarioAudit?.fact_id,
      auditFactName: scenarioAudit?.fact_name
    },
    {
      key: "recessions",
      label: "Recessions",
      visible: visibility.recessionBands,
      coverage: `${recessionRects.length}/${recessionBands.length} bands in range`,
      source: traceRecordText(recessionTrace, "source", recessionBands[0]?.source ?? "macro_series"),
      method: traceRecordText(recessionTrace, "method", "macro_overlay"),
      quality: traceRecordText(recessionTrace, "quality_status", recessionBands.length ? "source_backed" : "pending")
    },
    {
      key: "transactions",
      label: "Transactions",
      visible: tradeOverlayCount > 0,
      coverage: `${tradeOverlayCount} overlay markers`,
      source: "portfolio_transactions",
      method: "display_overlay",
      quality: tradeOverlayCount ? "source_traced" : "empty",
      auditFactId: transactionAudit?.fact_id,
      auditFactName: transactionAudit?.fact_name
    }
  ];
}

function layerEvidence(row: AuditRow | null) {
  const trace = row?.source_trace ?? {};
  return {
    source: traceRecordText(trace, "source", traceRecordText(trace, "source_type", "source_trace")),
    method: row?.method ?? traceRecordText(trace, "method", "source_trace"),
    quality: row?.quality_status ?? traceRecordText(trace, "quality_status", "pending"),
    auditFactId: row?.fact_id,
    auditFactName: row?.fact_name
  };
}

function chartSettingsFallbackEvidence(auditKey: string) {
  if (auditKey === "custom-valuation") {
    return {
      source: "user_input",
      method: "deterministic_display_formula",
      quality: "manual_policy"
    };
  }
  return {
    source: "chart_key",
    method: "deterministic_display_formula",
    quality: "source_trace_required"
  };
}

function traceText(row: ValuationRow, key: string, fallback: string) {
  const value = row.source_trace?.[key];
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function traceTextOptional(row: ValuationRow | undefined, key: string, fallback: string) {
  return row ? traceText(row, key, fallback) : fallback;
}

function traceRecordText(trace: Record<string, unknown> | undefined, key: string, fallback: string) {
  const value = trace?.[key];
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function traceRecord(value: unknown, key: string): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const nested = (value as Record<string, unknown>)[key];
  if (!nested || typeof nested !== "object" || Array.isArray(nested)) {
    return undefined;
  }
  return nested as Record<string, unknown>;
}

function traceStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => String(entry))
    .filter((entry) => entry.length > 0);
}
