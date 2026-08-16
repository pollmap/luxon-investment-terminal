"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { KrSourceReadinessCard } from "./kr-source-readiness-card";
import { Metric } from "./terminal-primitives";
import type {
  AuditRow,
  FinancialRow,
  KrValuationCacheCoverage,
  KrValuationCacheUniverseCoverage
} from "../lib/terminal-types";

type FinancialsPanelProps = {
  ticker?: string | null;
  financials: FinancialRow[];
  auditRows: AuditRow[];
  krCacheCoverage?: KrValuationCacheCoverage | null;
  krCacheUniverse?: KrValuationCacheUniverseCoverage | null;
};

const financialMiniCharts: Array<{ title: string; field: keyof FinancialRow }> = [
  { title: "Revenue", field: "revenue" },
  { title: "FCF", field: "fcf" },
  { title: "ROE", field: "roe" }
];

const statementPeriodModes = ["annual", "quarterly", "TTM"] as const;
const statementBasisModes = ["reported", "reconstructed"] as const;
const statementDisplayModes = ["absolute", "per-share", "common-size"] as const;

type StatementPeriodMode = (typeof statementPeriodModes)[number];
type StatementBasisMode = (typeof statementBasisModes)[number];
type StatementDisplayMode = (typeof statementDisplayModes)[number];

