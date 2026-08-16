"use client";

import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { KrSourceReadinessCard } from "./kr-source-readiness-card";
import { Metric } from "./terminal-primitives";
import { publicTraceSummary } from "../lib/audit-utils";
import type {
  AuditRow,
  KrValuationCacheCoverage,
  KrValuationCacheUniverseCoverage,
  PerformanceRow,
  PerformanceSummary
} from "../lib/terminal-types";

type PerformancePanelProps = {
  ticker?: string | null;
  performance: PerformanceSummary | null;
  auditRows: AuditRow[];
  krCacheCoverage?: KrValuationCacheCoverage | null;
  krCacheUniverse?: KrValuationCacheUniverseCoverage | null;
};

const performanceRanges = ["MAX", "18Y", "14Y", "10Y", "5Y", "1Y"] as const;

export function PerformancePanel({ ticker, performance, auditRows, krCacheCoverage, krCacheUniverse }: PerformancePanelProps) {
  if (!performance) {
    return (
      <section className="single-panel">
        <div className="panel-header">
          <div>
            <h1>Performance</h1>
            <p>Performance data is unavailable for this request.</p>
          </div>
        </div>
        <div className="performance-contract-grid" data-testid="performance-p1-contract">
          <KrSourceReadinessCard
            title="Performance KR source gate"
            testIdPrefix="performance-kr"
            ticker={ticker}
            krCacheCoverage={krCacheCoverage}
            krCacheUniverse={krCacheUniverse}
          />
        </div>
      </section>
    );
  }
  return (
    <PerformancePanelContent
      performance={performance}
      auditRows={auditRows}
      ticker={ticker}
      krCacheCoverage={krCacheCoverage}
      krCacheUniverse={krCacheUniverse}
    />
  );
}

