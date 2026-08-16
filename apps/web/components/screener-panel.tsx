"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { Metric, NumberControl } from "./terminal-primitives";
import { auditTestIdPart } from "../lib/audit-utils";
import type { AuditRow, ScreenerRow } from "../lib/terminal-types";

type ScreenerPanelProps = {
  rows: ScreenerRow[];
  auditRows: AuditRow[];
  maxPer: number;
  minRoe: number;
  minEpsCagr: number;
  maxDebt: number;
  minMarketCap: number;
  minMarketCapUsd: number;
  relativeDiscount: number;
  requireRoeGtRoic: boolean;
  onMaxPerChange: (value: number) => void;
  onMinRoeChange: (value: number) => void;
  onMinEpsCagrChange: (value: number) => void;
  onMaxDebtChange: (value: number) => void;
  onMinMarketCapChange: (value: number) => void;
  onMinMarketCapUsdChange: (value: number) => void;
  onRelativeDiscountChange: (value: number) => void;
  onRequireRoeGtRoicChange: (value: boolean) => void;
};

export function ScreenerPanel({
  rows,
  auditRows,
  maxPer,
  minRoe,
  minEpsCagr,
  maxDebt,
  minMarketCap,
  minMarketCapUsd,
  relativeDiscount,
  requireRoeGtRoic,
  onMaxPerChange,
  onMinRoeChange,
  onMinEpsCagrChange,
  onMaxDebtChange,
  onMinMarketCapChange,
  onMinMarketCapUsdChange,
  onRelativeDiscountChange,
  onRequireRoeGtRoicChange
}: ScreenerPanelProps) {
  const passCount = rows.filter((row) => row.filters.passes_all).length;
  const filterClassCounts = screenerFilterClassCounts(rows);
  const screenerAuditRows = auditRows.filter((row) => row.fact_name?.startsWith("screener."));
  const sourceTracedRows = rows.filter((row) => Boolean(row.source_trace));
  const p1States = screenerP1States(rows, screenerAuditRows);
  const qualityStatuses = screenerQualityStatuses(rows);
  const [selectedScreenerCell, setSelectedScreenerCell] = useState(() => ({
    ticker: rows[0]?.ticker ?? "",
    factName: "per"
  }));
  const selectedScreenerRow =
    rows.find((row) => row.ticker === selectedScreenerCell.ticker) ?? rows[0];
  const selectedScreenerAuditRow = selectedScreenerRow
    ? auditRows.find(
        (row) =>
          row.fact_id.startsWith(`${selectedScreenerRow.ticker}-`) &&
          row.fact_name === `screener.${selectedScreenerCell.factName}`
      )
    : undefined;

  const selectScreenerCell = (ticker: string, factName: string) => {
    setSelectedScreenerCell({ ticker, factName });
  };

  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Screener</h1>
          <p>Custom metric-to-value, metric-to-metric, and company-relative filters over the loaded universe.</p>
        </div>
        <div className="facts-row">
          <Metric label="Passing" value={`${passCount}/${rows.length}`} />
          <Metric label="Relative discount" value={`${relativeDiscount}%`} />
        </div>
      </div>
      <div className="screener-contract-grid" data-testid="screener-p1-contract">
        <div className="screener-contract-card">
          <span>Data dependencies</span>
          <strong>universe + latest metric values + quality flags</strong>
          <small>{rows.length} rows · {screenerAuditRows.length} audit facts</small>
        </div>
        <div className="screener-contract-card">
          <span>Coverage badges</span>
          <div className="screener-p1-badges" data-testid="screener-coverage-badges">
            <em>{sourceTracedRows.length}/{rows.length} source traced</em>
            <em>{filterClassCounts.metricToValue}/{rows.length} value</em>
            <em>{filterClassCounts.metricToMetric}/{rows.length} metric</em>
            <em>{filterClassCounts.companyRelative}/{rows.length} relative</em>
          </div>
        </div>
        <div className="screener-contract-card">
          <span>Snapshot audit</span>
          <button type="button" disabled>
            Save snapshot
          </button>
          <small>screen_snapshots persistence opens only after source-backed snapshot storage is present.</small>
        </div>
        <div className="screener-contract-card">
          <span>States</span>
          <div className="screener-p1-badges" data-testid="screener-state-chips">
            {p1States.map((state) => (
              <em key={state.label} className={state.tone}>
                {state.label}
              </em>
            ))}
          </div>
        </div>
        <div className="screener-contract-card wide">
          <span>Quality filters</span>
          <strong>reported filters stay separate from estimated filters</strong>
          <small>{qualityStatuses.join(", ") || "No quality status loaded"}</small>
        </div>
      </div>
      <div className="screener-controls" aria-label="Screener filters">
        <NumberControl label="Max P/E" value={maxPer} onChange={onMaxPerChange} />
        <NumberControl label="Min ROE" value={minRoe} onChange={onMinRoeChange} suffix="%" />
        <NumberControl label="Min EPS CAGR" value={minEpsCagr} onChange={onMinEpsCagrChange} suffix="%" />
        <NumberControl label="Max Debt/Eq" value={maxDebt} onChange={onMaxDebtChange} />
        <NumberControl label="Min Market Cap" value={minMarketCap} onChange={onMinMarketCapChange} />
        <NumberControl label="Min Market Cap USD" value={minMarketCapUsd} onChange={onMinMarketCapUsdChange} />
        <NumberControl
          label="Relative discount"
          value={relativeDiscount}
          onChange={onRelativeDiscountChange}
          suffix="%"
        />
        <label className="check-control">
          <input
            type="checkbox"
            checked={requireRoeGtRoic}
            onChange={(event) => onRequireRoeGtRoicChange(event.target.checked)}
          />
          ROE &gt; ROIC
        </label>
      </div>
      <div className="source-box compact">
        <strong>Active filter contract</strong>
        <code>
          {[
            `P/E <= ${maxPer}`,
            `ROE >= ${minRoe}%`,
            `EPS CAGR >= ${minEpsCagr}%`,
            `Debt/Eq <= ${maxDebt}`,
            minMarketCap > 0 ? `Market cap >= ${minMarketCap}` : "Market cap filter disabled",
            minMarketCapUsd > 0 ? `Market cap USD >= ${minMarketCapUsd}` : "Market cap USD filter disabled",
            `P/E <= Normal P/E less ${relativeDiscount}%`,
            requireRoeGtRoic ? "ROE > ROIC enabled" : "ROE > ROIC disabled"
          ].join("\n")}
        </code>
      </div>
      <div className="screener-class-ledger" aria-label="Screener filter classes" data-testid="screener-class-ledger">
        <ScreenerClassCard
          testId="metric-to-value"
          label="Metric-to-value"
          passed={filterClassCounts.metricToValue}
          total={rows.length}
          detail={`P/E <= ${maxPer}; ROE >= ${minRoe}%; EPS CAGR >= ${minEpsCagr}%; Debt/Eq <= ${maxDebt}`}
        />
        <ScreenerClassCard
          testId="metric-to-metric"
          label="Metric-to-metric"
          passed={filterClassCounts.metricToMetric}
          total={rows.length}
          detail={requireRoeGtRoic ? "ROE must be greater than ROIC" : "ROE > ROIC disabled"}
        />
        <ScreenerClassCard
          testId="company-relative"
          label="Company-relative"
          passed={filterClassCounts.companyRelative}
          total={rows.length}
          detail={`P/E <= Normal P/E * (1 - ${relativeDiscount}% / 100)`}
        />
      </div>
      <table className="terminal-table wide" aria-label="Screener results">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Market Cap USD</th>
            <th>Market Cap</th>
            <th>Shares</th>
            <th>P/E</th>
            <th>Normal P/E</th>
            <th>Rel threshold</th>
            <th>ROE</th>
            <th>ROIC</th>
            <th>EPS CAGR</th>
            <th>Debt/Eq</th>
            <th>Value</th>
            <th>Metric</th>
            <th>Relative</th>
            <th>All</th>
            <th>Reason</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.ticker}>
              <td>{row.ticker}</td>
              <td>
                <ScreenerAuditCellButton row={row} factName="market_cap_usd" onSelect={selectScreenerCell}>
                  {formatMarketCap(row.market_cap_usd, "USD")}
                </ScreenerAuditCellButton>
              </td>
              <td>
                <ScreenerAuditCellButton row={row} factName="market_cap" onSelect={selectScreenerCell}>
                  {formatMarketCap(row.market_cap, row.currency)}
                </ScreenerAuditCellButton>
              </td>
              <td>
                <ScreenerAuditCellButton row={row} factName="listed_shares" onSelect={selectScreenerCell}>
                  {formatNumber(row.listed_shares)}
                </ScreenerAuditCellButton>
              </td>
              <td>
                <ScreenerAuditCellButton row={row} factName="per" onSelect={selectScreenerCell}>
                  {row.per}
                </ScreenerAuditCellButton>
              </td>
              <td>
                <ScreenerAuditCellButton row={row} factName="normal_pe" onSelect={selectScreenerCell}>
                  {row.normal_pe}
                </ScreenerAuditCellButton>
              </td>
              <td>{screenerRelativeThreshold(row, relativeDiscount)}</td>
              <td>
                <ScreenerAuditCellButton row={row} factName="roe" onSelect={selectScreenerCell}>
                  {row.roe}%
                </ScreenerAuditCellButton>
              </td>
              <td>
                <ScreenerAuditCellButton row={row} factName="roic" onSelect={selectScreenerCell}>
                  {row.roic}%
                </ScreenerAuditCellButton>
              </td>
              <td>
                <ScreenerAuditCellButton row={row} factName="eps_cagr" onSelect={selectScreenerCell}>
                  {row.eps_cagr}%
                </ScreenerAuditCellButton>
              </td>
              <td>
                <ScreenerAuditCellButton row={row} factName="debt_to_equity" onSelect={selectScreenerCell}>
                  {row.debt_to_equity}
                </ScreenerAuditCellButton>
              </td>
              <td>
                <ScreenerFilterBadge
                  ticker={row.ticker}
                  filterKey="metric-to-value"
                  passed={row.filters.metric_to_value}
                  onSelect={selectScreenerCell}
                />
              </td>
              <td>
                <ScreenerFilterBadge
                  ticker={row.ticker}
                  filterKey="metric-to-metric"
                  passed={row.filters.metric_to_metric}
                  onSelect={selectScreenerCell}
                />
              </td>
              <td>
                <ScreenerFilterBadge
                  ticker={row.ticker}
                  filterKey="company-relative"
                  passed={row.filters.company_relative}
                  onSelect={selectScreenerCell}
                />
              </td>
              <td>
                <ScreenerFilterBadge ticker={row.ticker} filterKey="all" passed={Boolean(row.filters.passes_all)} onSelect={selectScreenerCell} />
              </td>
              <td>{(row.filter_reasons ?? []).join("; ") || "-"}</td>
              <td>
                <span className={`source-state-badge ${row.source_trace ? "ok" : "blocked"}`}>
                  {row.source_trace ? "Source traced" : "No source_trace"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <SelectedAuditTrace
        row={selectedScreenerAuditRow}
        fallbackTrace={selectedScreenerRow?.source_trace}
        fallbackLabel={`screener.${selectedScreenerCell.factName}`}
      />
    </section>
  );
}

