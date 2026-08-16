"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { Metric } from "./terminal-primitives";
import { auditTestIdPart } from "../lib/audit-utils";
import type { AuditRow, PortfolioSummary } from "../lib/terminal-types";

type PortfolioPanelProps = {
  portfolio: PortfolioSummary;
  auditRows: AuditRow[];
  csvText: string;
  importStatus: string;
  onCsvChange: (value: string) => void;
  onImport: () => void;
};

export function PortfolioPanel({
  portfolio,
  auditRows,
  csvText,
  importStatus,
  onCsvChange,
  onImport
}: PortfolioPanelProps) {
  const [selectedPortfolioCell, setSelectedPortfolioCell] = useState(() => ({
    ticker: portfolio.holdings[0]?.ticker ?? "",
    factName: "market_value",
    namespace: "portfolio"
  }));
  const selectedHolding =
    portfolio.holdings.find((row) => row.ticker === selectedPortfolioCell.ticker) ??
    portfolio.holdings[0];
  const portfolioAuditRows = auditRows.filter((row) => row.fact_name?.startsWith("portfolio."));
  const transactionAuditRows = auditRows.filter((row) => row.fact_name?.startsWith("portfolio_transaction."));
  const selectedPortfolioAuditRow =
    selectedPortfolioCell.namespace === "portfolio_transaction"
      ? auditRows.find((row) => row.fact_name === selectedPortfolioCell.factName)
      : selectedHolding
        ? auditRows.find(
            (row) =>
              row.fact_id.startsWith(`${selectedHolding.ticker}-`) &&
              row.fact_name === `portfolio.${selectedPortfolioCell.factName}`
          )
        : undefined;
  const transactionTimeline = portfolioTransactionTimeline(portfolio, transactionAuditRows);
  const p1States = portfolioP1States(portfolio, importStatus);
  const sourceTracedHoldings = portfolio.holdings.filter((holding) => Boolean(holding.source_trace)).length;
  const selectPortfolioCell = (ticker: string, factName: string, namespace = "portfolio") => {
    setSelectedPortfolioCell({ ticker, factName, namespace });
  };
  const selectFirstTransaction = () => {
    const transaction = transactionTimeline[0];
    if (transaction?.auditFactName) {
      selectPortfolioCell(transaction.ticker, transaction.auditFactName, "portfolio_transaction");
    }
  };

  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Portfolio</h1>
          <p>CSV transactions, holdings, XIRR, sector weights, and transaction overlay source for valuation charts.</p>
        </div>
        <div className="facts-row">
          <Metric label="Market value" value={portfolio.total_market_value} />
          <Metric label="XIRR" value={portfolio.xirr ? `${portfolio.xirr}%` : "-"} />
          <Metric label="As of" value={portfolio.as_of} />
        </div>
      </div>
      <div className="portfolio-contract-grid" data-testid="portfolio-p1-contract">
        <div className="portfolio-contract-card">
          <span>Data dependencies</span>
          <strong>CSV transactions, holdings, prices, dividends, FX, valuation_series, source_trace</strong>
          <small>{portfolio.holdings.length} holdings · {transactionTimeline.length} transactions · {portfolioAuditRows.length + transactionAuditRows.length} audit rows</small>
        </div>
        <div className="portfolio-contract-card">
          <span>Interactions</span>
          <strong>CSV import, holding select, transaction marker toggle, XIRR view, audit click</strong>
          <small>{sourceTracedHoldings}/{portfolio.holdings.length} holdings source traced</small>
        </div>
        <div className="portfolio-contract-card">
          <span>States</span>
          <div className="portfolio-p1-badges" data-testid="portfolio-state-chips">
            {p1States.map((state) => (
              <em key={state.label} className={state.tone}>
                {state.label}
              </em>
            ))}
          </div>
        </div>
        <div className="portfolio-contract-card">
          <span>source_trace click targets</span>
          <div className="portfolio-target-buttons" data-testid="portfolio-source-targets">
            <button type="button" data-testid="portfolio-target-transaction-row" onClick={selectFirstTransaction}>
              transaction row
            </button>
            <button type="button" onClick={() => selectedHolding && selectPortfolioCell(selectedHolding.ticker, "market_value")}>
              holding metric
            </button>
            <button type="button" onClick={() => selectedHolding && selectPortfolioCell(selectedHolding.ticker, "weight_pct")}>
              allocation card
            </button>
            <button type="button" onClick={selectFirstTransaction}>
              overlay marker
            </button>
          </div>
        </div>
        <div className="portfolio-contract-card wide">
          <span>Acceptance criteria</span>
          <strong>User-entered transactions are tagged manual; valuation overlays use source-backed historical rows.</strong>
          <small>Manual CSV input remains tagged by import_trace and transaction rows stay auditable.</small>
        </div>
      </div>
      <div className="portfolio-import" aria-label="Portfolio CSV import">
        <label>
          Portfolio CSV
          <textarea
            aria-label="Portfolio CSV"
            value={csvText}
            onChange={(event) => onCsvChange(event.target.value)}
            spellCheck={false}
          />
        </label>
        <div>
          <button type="button" onClick={onImport}>
            Import CSV
          </button>
          <span>{importStatus}</span>
          <p>Required columns: date, ticker, side, quantity, price, currency, sector.</p>
        </div>
      </div>
      <div className="portfolio-grid">
        <table className="terminal-table wide" aria-label="Portfolio holdings">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Qty</th>
              <th>Avg cost</th>
              <th>Last</th>
              <th>Value</th>
              <th>P/L</th>
              <th>Weight</th>
              <th>Sector</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {portfolio.holdings.map((row) => (
              <tr key={row.ticker}>
                <td>{row.ticker}</td>
                <td>
                  <PortfolioAuditCellButton row={row} factName="quantity" onSelect={selectPortfolioCell}>
                    {row.quantity}
                  </PortfolioAuditCellButton>
                </td>
                <td>
                  <PortfolioAuditCellButton row={row} factName="average_cost" onSelect={selectPortfolioCell}>
                    {row.average_cost}
                  </PortfolioAuditCellButton>
                </td>
                <td>
                  <PortfolioAuditCellButton row={row} factName="latest_price" onSelect={selectPortfolioCell}>
                    {row.latest_price}
                  </PortfolioAuditCellButton>
                </td>
                <td>
                  <PortfolioAuditCellButton row={row} factName="market_value" onSelect={selectPortfolioCell}>
                    {row.market_value}
                  </PortfolioAuditCellButton>
                </td>
                <td>
                  <PortfolioAuditCellButton row={row} factName="unrealized_pnl" onSelect={selectPortfolioCell}>
                    {row.unrealized_pnl}
                  </PortfolioAuditCellButton>
                </td>
                <td>
                  <PortfolioAuditCellButton row={row} factName="weight_pct" onSelect={selectPortfolioCell}>
                    {row.weight_pct}%
                  </PortfolioAuditCellButton>
                </td>
                <td>{row.sector}</td>
                <td>
                  <span className={`source-state-badge ${row.source_trace ? "ok" : "blocked"}`}>
                    {row.source_trace ? "Source traced" : "No source_trace"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="source-box portfolio-allocation-ledger" data-testid="portfolio-allocation-ledger">
          <strong>Sector weights</strong>
          {Object.entries(portfolio.sector_weights).map(([sector, weight]) => {
            const holding = portfolio.holdings.find((row) => row.sector === sector) ?? portfolio.holdings[0];
            return (
              <button
                key={sector}
                type="button"
                data-testid={`portfolio-allocation-card-${auditTestIdPart(sector)}`}
                onClick={() => holding && selectPortfolioCell(holding.ticker, "weight_pct")}
              >
                <span>{sector}</span>
                <strong>{weight}%</strong>
              </button>
            );
          })}
        </div>
        <div className="source-box">
          <strong>Portfolio trace</strong>
          <code>{JSON.stringify(portfolio.import_trace ?? portfolio.source_trace ?? {}, null, 2)}</code>
        </div>
        <div className="portfolio-timeline" aria-label="Portfolio transaction timeline" data-testid="portfolio-transaction-timeline">
          <strong>Transaction overlay ledger</strong>
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Ticker</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Price</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {transactionTimeline.map((transaction, index) => (
                <tr key={`${transaction.ticker}-${transaction.date}-${transaction.side}-${index}`}>
                  <td>{transaction.date}</td>
                  <td>{transaction.ticker}</td>
                  <td>
                    <span className={`filter-badge ${transaction.side === "sell" ? "watch" : "pass"}`}>
                      {transaction.side}
                    </span>
                  </td>
                  <td>{transaction.quantity}</td>
                  <td>
                    <button
                      className="audit-cell-button"
                      type="button"
                      data-testid={`portfolio-transaction-audit-cell-${index}`}
                      disabled={!transaction.auditFactName}
                      onClick={() => transaction.auditFactName && selectPortfolioCell(transaction.ticker, transaction.auditFactName, "portfolio_transaction")}
                    >
                      {transaction.price}
                    </button>
                  </td>
                  <td>
                    <span className={`source-state-badge ${transaction.auditFactName ? "ok" : "blocked"}`}>
                      {transaction.auditFactName ? "Source traced" : "No source_trace"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <em>These CSV transactions are also rendered as buy/sell markers on the valuation map for the active ticker.</em>
        </div>
        <SelectedAuditTrace
          row={selectedPortfolioAuditRow}
          fallbackTrace={selectedHolding?.source_trace ?? portfolio.import_trace ?? portfolio.source_trace}
          fallbackLabel={selectedPortfolioCell.namespace === "portfolio_transaction" ? selectedPortfolioCell.factName : `portfolio.${selectedPortfolioCell.factName}`}
        />
      </div>
    </section>
  );
}

function portfolioTransactionTimeline(portfolio: PortfolioSummary, transactionAuditRows: AuditRow[]) {
  return portfolio.holdings
    .flatMap((holding) =>
      holding.transactions.map((transaction) => ({
        ...transaction,
        ticker: holding.ticker,
        auditFactName: transactionAuditRows.find((row) =>
          row.fact_name?.startsWith(`portfolio_transaction.${transaction.date}.${transaction.side}.`) &&
          row.fact_name.endsWith(".price")
        )?.fact_name
      }))
    )
    .sort((left, right) => `${left.date}-${left.ticker}`.localeCompare(`${right.date}-${right.ticker}`));
}

function portfolioP1States(portfolio: PortfolioSummary, importStatus: string) {
  const traceFlags = [
    ...(Array.isArray(portfolio.import_trace?.flags) ? portfolio.import_trace.flags.map(String) : []),
    ...(Array.isArray(portfolio.source_trace?.flags) ? portfolio.source_trace.flags.map(String) : []),
    ...portfolio.holdings.flatMap((holding) => Array.isArray(holding.source_trace?.flags) ? holding.source_trace.flags.map(String) : [])
  ].map((flag) => flag.toLowerCase());
  const missingPrice = portfolio.holdings.some((holding) => !holding.latest_price || holding.latest_price === "-");
  const unmatchedTicker = traceFlags.some((flag) => flag.includes("unmatched"));
  const staleFx = traceFlags.some((flag) => flag.includes("stale_fx") || flag.includes("stale fx"));
  const invalidCsv = importStatus.toLowerCase().includes("error");

  return [
    {
      label: invalidCsv ? "invalid CSV" : "CSV parse ready",
      tone: invalidCsv ? "danger" : "ok"
    },
    {
      label: unmatchedTicker ? "unmatched ticker" : "tickers matched",
      tone: unmatchedTicker ? "warning" : "ok"
    },
    {
      label: missingPrice ? "missing price" : "prices loaded",
      tone: missingPrice ? "warning" : "ok"
    },
    {
      label: staleFx ? "stale FX" : "FX current",
      tone: staleFx ? "warning" : "ok"
    }
  ];
}

function PortfolioAuditCellButton({
  row,
  factName,
  onSelect,
  children
}: {
  row: PortfolioSummary["holdings"][number];
  factName: string;
  onSelect: (ticker: string, factName: string) => void;
  children: ReactNode;
}) {
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`portfolio-audit-cell-${row.ticker}-${auditTestIdPart(factName)}`}
      aria-label={`Audit portfolio ${row.ticker} ${factName}`}
      onClick={() => onSelect(row.ticker, factName)}
    >
      {children}
    </button>
  );
}