function PerformancePanelContent({
  performance,
  auditRows,
  ticker,
  krCacheCoverage,
  krCacheUniverse
}: {
  performance: PerformanceSummary;
  auditRows: AuditRow[];
  ticker?: string | null;
  krCacheCoverage?: KrValuationCacheCoverage | null;
  krCacheUniverse?: KrValuationCacheUniverseCoverage | null;
}) {
  const latestRow = performance.rows.at(-1);
  const [selectedRange, setSelectedRange] = useState<(typeof performanceRanges)[number]>("MAX");
  const [reinvestDividends, setReinvestDividends] = useState(false);
  const [selectedFact, setSelectedFact] = useState<{ startYear?: number; endYear?: number; factName: string }>({
    factName: "total_return_pct"
  });
  const selectedStartYear = selectedFact.startYear ?? latestRow?.start_year ?? 0;
  const selectedEndYear = selectedFact.endYear ?? latestRow?.end_year ?? 0;
  const selectedPerformanceRow =
    performance.rows.find((row) => row.start_year === selectedStartYear && row.end_year === selectedEndYear) ??
    latestRow;
  const selectedAuditRow = auditRows.find(
    (row) =>
      row.fiscal_year === selectedEndYear &&
      row.fact_name === `performance.${selectedFact.factName}.${selectedStartYear}`
  );
  const filteredRows = useMemo(
    () => filterPerformanceRows(performance.rows, selectedRange),
    [performance.rows, selectedRange]
  );
  const visibleRows = filteredRows.length ? filteredRows : performance.rows;
  const endingSharesFact = reinvestDividends ? "reinvested_shares" : "shares_purchased";
  const endingValueFact = reinvestDividends ? "reinvested_ending_value" : "ending_value";
  const dividendsFact = reinvestDividends ? "reinvested_dividends" : "dividends_received";
  const totalGainFact = reinvestDividends ? "reinvested_total_gain" : "total_gain";
  const totalReturnFact = reinvestDividends ? "reinvested_total_return_pct" : "total_return_pct";
  const totalCagrFact = reinvestDividends ? "reinvested_annualized_total_return_pct" : "annualized_total_return_pct";
  const performanceAuditRows = auditRows.filter((row) => row.fact_name?.startsWith("performance."));
  const p1States = performanceP1States(performance, performanceAuditRows);
  const sourceTraceCoverage = buildPerformanceSourceTraceCoverage(performance, performanceAuditRows);
  const selectedSpanLabel = selectedPerformanceRow
    ? `${selectedPerformanceRow.start_year}-${selectedPerformanceRow.end_year}`
    : "-";
  const selectedTotalReturn = formatPercent(rowValue(selectedPerformanceRow, totalReturnFact));
  const selectedAnnualizedReturn = formatPercent(rowValue(selectedPerformanceRow, totalCagrFact));
  const selectedDividendReturn = formatPercent(selectedPerformanceRow?.dividend_return_pct);
  const selectedQuality = selectedPerformanceRow?.quality_status ?? performance.quality_status;
  const selectedTrace = selectedPerformanceRow?.source_trace ?? performance.source_trace ?? {};
  const selectedFlags = formatFlagList([
    ...(selectedPerformanceRow?.flags ?? []),
    ...performance.flags,
    ...(Array.isArray(selectedTrace.flags) ? selectedTrace.flags.map(String) : []),
    ...(Array.isArray(selectedTrace.quality_flags) ? selectedTrace.quality_flags.map(String) : [])
  ]);
  const selectedFormula = traceRecordText(
    selectedAuditRow?.source_trace ?? selectedTrace,
    "formula",
    selectedAuditRow?.formula ?? (reinvestDividends ? "price_return + reinvested_dividends" : "price_return + cash_dividends")
  );
  const decisionCards = [
    { key: "total-return", label: "Total return", value: selectedTotalReturn, detail: selectedSpanLabel },
    { key: "annualized", label: "Annualized", value: selectedAnnualizedReturn, detail: reinvestDividends ? "reinvested" : "cash dividends" },
    { key: "audit-coverage", label: "Audit coverage", value: `${sourceTraceCoverage.complete}/${sourceTraceCoverage.total}`, detail: sourceTraceCoverage.statusLabel },
    { key: "benchmark", label: "Benchmark", value: "pending", detail: "source_trace required" }
  ];
  const decisionAuditItems = [
    {
      key: "method",
      label: "Method",
      value: selectedAuditRow?.method ?? traceRecordText(selectedTrace, "method", "performance_source_trace")
    },
    {
      key: "source",
      label: "Source",
      value: traceRecordText(selectedTrace, "source", traceRecordText(selectedTrace, "source_type", "source_trace"))
    },
    { key: "formula", label: "Formula", value: selectedFormula },
    { key: "flags", label: "Flags", value: selectedFlags || sourceTraceCoverage.firstMissingLabel }
  ];

  function selectPerformanceFact(row: PerformanceRow, factName: string) {
    setSelectedFact({ startYear: row.start_year, endYear: row.end_year, factName });
  }

  return (
    <section className="single-panel performance-panel">
      <div className="panel-header">
        <div>
          <h1>Performance</h1>
          <p>Source-traced investment return table using historical price and dividend rows.</p>
        </div>
        <div className="facts-row">
          <Metric label="Initial" value={`${formatNumber(performance.initial_investment)} ${performance.currency}`} />
          <Metric label="Quality" value={performance.quality_status} />
          <Metric label="Best start" value={String(performance.summary.best_start_year ?? "-")} />
          <Metric label="Best CAGR" value={formatPercent(performance.summary.best_annualized_total_return_pct as string | number | null)} />
        </div>
      </div>

      <div className="performance-contract-grid" data-testid="performance-p1-contract">
        <div className="performance-contract-card">
          <span>Data scope</span>
          <strong>price_bars + dividends + metric series</strong>
          <small>{performance.rows.length} return rows · {performanceAuditRows.length} audit facts</small>
        </div>
        <label className="performance-contract-card" htmlFor="performance-benchmark-select">
          <span>Benchmark</span>
          <select id="performance-benchmark-select" aria-label="Performance benchmark" disabled>
            <option>No source-backed benchmark loaded</option>
          </select>
          <small>Benchmark series is rejected until source_trace is present.</small>
        </label>
        <div className="performance-contract-card">
          <span>Audit targets</span>
          <div className="performance-audit-targets" data-testid="performance-audit-targets">
            <PerformanceAuditTargetButton
              disabled={!selectedPerformanceRow}
              label="Return card"
              onClick={() => selectedPerformanceRow && selectPerformanceFact(selectedPerformanceRow, totalReturnFact)}
            />
            <PerformanceAuditTargetButton
              disabled={!selectedPerformanceRow}
              label="Dividend row"
              onClick={() => selectedPerformanceRow && selectPerformanceFact(selectedPerformanceRow, dividendsFact)}
            />
            <PerformanceAuditTargetButton
              disabled={!selectedPerformanceRow}
              label="Valuation point"
              onClick={() => selectedPerformanceRow && selectPerformanceFact(selectedPerformanceRow, "price_return_pct")}
            />
          </div>
        </div>
        <div className="performance-contract-card">
          <span>States</span>
          <div className="performance-state-chips" data-testid="performance-state-chips">
            {p1States.map((state) => (
              <span key={state.label} className={`performance-state-chip ${state.tone}`}>
                {state.label}
              </span>
            ))}
          </div>
        </div>
        <KrSourceReadinessCard
          title="Performance KR source gate"
          testIdPrefix="performance-kr"
          ticker={ticker}
          krCacheCoverage={krCacheCoverage}
          krCacheUniverse={krCacheUniverse}
        />
      </div>

      <div className="performance-range-row" aria-label="Performance period controls">
        {performanceRanges.map((range) => (
          <button
            key={range}
            type="button"
            className={selectedRange === range ? "active" : ""}
            onClick={() => setSelectedRange(range)}
          >
            {range}
          </button>
        ))}
        <button
          type="button"
          className={`performance-reinvest-toggle ${reinvestDividends ? "active" : ""}`}
          aria-pressed={reinvestDividends}
          onClick={() => setReinvestDividends((value) => !value)}
        >
          Reinvest Dividends
        </button>
      </div>

      <div className="performance-comparison-card">
        <div className="panel-header compact">
          <div>
            <h2>Valuation Comparison</h2>
            <p>
              {reinvestDividends
                ? "Price return and dividend-reinvestment total return from source-backed rows."
                : "Price return, cash dividend total return, and source-backed return facts."}
            </p>
          </div>
          <Metric label="Return mode" value={reinvestDividends ? "Reinvested" : "Cash dividends"} />
        </div>
        <div className="performance-workbench-grid">
          <PerformanceComparisonChart rows={visibleRows} reinvestDividends={reinvestDividends} />
          <aside
            className="performance-return-rail"
            data-testid="performance-decision-rail"
            aria-label="Performance decision rail"
          >
            <div className="performance-return-rail-header">
              <div>
                <span>Performance Decision Rail</span>
                <em>Selected return window</em>
              </div>
              <strong>{selectedSpanLabel}</strong>
            </div>
            <div className="performance-decision-strip" data-testid="performance-decision-strip">
              {decisionCards.map((card) => (
                <div key={card.key} data-testid={`performance-decision-card-${card.key}`}>
                  <span>{card.label}</span>
                  <strong>{card.value}</strong>
                  <small>{card.detail}</small>
                </div>
              ))}
            </div>
            <dl className="performance-decision-audit-grid">
              {decisionAuditItems.map((item) => (
                <div key={item.key} data-testid={`performance-decision-audit-${item.key}`}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
              <div data-testid="performance-decision-audit-dividend">
                <dt>Dividend return</dt>
                <dd>{selectedDividendReturn}</dd>
              </div>
              <div data-testid="performance-decision-audit-quality">
                <dt>Quality</dt>
                <dd>{selectedQuality}</dd>
              </div>
            </dl>
            <div className="performance-formula-card">
              <span>Formula lineage</span>
              <code>{selectedFormula}</code>
            </div>
            <button
              type="button"
              className="performance-decision-open-audit"
              data-testid="performance-decision-open-audit"
              disabled={!selectedPerformanceRow}
              onClick={() => selectedPerformanceRow && selectPerformanceFact(selectedPerformanceRow, totalReturnFact)}
            >
              Inspect selected return
            </button>
          </aside>
        </div>
        <div className="performance-card-row">
          <PerformanceMetricCard label="Beginning price" row={selectedPerformanceRow} factName="start_price" onSelect={selectPerformanceFact}>
            {formatNumber(selectedPerformanceRow?.start_price)}
          </PerformanceMetricCard>
          <PerformanceMetricCard label="Ending price" row={selectedPerformanceRow} factName="end_price" onSelect={selectPerformanceFact}>
            {formatNumber(selectedPerformanceRow?.end_price)}
          </PerformanceMetricCard>
          <PerformanceMetricCard label="Invest amount" row={selectedPerformanceRow} factName="initial_investment" onSelect={selectPerformanceFact}>
            {formatNumber(selectedPerformanceRow?.initial_investment)}
          </PerformanceMetricCard>
          <PerformanceMetricCard label="Beginning shares" row={selectedPerformanceRow} factName="shares_purchased" onSelect={selectPerformanceFact}>
            {formatNumber(selectedPerformanceRow?.shares_purchased)}
          </PerformanceMetricCard>
          <PerformanceMetricCard label="Ending shares" row={selectedPerformanceRow} factName={endingSharesFact} onSelect={selectPerformanceFact}>
            {formatNumber(rowValue(selectedPerformanceRow, endingSharesFact))}
          </PerformanceMetricCard>
        </div>
      </div>

      <div className="performance-table-shell">
        <table className="terminal-table wide" aria-label="Performance return table">
          <thead>
            <tr>
              <th>Start</th>
              <th>End</th>
              <th>Years</th>
              <th>Start price</th>
              <th>End price</th>
              <th>End value</th>
              <th>{reinvestDividends ? "Reinvested divs" : "Cash dividends"}</th>
              <th>Total gain</th>
              <th>Total return</th>
              <th>Total CAGR</th>
              <th>Quality</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={`${row.start_year}-${row.end_year}`}>
                <td>{row.start_year}</td>
                <td>{row.end_year}</td>
                <td>{row.years}</td>
                <td>
                  <PerformanceAuditCellButton row={row} factName="start_price" onSelect={selectPerformanceFact}>
                    {formatNumber(row.start_price)}
                  </PerformanceAuditCellButton>
                </td>
                <td>
                  <PerformanceAuditCellButton row={row} factName="end_price" onSelect={selectPerformanceFact}>
                    {formatNumber(row.end_price)}
                  </PerformanceAuditCellButton>
                </td>
                <td>
                  <PerformanceAuditCellButton row={row} factName={endingValueFact} onSelect={selectPerformanceFact}>
                    {formatNumber(rowValue(row, endingValueFact))}
                  </PerformanceAuditCellButton>
                </td>
                <td>
                  <PerformanceAuditCellButton row={row} factName={dividendsFact} onSelect={selectPerformanceFact}>
                    {formatNumber(rowValue(row, dividendsFact))}
                  </PerformanceAuditCellButton>
                </td>
                <td>
                  <PerformanceAuditCellButton row={row} factName={totalGainFact} onSelect={selectPerformanceFact}>
                    {formatNumber(rowValue(row, totalGainFact))}
                  </PerformanceAuditCellButton>
                </td>
                <td>
                  <PerformanceAuditCellButton row={row} factName={totalReturnFact} onSelect={selectPerformanceFact}>
                    {formatPercent(rowValue(row, totalReturnFact))}
                  </PerformanceAuditCellButton>
                </td>
                <td>
                  <PerformanceAuditCellButton row={row} factName={totalCagrFact} onSelect={selectPerformanceFact}>
                    {formatPercent(rowValue(row, totalCagrFact))}
                  </PerformanceAuditCellButton>
                </td>
                <td>{row.quality_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="performance-dividend-card">
        <div className="panel-header compact">
          <div>
            <h2>Dividend Cash Flow</h2>
            <p>
              {reinvestDividends
                ? "Dividend cash is recursively reinvested at each fiscal year-end price."
                : "Dividend cash received and yield contribution from the same source-traced return rows."}
            </p>
          </div>
        </div>
        <div className="performance-table-shell compact">
          <table className="terminal-table wide" aria-label="Dividend cash flow table">
            <thead>
              <tr>
                <th>Record span</th>
                <th>Shares</th>
                <th>Dividends</th>
                <th>Dividend return</th>
                <th>Capital gain</th>
                <th>Yield on cost proxy</th>
                <th>Trace</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => (
                <tr key={`dividend-${row.start_year}-${row.end_year}`}>
                  <td>{row.start_year}-{row.end_year}</td>
                  <td>{formatNumber(rowValue(row, endingSharesFact))}</td>
                  <td>
                    <PerformanceAuditCellButton row={row} factName={dividendsFact} onSelect={selectPerformanceFact}>
                      {formatNumber(rowValue(row, dividendsFact))}
                    </PerformanceAuditCellButton>
                  </td>
                  <td>{formatPercent(row.dividend_return_pct)}</td>
                  <td>{formatNumber(row.capital_gain)}</td>
                  <td>{formatPercent(row.dividend_return_pct)}</td>
                  <td>{row.quality_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <SelectedAuditTrace
        row={selectedAuditRow}
        fallbackTrace={selectedPerformanceRow?.source_trace ?? performance.source_trace}
        fallbackLabel="performance source_trace"
      />
      <div className="source-box">
        <strong>Performance trace</strong>
        <code>{JSON.stringify(publicTraceSummary(performance.source_trace), null, 2)}</code>
      </div>
    </section>
  );
}

function PerformanceAuditTargetButton({
  disabled,
  label,
  onClick
}: {
  disabled: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button type="button" disabled={disabled} onClick={onClick}>
      {label}
    </button>
  );
}

function PerformanceComparisonChart({
  rows,
  reinvestDividends
}: {
  rows: PerformanceRow[];
  reinvestDividends: boolean;
}) {
  const safeRows = rows.length ? rows : [];
  const values = safeRows.flatMap((row) => [
    Number(row.price_return_pct),
    Number(returnModeValue(row, reinvestDividends, "total_return_pct")),
    Number(returnModeValue(row, reinvestDividends, "annualized_total_return_pct"))
  ]).filter(Number.isFinite);
  const maxValue = Math.max(1, ...values.map((value) => Math.abs(value)));
  const points = safeRows.map((row, index) => ({
    row,
    x: safeRows.length <= 1 ? 8 : 8 + (index / (safeRows.length - 1)) * 84,
    priceY: 88 - normalizeChartValue(Number(row.price_return_pct), maxValue),
    totalY: 88 - normalizeChartValue(Number(returnModeValue(row, reinvestDividends, "total_return_pct")), maxValue),
    cagrY: 88 - normalizeChartValue(Number(returnModeValue(row, reinvestDividends, "annualized_total_return_pct")), maxValue)
  }));
  const pricePoints = points.map((point) => `${point.x},${point.priceY}`).join(" ");
  const totalPoints = points.map((point) => `${point.x},${point.totalY}`).join(" ");
  const cagrPoints = points.map((point) => `${point.x},${point.cagrY}`).join(" ");

  return (
    <div className="performance-chart" data-testid="performance-comparison-chart">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Performance comparison chart">
        {[15, 30, 45, 60, 75, 90].map((y) => (
          <line key={`h-${y}`} x1="5" x2="96" y1={y} y2={y} className="performance-grid-line" />
        ))}
        {[8, 20, 32, 44, 56, 68, 80, 92].map((x) => (
          <line key={`v-${x}`} x1={x} x2={x} y1="10" y2="90" className="performance-grid-line" />
        ))}
        <rect x="5" y="70" width="91" height="18" className="performance-fundamental-area" />
        <polyline points={cagrPoints} className="performance-line fair" />
        <polyline points={totalPoints} className="performance-line normal" />
        <polyline points={pricePoints} className="performance-line price" />
        {points.map((point) => (
          <circle key={`${point.row.start_year}-${point.row.end_year}`} cx={point.x} cy={point.totalY} r="1.4" className="performance-point" />
        ))}
      </svg>
      <div className="performance-chart-axis">Return %</div>
      <div className="performance-chart-legend">
        <span><i className="price" />Price return</span>
        <span><i className="normal" />{reinvestDividends ? "Reinvested total" : "Total return"}</span>
        <span><i className="fair" />Annualized CAGR</span>
      </div>
    </div>
  );
}

function PerformanceMetricCard({
  label,
  row,
  factName,
  onSelect,
  children
}: {
  label: string;
  row: PerformanceRow | undefined;
  factName: string;
  onSelect: (row: PerformanceRow, factName: string) => void;
  children: ReactNode;
}) {
  if (!row) {
    return (
      <div className="performance-metric-card">
        <span>{label}</span>
        <strong>-</strong>
      </div>
    );
  }
  return (
    <button className="performance-metric-card" type="button" onClick={() => onSelect(row, factName)}>
      <span>{label}</span>
      <strong>{children}</strong>
    </button>
  );
}

function PerformanceAuditCellButton({
  row,
  factName,
  onSelect,
  children
}: {
  row: PerformanceRow;
  factName: string;
  onSelect: (row: PerformanceRow, factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`performance-audit-cell-${row.start_year}-${row.end_year}-${factName}`}
      aria-label={`Audit performance ${row.start_year} to ${row.end_year} ${factName}`}
      onClick={() => onSelect(row, factName)}
    >
      {children}
    </button>
  );
}

function rowValue(row: PerformanceRow | undefined, factName: string) {
  if (!row) {
    return "";
  }
  return String(row[factName as keyof PerformanceRow] ?? "");
}

function traceRecordText(trace: Record<string, unknown> | undefined, key: string, fallback: string) {
  const value = trace?.[key];
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function returnModeValue(
  row: PerformanceRow,
  reinvestDividends: boolean,
  factName: "total_return_pct" | "annualized_total_return_pct",
) {
  if (!reinvestDividends) {
    return rowValue(row, factName);
  }
  return rowValue(
    row,
    factName === "total_return_pct"
      ? "reinvested_total_return_pct"
      : "reinvested_annualized_total_return_pct",
  );
}

function filterPerformanceRows(rows: PerformanceRow[], selectedRange: (typeof performanceRanges)[number]) {
  if (selectedRange === "MAX") {
    return rows;
  }
  const years = Number(selectedRange.replace("Y", ""));
  if (!Number.isFinite(years)) {
    return rows;
  }
  return rows.filter((row) => row.years <= years);
}

function performanceP1States(performance: PerformanceSummary, auditRows: AuditRow[]) {
  const flags = [
    ...performance.flags,
    ...performance.rows.flatMap((row) => row.flags ?? [])
  ].map((flag) => flag.toLowerCase());
  const hasDividendRows = performance.rows.some((row) => Number(row.dividends_received) > 0 || Number(row.reinvested_dividends) > 0);
  const rowsHaveSourceTrace = performance.rows.length > 0 && performance.rows.every((row) => Boolean(row.source_trace));
  const hasPerformanceTrace = Boolean(performance.source_trace) && auditRows.length > 0 && rowsHaveSourceTrace;
  const stalePrice = flags.some((flag) => flag.includes("stale") && flag.includes("price"));

  return [
    {
      label: "missing benchmark",
      tone: "warning"
    },
    {
      label: hasDividendRows ? "dividend loaded" : "no dividend",
      tone: hasDividendRows ? "ok" : "warning"
    },
    {
      label: stalePrice ? "stale price" : "price trace current",
      tone: stalePrice ? "warning" : "ok"
    },
    {
      label: hasPerformanceTrace ? "no source rejected" : "source_trace required",
      tone: hasPerformanceTrace ? "ok" : "danger"
    }
  ];
}

const performanceSourceTraceRequiredFields = [
  "source",
  "source_document_id",
  "filing_id",
  "period",
  "unit",
  "currency",
  "method",
  "formula"
] as const;

function buildPerformanceSourceTraceCoverage(performance: PerformanceSummary, auditRows: AuditRow[]) {
  const rowComplete = performance.rows.filter((row) => missingPerformanceTraceFields(row.source_trace).length === 0).length;
  const auditComplete = auditRows.filter((row) => missingPerformanceTraceFields(row.source_trace).length === 0).length;
  const metaMissing = missingPerformanceTraceFields(performance.source_trace);
  const total = performance.rows.length + auditRows.length + 1;
  const complete = rowComplete + auditComplete + (metaMissing.length === 0 ? 1 : 0);
  const firstRowGap = performance.rows.find((row) => missingPerformanceTraceFields(row.source_trace).length > 0);
  const firstAuditGap = auditRows.find((row) => missingPerformanceTraceFields(row.source_trace).length > 0);

  return {
    complete,
    total,
    statusLabel: complete === total ? "all rows storage-ready" : "performance source gaps",
    firstMissingLabel: performanceMissingLabel(firstRowGap, firstAuditGap, metaMissing)
  };
}

function performanceMissingLabel(
  firstRowGap: PerformanceRow | undefined,
  firstAuditGap: AuditRow | undefined,
  metaMissing: string[]
) {
  if (firstRowGap) {
    return `${firstRowGap.start_year}-${firstRowGap.end_year} missing ${missingPerformanceTraceFields(firstRowGap.source_trace).join(", ")}`;
  }
  if (firstAuditGap) {
    return `${firstAuditGap.fact_name ?? firstAuditGap.fact_id} missing ${missingPerformanceTraceFields(firstAuditGap.source_trace).join(", ")}`;
  }
  if (metaMissing.length) {
    return `performance summary missing ${metaMissing.join(", ")}`;
  }
  return "no missing performance storage fields";
}

function missingPerformanceTraceFields(trace: Record<string, unknown> | undefined) {
  return performanceSourceTraceRequiredFields.filter((field) => {
    const value = trace?.[field];
    return value === null || value === undefined || value === "";
  });
}

function formatFlagList(flags: string[]) {
  const normalized = Array.from(new Set(flags.map((flag) => flag.trim()).filter(Boolean)));
  return normalized.length ? normalized.slice(0, 4).join(", ") : "";
}

function normalizeChartValue(value: number, maxValue: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  const normalized = 20 + (value / maxValue) * 58;
  return Math.min(78, Math.max(0, normalized));
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