function ScreenerClassCard({
  testId,
  label,
  passed,
  total,
  detail
}: {
  testId: string;
  label: string;
  passed: number;
  total: number;
  detail: string;
}) {
  return (
    <div className="screener-class-card" data-testid={`screener-class-${testId}`}>
      <span>{label}</span>
      <strong>
        {passed}/{total}
      </strong>
      <em>{detail}</em>
    </div>
  );
}

function ScreenerFilterBadge({
  ticker,
  filterKey,
  passed,
  onSelect
}: {
  ticker: string;
  filterKey: string;
  passed: boolean;
  onSelect: (ticker: string, factName: string) => void;
}) {
  return (
    <button
      className={`filter-badge ${passed ? "pass" : "watch"}`}
      type="button"
      data-testid={`screener-filter-${ticker}-${filterKey}`}
      aria-label={`Audit screener ${ticker} ${filterKey} filter`}
      onClick={() => onSelect(ticker, screenerFilterAuditFact(filterKey))}
    >
      {passed ? "Pass" : "Watch"}
    </button>
  );
}

function ScreenerAuditCellButton({
  row,
  factName,
  onSelect,
  children
}: {
  row: ScreenerRow;
  factName: string;
  onSelect: (ticker: string, factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`screener-audit-cell-${row.ticker}-${auditTestIdPart(factName)}`}
      aria-label={`Audit screener ${row.ticker} ${factName}`}
      onClick={() => onSelect(row.ticker, factName)}
    >
      {children}
    </button>
  );
}

