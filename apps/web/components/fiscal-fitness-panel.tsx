"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { Metric } from "./terminal-primitives";
import type { AuditRow, FiscalFitnessRow } from "../lib/terminal-types";

type FiscalFitnessPanelProps = {
  rows: FiscalFitnessRow[];
  auditRows: AuditRow[];
};

const keyMetrics = ["roe_pct", "roic_pct", "fcf_margin_pct", "debt_to_equity"];

export function FiscalFitnessPanel({ rows, auditRows }: FiscalFitnessPanelProps) {
  const latestYear = rows.length ? Math.max(...rows.map((row) => row.fiscal_year)) : 0;
  const latestRows = rows.filter((row) => row.fiscal_year === latestYear);
  const [selectedFact, setSelectedFact] = useState<{ fiscalYear?: number; metricKey: string }>({
    metricKey: "roe_pct"
  });
  const selectedYear = selectedFact.fiscalYear ?? latestYear;
  const selectedFitnessRow =
    rows.find((row) => row.fiscal_year === selectedYear && row.metric_key === selectedFact.metricKey) ??
    latestRows.find((row) => row.metric_key === selectedFact.metricKey) ??
    latestRows[0];
  const selectedAuditRow = auditRows.find(
    (row) => row.fiscal_year === selectedYear && row.fact_name === `fiscal_fitness.${selectedFact.metricKey}`
  );
  const flags = Array.from(new Set(latestRows.flatMap((row) => row.flags ?? []))).sort();

  function selectFiscalFitnessFact(fiscalYear: number, metricKey: string) {
    setSelectedFact({ fiscalYear, metricKey });
  }

  if (!rows.length) {
    return (
      <section className="single-panel">
        <div className="panel-header">
          <div>
            <h1>Fiscal Fitness</h1>
            <p>Fiscal Fitness rows are unavailable for this request.</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Fiscal Fitness</h1>
          <p>Source-traced profitability, cash generation, growth, solvency, and liquidity checks.</p>
        </div>
        <div className="facts-row">
          {keyMetrics.map((metricKey) => {
            const row = latestRows.find((item) => item.metric_key === metricKey);
            return <Metric key={metricKey} label={row?.label ?? metricKey} value={formatMetricValue(row)} />;
          })}
        </div>
      </div>
      <table className="terminal-table wide" aria-label="Fiscal Fitness source table">
        <thead>
          <tr>
            <th>FY</th>
            <th>Category</th>
            <th>Metric</th>
            <th>Value</th>
            <th>Direction</th>
            <th>Quality</th>
            <th>Source</th>
            <th>Confidence</th>
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {latestRows.map((row) => (
            <tr key={`${row.fiscal_year}-${row.metric_key}`}>
              <td>{row.fiscal_year}</td>
              <td>{row.category}</td>
              <td>{row.label}</td>
              <td>
                <FiscalFitnessAuditCellButton row={row} onSelect={selectFiscalFitnessFact}>
                  {formatMetricValue(row)}
                </FiscalFitnessAuditCellButton>
              </td>
              <td>{row.direction}</td>
              <td>{row.quality_status}</td>
              <td>{String(row.source_trace?.source_type ?? row.method ?? "-")}</td>
              <td>{formatConfidence(row.confidence)}</td>
              <td>{row.flags.length ? row.flags.join(", ") : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <SelectedAuditTrace
        row={selectedAuditRow}
        fallbackTrace={selectedFitnessRow?.source_trace}
        fallbackLabel="fiscal_fitness source_trace"
      />
      <div className="source-box">
        <strong>Fiscal Fitness trace</strong>
        <code>{JSON.stringify(latestRows[0]?.source_trace ?? {}, null, 2)}</code>
      </div>
      <div className="source-box">
        <strong>Flags</strong>
        <code>{JSON.stringify(flags, null, 2)}</code>
      </div>
    </section>
  );
}

function FiscalFitnessAuditCellButton({
  row,
  onSelect,
  children
}: {
  row: FiscalFitnessRow;
  onSelect: (fiscalYear: number, metricKey: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`fiscal-fitness-audit-cell-${row.fiscal_year}-${row.metric_key}`}
      aria-label={`Audit fiscal fitness ${row.fiscal_year} ${row.metric_key}`}
      onClick={() => onSelect(row.fiscal_year, row.metric_key)}
    >
      {children}
    </button>
  );
}

function formatMetricValue(row: FiscalFitnessRow | undefined) {
  if (!row || row.value === null || row.value === undefined || row.value === "") {
    return "-";
  }
  return row.unit === "percent" ? formatPercent(row.value) : formatNumber(row.value);
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

function formatConfidence(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : String(raw);
}