export function FinancialsPanel({
  ticker,
  financials,
  auditRows,
  krCacheCoverage,
  krCacheUniverse
}: FinancialsPanelProps) {
  const latestFinancialYear = financials.at(-1)?.fiscal_year ?? 0;
  const [selectedFact, setSelectedFact] = useState<{ fiscalYear?: number; factName: string }>({
    factName: "revenue"
  });
  const [periodMode, setPeriodMode] = useState<StatementPeriodMode>("annual");
  const [basisMode, setBasisMode] = useState<StatementBasisMode>("reported");
  const [displayMode, setDisplayMode] = useState<StatementDisplayMode>("absolute");
  const selectedYear = selectedFact.fiscalYear ?? latestFinancialYear;
  const selectedFinancialRow = financials.find((row) => row.fiscal_year === selectedYear) ?? financials.at(-1);
  const selectedAuditRow = auditRows.find(
    (row) => row.fiscal_year === selectedYear && row.fact_name === `financials.${selectedFact.factName}`
  );
  const financialAuditRows = auditRows.filter((row) => row.fact_name?.startsWith("financials."));
  const p1States = buildFinancialsP1States(financials, financialAuditRows);
  const sourceTargets = [
    { key: "statement-cell", label: "statement cell", factName: "revenue", detail: "financials.revenue" },
    { key: "ratio-card", label: "ratio card", factName: "roe", detail: "financials.roe" },
    { key: "chart-point", label: "chart point", factName: "fcf", detail: "financials.fcf" },
    { key: "source-document-row", label: "source document row", factName: "revenue", detail: "source document row" }
  ];

  function selectFinancialFact(fiscalYear: number, factName: string) {
    setSelectedFact({ fiscalYear, factName });
  }

  if (!financials.length) {
    return (
      <section className="single-panel">
        <div className="panel-header">
          <div>
            <h1>Financials</h1>
            <p>Financial statement rows stay gated until source-backed statement facts are available.</p>
          </div>
        </div>
        <div className="financials-contract-grid" data-testid="financials-p1-contract">
          <section className="financials-contract-card route">
            <span>Route</span>
            <strong>/financials</strong>
            <small>IS/BS/CF display is waiting for normalized statement facts with source_trace.</small>
          </section>
          <section className="financials-contract-card wide acceptance">
            <span>Display gate</span>
            <strong>No source_trace, no financial statement number.</strong>
            <small>KR valuation warehouse can unlock chart valuation rows before full financial statement rows exist.</small>
          </section>
          <KrSourceReadinessCard
            title="Financials KR source gate"
            testIdPrefix="financials-kr"
            ticker={ticker}
            krCacheCoverage={krCacheCoverage}
            krCacheUniverse={krCacheUniverse}
          />
        </div>
      </section>
    );
  }

  return (
    <section className="single-panel financials-panel">
      <div className="panel-header">
        <div>
          <h1>Financials</h1>
          <p>IS/BS/CF-derived series for Revenue, EPS, FCF, margins, ROE, ROIC, and debt trend.</p>
        </div>
        <div className="facts-row">
          <Metric label="Latest FY" value={String(selectedFinancialRow?.fiscal_year ?? "-")} />
          <Metric label="Revenue" value={formatNumber(selectedFinancialRow?.revenue)} />
          <Metric label="FCF" value={formatNumber(selectedFinancialRow?.fcf)} />
          <Metric label="ROE" value={formatPercent(selectedFinancialRow?.roe)} />
        </div>
      </div>

      <div className="financials-contract-grid" data-testid="financials-p1-contract">
        <section className="financials-contract-card route">
          <span>Route</span>
          <strong>/financials</strong>
          <small>Review statements and source-backed operating trends.</small>
        </section>
        <section className="financials-contract-card">
          <span>Data dependencies</span>
          <strong>normalized_facts, derived_metrics, statement periods, quality_flags, source documents</strong>
          <small>No source_trace, no financial statement number.</small>
        </section>
        <section className="financials-contract-card">
          <span>Interactions</span>
          <strong>annual/quarterly/TTM toggle, reported/reconstructed toggle, per-share/common-size switch, cell audit</strong>
          <small>Unsupported modes remain source-gated and do not mutate displayed numbers.</small>
        </section>
        <section className="financials-contract-card wide">
          <span>Screen model</span>
          <div className="financials-screen-model">
            {["IS table", "BS table", "CF table", "Trend chart", "Ratio cards", "Quality flags"].map((label) => (
              <em key={label}>{label}</em>
            ))}
          </div>
        </section>
        <section className="financials-contract-card wide">
          <span>Source-gated statement modes</span>
          <div className="financials-mode-controls" data-testid="financials-mode-controls">
            <ModeButtonGroup
              label="Period"
              values={statementPeriodModes}
              selected={periodMode}
              onSelect={setPeriodMode}
            />
            <ModeButtonGroup label="Basis" values={statementBasisModes} selected={basisMode} onSelect={setBasisMode} />
            <ModeButtonGroup
              label="Display"
              values={statementDisplayModes}
              selected={displayMode}
              onSelect={setDisplayMode}
            />
          </div>
          <small>
            Active view contract: {periodMode} / {basisMode} / {displayMode}. Values change only after matching
            source-backed statement rows exist.
          </small>
        </section>
        <section className="financials-contract-card wide">
          <span>States</span>
          <div className="financials-state-chips" data-testid="financials-state-chips">
            {p1States.map((state) => (
              <em className={`financials-state-chip ${state.tone}`} key={state.label}>
                {state.label}: {state.value}
              </em>
            ))}
          </div>
        </section>
        <section className="financials-contract-card wide">
          <span>source_trace click targets</span>
          <div className="financials-source-targets" data-testid="financials-source-targets">
            {sourceTargets.map((target) => (
              <button
                key={target.key}
                type="button"
                data-testid={`financials-target-${target.key}`}
                onClick={() => selectFinancialFact(latestFinancialYear, target.factName)}
              >
                <span>{target.label}</span>
                <strong>{target.detail}</strong>
              </button>
            ))}
          </div>
        </section>
        <section className="financials-contract-card wide acceptance">
          <span>Acceptance criteria</span>
          <strong>IS/BS/CF rows keep source_trace; derived ratios expose formula; no mixed unit display.</strong>
        </section>
        <KrSourceReadinessCard
          title="Financials KR source gate"
          testIdPrefix="financials-kr"
          ticker={ticker}
          krCacheCoverage={krCacheCoverage}
          krCacheUniverse={krCacheUniverse}
        />
      </div>

      <div className="mini-chart-row">
        {financialMiniCharts.map((chart) => (
          <MiniBars key={chart.field} title={chart.title} rows={financials} field={chart.field} />
        ))}
      </div>

      <table className="terminal-table wide" aria-label="Financials source table">
        <thead>
          <tr>
            <th>FY</th>
            <th>Revenue</th>
            <th>EPS</th>
            <th>FCF</th>
            <th>Gross M</th>
            <th>OPM</th>
            <th>NPM</th>
            <th>ROE</th>
            <th>ROIC</th>
            <th>Debt/Eq</th>
            <th>Trace</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {financials.map((row) => (
            <tr key={row.fiscal_year}>
              <td>{row.fiscal_year}</td>
              <td>
                <FinancialAuditCellButton row={row} factName="revenue" onSelect={selectFinancialFact}>
                  {formatNumber(row.revenue)}
                </FinancialAuditCellButton>
              </td>
              <td>
                <FinancialAuditCellButton row={row} factName="eps" onSelect={selectFinancialFact}>
                  {formatNumber(row.eps)}
                </FinancialAuditCellButton>
              </td>
              <td>
                <FinancialAuditCellButton row={row} factName="fcf" onSelect={selectFinancialFact}>
                  {formatNumber(row.fcf)}
                </FinancialAuditCellButton>
              </td>
              <td>
                <FinancialAuditCellButton row={row} factName="gross_margin" onSelect={selectFinancialFact}>
                  {formatPercent(row.gross_margin)}
                </FinancialAuditCellButton>
              </td>
              <td>
                <FinancialAuditCellButton row={row} factName="operating_margin" onSelect={selectFinancialFact}>
                  {formatPercent(row.operating_margin)}
                </FinancialAuditCellButton>
              </td>
              <td>
                <FinancialAuditCellButton row={row} factName="net_margin" onSelect={selectFinancialFact}>
                  {formatPercent(row.net_margin)}
                </FinancialAuditCellButton>
              </td>
              <td>
                <FinancialAuditCellButton row={row} factName="roe" onSelect={selectFinancialFact}>
                  {formatPercent(row.roe)}
                </FinancialAuditCellButton>
              </td>
              <td>
                <FinancialAuditCellButton row={row} factName="roic" onSelect={selectFinancialFact}>
                  {formatPercent(row.roic)}
                </FinancialAuditCellButton>
              </td>
              <td>
                <FinancialAuditCellButton row={row} factName="debt_to_equity" onSelect={selectFinancialFact}>
                  {formatNumber(row.debt_to_equity)}
                </FinancialAuditCellButton>
              </td>
              <td>{row.method}</td>
              <td>{formatNumber(row.confidence)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <SelectedAuditTrace row={selectedAuditRow} fallbackTrace={selectedFinancialRow?.source_trace} />
    </section>
  );
}

function ModeButtonGroup<T extends string>({
  label,
  values,
  selected,
  onSelect
}: {
  label: string;
  values: readonly T[];
  selected: T;
  onSelect: (value: T) => void;
}) {
  return (
    <div>
      <span>{label}</span>
      <div>
        {values.map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={selected === value}
            className={selected === value ? "active" : undefined}
            onClick={() => onSelect(value)}
          >
            {value}
          </button>
        ))}
      </div>
    </div>
  );
}

function FinancialAuditCellButton({
  row,
  factName,
  onSelect,
  children
}: {
  row: FinancialRow;
  factName: string;
  onSelect: (fiscalYear: number, factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`financial-audit-cell-${row.fiscal_year}-${factName}`}
      aria-label={`Audit financials ${row.fiscal_year} ${factName}`}
      onClick={() => onSelect(row.fiscal_year, factName)}
    >
      {children}
    </button>
  );
}

function buildFinancialsP1States(financials: FinancialRow[], auditRows: AuditRow[]) {
  const hasRows = financials.length > 0;
  const hasSourceTrace = auditRows.length > 0 && auditRows.every((row) => row.source_trace?.source_document_id);
  const hasUnitMismatch = hasQualitySignal(auditRows, ["unit", "currency", "mismatch"]);
  const hasRestatement = hasQualitySignal(auditRows, ["restatement", "restated"]);
  const hasStalePeriod = hasQualitySignal(auditRows, ["stale"]);
  return [
    { label: "missing statement", value: hasRows ? "clear" : "blocked", tone: hasRows ? "ok" : "warn" },
    { label: "unit mismatch", value: hasUnitMismatch ? "review" : "clear", tone: hasUnitMismatch ? "warn" : "ok" },
    {
      label: "restatement warning",
      value: hasRestatement ? "review" : "clear",
      tone: hasRestatement ? "warn" : "ok"
    },
    { label: "stale period", value: hasStalePeriod ? "review" : "clear", tone: hasStalePeriod ? "warn" : "ok" },
    {
      label: "no source rejected",
      value: hasSourceTrace ? "passed" : "source_trace required",
      tone: hasSourceTrace ? "ok" : "neutral"
    }
  ];
}

function hasQualitySignal(auditRows: AuditRow[], terms: string[]) {
  return auditRows.some((row) => {
    const haystack = [
      row.quality_status,
      row.formula ?? "",
      row.source_trace?.quality_status ?? "",
      row.source_trace?.formula ?? "",
      ...row.flags
    ]
      .join(" ")
      .toLowerCase();
    return terms.some((term) => haystack.includes(term));
  });
}

function MiniBars({ title, rows, field }: { title: string; rows: FinancialRow[]; field: keyof FinancialRow }) {
  const values = rows.map((row) => Math.abs(Number(row[field])) || 0);
  const max = Math.max(...values, 1);
  return (
    <div className="mini-chart">
      <strong>{title}</strong>
      <div>
        {rows.map((row) => (
          <span
            key={`${title}-${row.fiscal_year}`}
            style={{ height: `${Math.max(6, (Math.abs(Number(row[field])) / max) * 88)}%` }}
          >
            <small>{row.fiscal_year}</small>
          </span>
        ))}
      </div>
    </div>
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
