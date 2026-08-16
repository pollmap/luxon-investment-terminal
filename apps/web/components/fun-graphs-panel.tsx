"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { Metric } from "./terminal-primitives";
import { publicTraceSummary } from "../lib/audit-utils";
import type { AuditRow, FunGraphPoint, FunGraphs } from "../lib/terminal-types";

type FunGraphsPanelProps = {
  funGraphs: FunGraphs | null;
  auditRows: AuditRow[];
};

const defaultFunGraphVisibility: Record<string, boolean> = {
  revenue: true,
  adjusted_eps: true,
  free_cash_flow: true,
  roe_pct: true,
  debt_to_equity: false
};

export function FunGraphsPanel({ funGraphs, auditRows }: FunGraphsPanelProps) {
  const [visible, setVisible] = useState<Record<string, boolean>>(defaultFunGraphVisibility);
  const [selectedFact, setSelectedFact] = useState<{ fiscalYear?: number; metricKey: string }>({
    metricKey: "revenue"
  });

  if (!funGraphs) {
    return (
      <section className="single-panel">
        <div className="panel-header">
          <div>
            <h1>Fun Graphs</h1>
            <p>Financial underlying numbers are unavailable for this request.</p>
          </div>
        </div>
      </section>
    );
  }

  const selectedMetrics = funGraphs.metrics.filter((metric) => visible[metric.metric_key]);
  const rows = selectedMetrics.flatMap((metric) =>
    metric.points.map((point) => ({ metric, point }))
  );
  const years = Array.from(
    new Set(funGraphs.metrics.flatMap((metric) => metric.points.map((point) => point.fiscal_year)))
  ).sort((left, right) => left - right);
  const latestYear = funGraphs.summary.latest_year ?? years.at(-1) ?? 0;
  const selectedYear = selectedFact.fiscalYear ?? latestYear;
  const selectedMetric =
    funGraphs.metrics.find((metric) => metric.metric_key === selectedFact.metricKey) ?? funGraphs.metrics[0];
  const selectedPoint = selectedMetric?.points.find((point) => point.fiscal_year === selectedYear) ?? selectedMetric?.points.at(-1);
  const selectedAuditRow = auditRows.find(
    (row) => row.fiscal_year === selectedYear && row.fact_name === `fun_graphs.${selectedFact.metricKey}`
  );
  const flags = Array.from(new Set(funGraphs.metrics.flatMap((metric) => metric.flags ?? []))).sort();

  function selectFunGraphFact(fiscalYear: number, metricKey: string) {
    setSelectedFact({ fiscalYear, metricKey });
  }

  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Fun Graphs</h1>
          <p>Financial underlying numbers as source-traced line series for statement items, ratios, margins, and per-share metrics.</p>
        </div>
        <div className="facts-row">
          <Metric label="Metrics" value={String(funGraphs.summary.metric_count)} />
          <Metric label="Latest FY" value={String(funGraphs.summary.latest_year ?? "-")} />
          <Metric label="Quality" value={funGraphs.summary.quality_status} />
        </div>
      </div>
      <div className="fun-graph-layout">
        <div>
          <div className="line-toggles compact">
            {funGraphs.metrics.map((metric, index) => (
              <button
                key={metric.metric_key}
                className={`toggle ${visible[metric.metric_key] ? "on" : ""}`}
                type="button"
                onClick={() => setVisible((state) => ({ ...state, [metric.metric_key]: !state[metric.metric_key] }))}
              >
                <span className="fun-legend" style={{ background: funLineColor(index) }} />
                {metric.label}
              </button>
            ))}
          </div>
          <div className="fun-line-chart" role="img" aria-label="FUN Graphs line chart">
            <svg viewBox="0 0 100 100" preserveAspectRatio="none">
              {selectedMetrics.map((metric, index) => (
                <polyline
                  key={metric.metric_key}
                  data-testid={index === 0 ? "fun-graphs-line" : undefined}
                  points={buildFunLinePoints(metric.points)}
                  style={{ stroke: funLineColor(funGraphs.metrics.findIndex((item) => item.metric_key === metric.metric_key)) }}
                />
              ))}
            </svg>
            <div className="fun-year-axis" style={{ gridTemplateColumns: `repeat(${Math.max(years.length, 1)}, minmax(0, 1fr))` }}>
              {years.map((year) => <span key={year}>{year}</span>)}
            </div>
          </div>
        </div>
        <div className="source-box">
          <strong>Fun Graphs trace</strong>
          <code>{JSON.stringify(publicTraceSummary(funGraphs.source_trace), null, 2)}</code>
        </div>
      </div>
      <table className="terminal-table wide" aria-label="FUN Graphs source table">
        <thead>
          <tr>
            <th>FY</th>
            <th>Metric</th>
            <th>Statement</th>
            <th>Value</th>
            <th>Unit</th>
            <th>Method</th>
            <th>Quality</th>
            <th>Source</th>
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ metric, point }) => (
            <tr key={`${metric.metric_key}-${point.fiscal_year}`}>
              <td>{point.fiscal_year}</td>
              <td>{metric.label}</td>
              <td>{metric.statement}</td>
              <td>
                <FunGraphsAuditCellButton
                  fiscalYear={point.fiscal_year}
                  metricKey={metric.metric_key}
                  onSelect={selectFunGraphFact}
                >
                  {metric.unit === "percent" ? formatPercent(point.value) : formatNumber(point.value)}
                </FunGraphsAuditCellButton>
              </td>
              <td>{metric.unit}</td>
              <td>{point.method}</td>
              <td>{point.quality_status}</td>
              <td>{String(point.source_trace?.source_type ?? "-")}</td>
              <td>{point.flags.length ? point.flags.join(", ") : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <SelectedAuditTrace
        row={selectedAuditRow}
        fallbackTrace={selectedPoint?.source_trace ?? funGraphs.source_trace}
        fallbackLabel="fun_graphs source_trace"
      />
      <div className="source-box">
        <strong>Flags</strong>
        <code>{JSON.stringify(flags, null, 2)}</code>
      </div>
    </section>
  );
}

function FunGraphsAuditCellButton({
  fiscalYear,
  metricKey,
  onSelect,
  children
}: {
  fiscalYear: number;
  metricKey: string;
  onSelect: (fiscalYear: number, metricKey: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`fun-graphs-audit-cell-${fiscalYear}-${metricKey}`}
      aria-label={`Audit fun graphs ${fiscalYear} ${metricKey}`}
      onClick={() => onSelect(fiscalYear, metricKey)}
    >
      {children}
    </button>
  );
}

function funLineColor(index: number) {
  const colors = ["#101418", "#2563eb", "#d97706", "#16794b", "#7c3aed", "#b42318", "#0f766e", "#475569", "#a16207", "#4338ca"];
  return colors[Math.max(0, index) % colors.length];
}

function buildFunLinePoints(points: FunGraphPoint[]) {
  const parsed = points
    .map((point, index) => ({ index, value: Number(point.value) }))
    .filter((point) => Number.isFinite(point.value));
  if (!parsed.length) {
    return "";
  }
  const values = parsed.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min || 1;
  return parsed
    .map((point) => {
      const x = points.length <= 1 ? 50 : ((point.index + 0.5) / points.length) * 100;
      const y = 88 - ((point.value - min) / spread) * 72;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
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
