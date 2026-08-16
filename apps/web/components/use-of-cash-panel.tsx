"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { Metric } from "./terminal-primitives";
import type { AuditRow, UseOfCashRow } from "../lib/terminal-types";

export function UseOfCashPanel({ rows, auditRows }: { rows: UseOfCashRow[]; auditRows: AuditRow[] }) {
  const latest = rows.at(-1);
  const [selectedFact, setSelectedFact] = useState<{ fiscalYear?: number; factName: string }>({
    factName: "free_cash_flow"
  });
  const selectedYear = selectedFact.fiscalYear ?? latest?.fiscal_year ?? 0;
  const selectedUseOfCashRow = rows.find((row) => row.fiscal_year === selectedYear) ?? latest;
  const selectedAuditRow = auditRows.find(
    (row) => row.fiscal_year === selectedYear && row.fact_name === `use_of_cash.${selectedFact.factName}`
  );

  function selectUseOfCashFact(fiscalYear: number, factName: string) {
    setSelectedFact({ fiscalYear, factName });
  }

  const flags = Array.from(new Set(rows.flatMap((row) => row.flags ?? []))).sort();

  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Use of Cash</h1>
          <p>
            Source-traced capital allocation view. Missing cash-use buckets remain blank until Capex, buyback, or debt
            repayment facts are ingested.
          </p>
        </div>
        <div className="facts-row">
          <Metric label="FCF margin" value={formatPercent(latest?.fcf_margin_pct)} />
          <Metric label="Dividend payout" value={formatPercent(latest?.dividend_payout_pct)} />
          <Metric label="Quality" value={latest?.quality_status ?? "-"} />
        </div>
      </div>
      <table className="terminal-table wide">
        <thead>
          <tr>
            <th>FY</th>
            <th>OCF</th>
            <th>FCF</th>
            <th>FCF margin</th>
            <th>Div/share</th>
            <th>Payout</th>
            <th>Capex</th>
            <th>Repurchase</th>
            <th>Debt repay</th>
            <th>Acq.</th>
            <th>Net cash use</th>
            <th>Debt/Eq</th>
            <th>Source</th>
            <th>Confidence</th>
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.fiscal_year}>
              <td>{row.fiscal_year}</td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="operating_cash_flow" onSelect={selectUseOfCashFact}>
                  {formatNumber(row.operating_cash_flow)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="free_cash_flow" onSelect={selectUseOfCashFact}>
                  {formatNumber(row.free_cash_flow)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="fcf_margin_pct" onSelect={selectUseOfCashFact}>
                  {formatPercent(row.fcf_margin_pct)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="dividend_per_share" onSelect={selectUseOfCashFact}>
                  {formatNumber(row.dividend_per_share)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="dividend_payout_pct" onSelect={selectUseOfCashFact}>
                  {formatPercent(row.dividend_payout_pct)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="capex" onSelect={selectUseOfCashFact}>
                  {formatNumber(row.capex)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="share_repurchases" onSelect={selectUseOfCashFact}>
                  {formatNumber(row.share_repurchases)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="debt_repayment" onSelect={selectUseOfCashFact}>
                  {formatNumber(row.debt_repayment)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="acquisitions" onSelect={selectUseOfCashFact}>
                  {formatNumber(row.acquisitions)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="net_cash_use" onSelect={selectUseOfCashFact}>
                  {formatNumber(row.net_cash_use)}
                </UseOfCashAuditCellButton>
              </td>
              <td>
                <UseOfCashAuditCellButton row={row} factName="debt_to_equity" onSelect={selectUseOfCashFact}>
                  {formatNumber(row.debt_to_equity)}
                </UseOfCashAuditCellButton>
              </td>
              <td>{String(row.source_trace?.source_type ?? row.method ?? "-")}</td>
              <td>{formatConfidence(row.confidence)}</td>
              <td>{row.flags.length ? row.flags.join(", ") : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <SelectedAuditTrace
        row={selectedAuditRow}
        fallbackTrace={selectedUseOfCashRow?.source_trace}
        fallbackLabel="use_of_cash source_trace"
      />
      <div className="source-box">
        <strong>Use of Cash trace</strong>
        <code>{JSON.stringify(latest?.source_trace ?? {}, null, 2)}</code>
      </div>
      <div className="source-box">
        <strong>Flags</strong>
        <code>{JSON.stringify(flags, null, 2)}</code>
      </div>
    </section>
  );
}

function UseOfCashAuditCellButton({
  row,
  factName,
  onSelect,
  children
}: {
  row: UseOfCashRow;
  factName: string;
  onSelect: (fiscalYear: number, factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`use-of-cash-audit-cell-${row.fiscal_year}-${factName}`}
      aria-label={`Audit use of cash ${row.fiscal_year} ${factName}`}
      onClick={() => onSelect(row.fiscal_year, factName)}
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

function formatConfidence(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : String(raw);
}