function screenerFilterClassCounts(rows: ScreenerRow[]) {
  return {
    metricToValue: rows.filter((row) => row.filters.metric_to_value).length,
    metricToMetric: rows.filter((row) => row.filters.metric_to_metric).length,
    companyRelative: rows.filter((row) => row.filters.company_relative).length
  };
}

function screenerFilterAuditFact(filterKey: string) {
  if (filterKey === "metric-to-metric") {
    return "roe";
  }
  if (filterKey === "company-relative") {
    return "normal_pe";
  }
  return "per";
}

function screenerP1States(rows: ScreenerRow[], auditRows: AuditRow[]) {
  const flags = rows.flatMap((row) => [
    ...(row.filter_reasons ?? []),
    ...(Array.isArray(row.source_trace?.flags) ? row.source_trace.flags.map(String) : []),
    ...(Array.isArray(row.source_trace?.quality_flags) ? row.source_trace.quality_flags.map(String) : [])
  ]).map((flag) => flag.toLowerCase());
  const sourceTracedRows = rows.filter((row) => Boolean(row.source_trace)).length;
  const staleSource = flags.some((flag) => flag.includes("stale"));
  const partialCoverage = sourceTracedRows < rows.length;

  return [
    {
      label: rows.length ? "universe loaded" : "empty universe",
      tone: rows.length ? "ok" : "warning"
    },
    {
      label: staleSource ? "stale source" : "source current",
      tone: staleSource ? "warning" : "ok"
    },
    {
      label: partialCoverage ? "partial coverage" : "full coverage",
      tone: partialCoverage ? "warning" : "ok"
    },
    {
      label: sourceTracedRows === rows.length && auditRows.length ? "no source rejected" : "source_trace required",
      tone: sourceTracedRows === rows.length && auditRows.length ? "ok" : "danger"
    }
  ];
}

function screenerQualityStatuses(rows: ScreenerRow[]) {
  return Array.from(
    new Set(
      rows
        .map((row) => row.source_trace?.quality_status ?? row.source_trace?.source_type)
        .filter((value): value is string => typeof value === "string" && value.length > 0)
    )
  );
}

function screenerRelativeThreshold(row: ScreenerRow, relativeDiscount: number) {
  const normalPe = toNumberOrNull(row.normal_pe);
  if (normalPe === null) {
    return "-";
  }
  return (normalPe * (1 - relativeDiscount / 100)).toFixed(2);
}

function toNumberOrNull(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return null;
  }
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
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
