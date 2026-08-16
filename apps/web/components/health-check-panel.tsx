"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { Metric } from "./terminal-primitives";
import type { AuditRow, HealthCheck } from "../lib/terminal-types";

type HealthCheckPanelProps = {
  healthCheck: HealthCheck;
  auditRows: AuditRow[];
};

export function HealthCheckPanel({ healthCheck, auditRows }: HealthCheckPanelProps) {
  const [selectedFactName, setSelectedFactName] = useState("overall_score");
  const selectedAxis = healthCheck.axes.find((axis) => axis.axis_key === selectedFactName);
  const selectedAuditRow = auditRows.find(
    (row) => row.fiscal_year === healthCheck.fiscal_year && row.fact_name === `health_check.${selectedFactName}`
  );
  const flags = Array.from(
    new Set([...(healthCheck.flags ?? []), ...healthCheck.axes.flatMap((axis) => axis.flags ?? [])])
  ).sort();

  function selectHealthCheckFact(factName: string) {
    setSelectedFactName(factName);
  }

  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Health Check</h1>
          <p>
            FG Score-style 0-100 quality view derived from source-traced Fiscal Fitness, forecast evidence, and analyst
            scorecard inputs.
          </p>
        </div>
        <div className="facts-row">
          <div className="metric-chip">
            <span>FG Score</span>
            <HealthCheckAuditCellButton
              factName="overall_score"
              testIdSuffix="overall_score"
              onSelect={selectHealthCheckFact}
            >
              {formatScore(healthCheck.overall_score)}
            </HealthCheckAuditCellButton>
          </div>
          <Metric label="Rating" value={healthCheck.rating} />
          <Metric label="FY" value={String(healthCheck.fiscal_year ?? "-")} />
          <Metric label="Quality" value={healthCheck.quality_status} />
        </div>
      </div>
      <div className="score-grid">
        {healthCheck.axes.map((axis) => (
          <div key={axis.axis_key} className="score-card">
            <div>
              <strong>{axis.label}</strong>
              <HealthCheckAuditCellButton
                factName={axis.axis_key}
                testIdSuffix={axis.axis_key}
                onSelect={selectHealthCheckFact}
              >
                {formatScore(axis.score)}
              </HealthCheckAuditCellButton>
            </div>
            <div className="score-bar" aria-label={`${axis.label} score`}>
              <i style={{ width: `${Math.max(0, Math.min(100, Number(axis.score) || 0))}%` }} />
            </div>
            <small>{axis.quality_status}</small>
          </div>
        ))}
      </div>
      <table className="terminal-table wide" aria-label="Health Check source table">
        <thead>
          <tr>
            <th>Axis</th>
            <th>Score</th>
            <th>Weight</th>
            <th>Inputs</th>
            <th>Quality</th>
            <th>Source</th>
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {healthCheck.axes.map((axis) => (
            <tr key={axis.axis_key}>
              <td>{axis.label}</td>
              <td>
                <HealthCheckAuditCellButton
                  factName={axis.axis_key}
                  testIdSuffix={`${axis.axis_key}-table`}
                  onSelect={selectHealthCheckFact}
                >
                  {formatScore(axis.score)}
                </HealthCheckAuditCellButton>
              </td>
              <td>{Number(axis.weight) * 100}%</td>
              <td>{axis.inputs.filter((input) => input.score !== null).map((input) => input.label).join(", ") || "-"}</td>
              <td>{axis.quality_status}</td>
              <td>{String(axis.source_trace?.source_type ?? "health_check_axis_derived")}</td>
              <td>{axis.flags.length ? axis.flags.join(", ") : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <SelectedAuditTrace
        row={selectedAuditRow}
        fallbackTrace={selectedAxis?.source_trace ?? healthCheck.source_trace}
        fallbackLabel="health_check source_trace"
      />
      <div className="source-box">
        <strong>Health Check trace</strong>
        <code>{JSON.stringify(healthCheck.source_trace ?? {}, null, 2)}</code>
      </div>
      <div className="source-box">
        <strong>Flags</strong>
        <code>{JSON.stringify(flags, null, 2)}</code>
      </div>
    </section>
  );
}

function HealthCheckAuditCellButton({
  factName,
  testIdSuffix,
  onSelect,
  children
}: {
  factName: string;
  testIdSuffix: string;
  onSelect: (factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`health-check-audit-cell-${testIdSuffix}`}
      aria-label={`Audit health check ${factName}`}
      onClick={() => onSelect(factName)}
    >
      {children}
    </button>
  );
}

function formatScore(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  return Number.isFinite(value) ? value.toFixed(0) : String(raw);
}
