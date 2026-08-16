"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { Metric } from "./terminal-primitives";
import { auditTestIdPart, publicTraceSummary } from "../lib/audit-utils";
import type { AnalystScorecard, AuditRow, ForecastEvidence } from "../lib/terminal-types";

type AnalystScorecardPanelProps = {
  ticker: string;
  scorecard: AnalystScorecard;
  evidence: ForecastEvidence;
  auditRows: AuditRow[];
};

export function AnalystScorecardPanel({
  ticker,
  scorecard,
  evidence,
  auditRows
}: AnalystScorecardPanelProps) {
  const summaryFiscalYear = Math.max(...scorecard.rows.map((row) => row.fiscal_year), 0);
  const [selectedScorecardFact, setSelectedScorecardFact] = useState({
    fiscalYear: summaryFiscalYear,
    factName: "hit_rate_1y_pct"
  });
  const selectedScorecardAuditRow =
    auditRows.find(
      (row) =>
        row.fiscal_year === selectedScorecardFact.fiscalYear &&
        row.fact_name === `analyst_scorecard.${selectedScorecardFact.factName}`
    ) ??
    auditRows.find((row) => row.fact_name === `analyst_scorecard.${selectedScorecardFact.factName}`) ??
    auditRows.find((row) => (row.fact_name ?? "").startsWith("analyst_scorecard."));

  function selectScorecardFact(fiscalYear: number, factName: string) {
    setSelectedScorecardFact({ fiscalYear, factName });
  }

  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Analyst Scorecard</h1>
          <p>
            Point-in-time consensus snapshots are required for production 1Y/2Y hit-rate scoring. Fixture proxy rows are
            isolated and quality-labeled.
          </p>
        </div>
        <div className="facts-row">
          <Metric label="1Y hit rate" value={formatPercent(scorecard.summary.hit_rate_1y_pct)} />
          <Metric label="2Y hit rate" value={formatPercent(scorecard.summary.hit_rate_2y_pct)} />
          <Metric label="Quality" value={scorecard.quality_status} />
        </div>
      </div>
      <div className="quality-ledger">
        <div>
          <strong>{ticker}</strong>
          <span>Consensus snapshot pipeline</span>
          <em>{scorecard.status}</em>
        </div>
        <div>
          <strong>1Y estimate hit rate</strong>
          <span>{scorecard.summary.required_source}</span>
          <em>
            <ScorecardAuditCellButton fiscalYear={summaryFiscalYear} factName="hit_rate_1y_pct" onSelect={selectScorecardFact}>
              {scorecard.summary.hit_rate_1y_pct}%
            </ScorecardAuditCellButton>
          </em>
        </div>
        <div>
          <strong>2Y estimate hit rate</strong>
          <span>{scorecard.summary.required_source}</span>
          <em>
            <ScorecardAuditCellButton fiscalYear={summaryFiscalYear} factName="hit_rate_2y_pct" onSelect={selectScorecardFact}>
              {scorecard.summary.hit_rate_2y_pct}%
            </ScorecardAuditCellButton>
          </em>
        </div>
        <div>
          <strong>Current sentiment</strong>
          <span>{evidence.sentiment.quality_status}</span>
          <em>{evidence.sentiment.label}</em>
        </div>
      </div>
      <table className="terminal-table wide" aria-label="Analyst Scorecard rows">
        <thead>
          <tr>
            <th>FY</th>
            <th>Actual EPS</th>
            <th>1Y Estimate</th>
            <th>1Y Error</th>
            <th>1Y Result</th>
            <th>2Y Estimate</th>
            <th>2Y Error</th>
            <th>2Y Result</th>
            <th>Quality</th>
          </tr>
        </thead>
        <tbody>
          {scorecard.rows.map((row) => (
            <tr key={row.fiscal_year}>
              <td>{row.fiscal_year}</td>
              <td>
                <ScorecardAuditCellButton fiscalYear={row.fiscal_year} factName="actual_eps" onSelect={selectScorecardFact}>
                  {row.actual_eps ?? "-"}
                </ScorecardAuditCellButton>
              </td>
              <td>
                <ScorecardAuditCellButton fiscalYear={row.fiscal_year} factName="estimate_1y_prior" onSelect={selectScorecardFact}>
                  {row.estimate_1y_prior ?? "-"}
                </ScorecardAuditCellButton>
              </td>
              <td>
                <ScorecardAuditCellButton fiscalYear={row.fiscal_year} factName="error_1y_pct" onSelect={selectScorecardFact}>
                  {formatPercent(row.error_1y_pct)}
                </ScorecardAuditCellButton>
              </td>
              <td>
                <ScorecardAuditCellButton fiscalYear={row.fiscal_year} factName="result_1y" onSelect={selectScorecardFact}>
                  {row.result_1y}
                </ScorecardAuditCellButton>
              </td>
              <td>
                <ScorecardAuditCellButton fiscalYear={row.fiscal_year} factName="estimate_2y_prior" onSelect={selectScorecardFact}>
                  {row.estimate_2y_prior ?? "-"}
                </ScorecardAuditCellButton>
              </td>
              <td>
                <ScorecardAuditCellButton fiscalYear={row.fiscal_year} factName="error_2y_pct" onSelect={selectScorecardFact}>
                  {formatPercent(row.error_2y_pct)}
                </ScorecardAuditCellButton>
              </td>
              <td>
                <ScorecardAuditCellButton fiscalYear={row.fiscal_year} factName="result_2y" onSelect={selectScorecardFact}>
                  {row.result_2y}
                </ScorecardAuditCellButton>
              </td>
              <td>{row.quality_status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <SelectedAuditTrace
        row={selectedScorecardAuditRow}
        fallbackTrace={scorecard.source_trace}
        fallbackLabel={`analyst_scorecard.${selectedScorecardFact.factName}`}
      />
      <div className="source-box">
        <strong>Analyst Scorecard trace</strong>
        <code>{JSON.stringify(publicTraceSummary(scorecard.source_trace), null, 2)}</code>
      </div>
      <div className="source-box">
        <strong>Flags</strong>
        <code>{JSON.stringify(scorecard.flags, null, 2)}</code>
      </div>
    </section>
  );
}

function ScorecardAuditCellButton({
  fiscalYear,
  factName,
  onSelect,
  children
}: {
  fiscalYear: number;
  factName: string;
  onSelect: (fiscalYear: number, factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`analyst-scorecard-audit-cell-${fiscalYear}-${auditTestIdPart(factName)}`}
      aria-label={`Audit analyst scorecard ${fiscalYear} ${factName}`}
      onClick={() => onSelect(fiscalYear, factName)}
    >
      {children}
    </button>
  );
}

function formatPercent(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const text = String(raw);
  return text.endsWith("%") ? text : `${text}%`;
}
