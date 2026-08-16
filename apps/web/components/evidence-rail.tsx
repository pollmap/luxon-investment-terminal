"use client";

import { AlertTriangle } from "lucide-react";
import type {
  AdjustedRow,
  KrValuationCacheCoverage,
  KrValuationCacheUniverseCoverage,
  PriorityUniverse,
  SourceCoverage,
  SourceCoverageRemediationAction,
  SourceCoverageTicker,
  SourceReadiness
} from "../lib/terminal-types";
import { GraphKeyLedger } from "./graph-key-ledger";
import type { GraphKeyLedgerItem } from "./graph-key-ledger";
import { Metric } from "./terminal-primitives";
import { sourceDocumentHref } from "../lib/audit-utils";

export function EvidenceRail({
  selected,
  warnings,
  s1Count,
  s2Count,
  s4Count,
  readiness,
  krCacheCoverage,
  krCacheUniverse,
  missingRequiredNames,
  coverage,
  priorityUniverse,
  currentCoverage,
  minForecastYears,
  currentBaseForecastYears,
  currentBaseForecastSnapshots,
  graphKeyItems,
  factQueryString,
  buildFactHref,
  onInspectAuditFact,
  onInspectGraphKeyFact,
  onSelectTicker
}: {
  selected?: AdjustedRow;
  warnings: string[];
  s1Count: number;
  s2Count: number;
  s4Count: number;
  readiness: SourceReadiness;
  krCacheCoverage: KrValuationCacheCoverage | null;
  krCacheUniverse: KrValuationCacheUniverseCoverage | null;
  missingRequiredNames: string[];
  coverage: SourceCoverage;
  priorityUniverse: PriorityUniverse;
  currentCoverage?: SourceCoverageTicker;
  minForecastYears: number;
  currentBaseForecastYears: number;
  currentBaseForecastSnapshots: number;
  graphKeyItems: GraphKeyLedgerItem[];
  factQueryString: string;
  buildFactHref: (factId: string, queryString?: string) => string;
  onInspectAuditFact?: (factId: string) => void;
  onInspectGraphKeyFact?: (factId: string) => void;
  onSelectTicker?: (ticker: string) => void;
}) {
  const krUniverseReadyPct = readinessPercent(
    krCacheUniverse?.summary.valuation_ready ?? 0,
    krCacheUniverse?.summary.tickers_expected ?? 0
  );
  const krUniverseGateLabel = krCacheUniverse
    ? krCacheUniverse.summary.valuation_ready === krCacheUniverse.summary.tickers_expected
      ? "KR Top10 deploy-ready"
      : `${krCacheUniverse.summary.tickers_expected - krCacheUniverse.summary.valuation_ready} ticker gaps before deploy`
    : "KR Top10 cache not loaded";
  const krTop10Matrix = krCacheUniverse ? krTop10CompletionMatrix(krCacheUniverse, coverage) : null;
  const krTop10PartialRows = krCacheUniverse ? krTop10PartialGapRows(krCacheUniverse) : [];
  const priorityRankLimit = priorityUniverse.rank_limit ?? priorityUniverse.tickers.length;
  const priorityRankCount = priorityUniverse.rank_count ?? priorityUniverse.tickers.length;
  const priorityRankPct = readinessPercent(priorityRankCount, priorityRankLimit);
  const priorityRankGateLabel =
    priorityRankCount >= priorityRankLimit
      ? "rank gate complete"
      : `${Math.max(priorityRankLimit - priorityRankCount, 0)} rank slots pending`;
  const currentMissingRequirements = currentCoverage?.missing_required ?? [];
  const currentTickerAction = currentCoverage
    ? coverage.remediation.next_actions.find((action) => action.tickers.includes(currentCoverage.ticker))
    : undefined;
  const currentForecastRequired = coverage.requirements.consensus_forecast_required;
  const currentTickerGateReady = Boolean(
    currentCoverage?.core_ready && (!currentForecastRequired || currentCoverage.consensus_forecast_ready)
  );
  const currentTickerGateLabel = currentCoverage
    ? currentTickerGateReady
      ? `${currentCoverage.ticker} source-ready`
      : `${currentCoverage.ticker} source gate blocked`
    : "Current ticker not tracked";
  const localForecastOverlayReady = Boolean(currentCoverage?.local_consensus_overlay_ready);
  const productionDbPending = localForecastOverlayReady && !coverage.postgres.reachable;
  const forecastOverlayLabel = localForecastOverlayReady
    ? productionDbPending
      ? "Local CSV forecast overlay ready / production DB pending"
      : "Source-backed forecast overlay ready"
    : "Forecast overlay pending";
  const forecastPreflight = coverage.remediation.forecast_csv_preflight;
  const forecastPreflightReady = Boolean(forecastPreflight?.import_ready_candidate);
  const forecastPreflightLabel = forecastPreflight
    ? forecastPreflightReady
      ? "Forecast CSV candidate ready"
      : `Forecast CSV ${forecastPreflight.status.replace(/_/g, " ")}`
    : "Forecast CSV preflight unavailable";
  const completionGate = sourceCompletionGate({
    ticker: currentCoverage?.ticker,
    market: currentCoverage?.market ?? null,
    years: coverage.remediation.years ?? "2020:2025",
    currentAction: currentTickerAction,
    currentCoverage,
    krCacheCoverage,
    requireConsensusForecast: coverage.requirements.consensus_forecast_required
  });
  const krWarehouseReady = krCacheCoverage?.data_backend === "kr_valuation_warehouse" && krCacheCoverage.valuation_ready;
  const krSelectedTicker = currentCoverage?.ticker ?? "005930.KS";
  const krWarehouseLoadCommand =
    krSelectedTicker === "005930.KS"
      ? "pnpm load:valuation-warehouse:kr:005930"
      : `python -m services.ingestion_worker.cli load-kr-valuation-warehouse --tickers ${krSelectedTicker} --strict`;
  const krWarehouseViews = krCacheCoverage?.warehouse_views ?? {};

  return (
    <aside className="audit-panel">
      <section>
        <h2>Source Evidence Rail</h2>
        <p className="audit-panel-note">No source_trace = reject display. AI commentary cannot create financial values.</p>
        <Metric label="Method" value={selected?.method ?? "pending"} />
        <Metric label="Confidence" value={selected ? `${Math.round(Number(selected.confidence) * 100)}%` : "-"} />
        <Metric label="S1/S2/S4 periods" value={`${s1Count}/${s2Count}/${s4Count}`} />
        <Metric label="Source readiness" value={readiness.status} />
        <Metric
          label="Source rows"
          value={`${readiness.postgres.counts.adjusted_earnings ?? 0} EPS / ${readiness.postgres.counts.price_bars ?? 0} prices`}
        />
        {krCacheCoverage ? (
          <div
            className={`kr-cache-coverage ${krWarehouseReady ? "complete" : krCacheCoverage.coverage_status ?? "unknown"}`}
            data-testid="kr-cache-coverage"
          >
            <div>
              <strong>{krWarehouseReady ? "KR valuation warehouse" : "KR valuation cache"}</strong>
              <span data-testid="kr-cache-coverage-status">{formatCoverageStatus(krCacheCoverage.coverage_status)}</span>
            </div>
            <div className="kr-cache-source-gate">
              <span
                className={`source-state-badge ${krFinancialNumbersAllowed(krCacheCoverage) ? "ok" : "blocked"}`}
                data-testid="kr-cache-financial-numbers"
              >
                Numbers {krFinancialNumbersAllowed(krCacheCoverage) ? "allowed" : "blocked"}
              </span>
              <em>{krCacheCoverage.data_backend ?? "source-backed cache"}</em>
            </div>
            <div className="kr-cache-coverage-grid">
              <span>
                <strong>Ready</strong>
                {krCacheCoverage.valuation_ready ? "yes" : "no"}
              </span>
              <span>
                <strong>Full</strong>
                {krCacheCoverage.full_coverage_ready ? "yes" : "no"}
              </span>
              <span>
                <strong>Points</strong>
                {formatYearList(krCacheCoverage.coverage_years.valuation_points)}
              </span>
              <span>
                <strong>Missing</strong>
                {formatMissingYears(krCacheCoverage)}
              </span>
            </div>
            <div className="kr-warehouse-proof" data-testid="kr-warehouse-proof">
              <span>
                <strong>Backend</strong>
                {krCacheCoverage.data_backend ?? "kr valuation input cache"}
              </span>
              <span>
                <strong>Warehouse DB</strong>
                {krCacheCoverage.warehouse_db_path ?? "pending load"}
              </span>
              <span>
                <strong>Views</strong>
                {krWarehouseViews.normalized_facts && krWarehouseViews.valuation_points
                  ? `${krWarehouseViews.normalized_facts} / ${krWarehouseViews.valuation_points}`
                  : "pending views"}
              </span>
              <span>
                <strong>Rejected rows</strong>
                {String(krCacheCoverage.rejected_warehouse_rows ?? krCacheCoverage.rejected_cache_points ?? 0)}
              </span>
              <code data-testid="kr-warehouse-load-command">{krWarehouseLoadCommand}</code>
            </div>
            {krCacheCoverage.quality_flags.length ? (
              <p data-testid="kr-cache-quality-flags">{krCacheCoverage.quality_flags.join(", ")}</p>
            ) : null}
            {krCacheCoverage.market_gap_diagnostics.length ? (
              <p data-testid="kr-cache-market-gap-diagnostics">{formatMarketGapDiagnostics(krCacheCoverage)}</p>
            ) : null}
            {krCacheCoverage.financial_gap_diagnostics.length ? (
              <p data-testid="kr-cache-gap-diagnostics">{formatGapDiagnostics(krCacheCoverage)}</p>
            ) : null}
          </div>
        ) : null}
        {krCacheUniverse ? (
          <div className={`kr-cache-universe ${krCacheUniverse.coverage_status}`} data-testid="kr-cache-universe">
            <div>
              <strong>KR Top 10 valuation cache</strong>
              <span>{formatCoverageStatus(krCacheUniverse.coverage_status)}</span>
            </div>
            <div className="kr-cache-universe-stats">
              <span data-testid="kr-cache-universe-ready">
                <strong>{krCacheUniverse.summary.valuation_ready}/{krCacheUniverse.summary.tickers_expected}</strong>
                valuation-ready
              </span>
              <span data-testid="kr-cache-universe-complete">
                <strong>{krCacheUniverse.summary.complete}</strong>
                complete
              </span>
              <span data-testid="kr-cache-universe-partial">
                <strong>{krCacheUniverse.summary.partial_source_backed}</strong>
                partial
              </span>
              <span data-testid="kr-cache-universe-missing">
                <strong>{krCacheUniverse.summary.missing}</strong>
                missing
              </span>
            </div>
            <div className="kr-cache-universe-readiness" data-testid="kr-cache-universe-readiness">
              <div>
                <strong>{krUniverseGateLabel}</strong>
                <span>{krUniverseReadyPct}% source-backed valuation readiness</span>
              </div>
              <meter
                min={0}
                max={100}
                value={krUniverseReadyPct}
                aria-label="KR Top10 source-backed valuation readiness"
              />
            </div>
            <div className="kr-cache-universe-list" data-testid="kr-cache-universe-rows">
              {krCacheUniverse.rows.slice(0, 10).map((row) => (
                <span key={row.ticker} className={row.valuation_ready ? "ready" : "missing"}>
                  <strong>{row.ticker}</strong>
                  {formatCoverageStatus(row.coverage_status)}
                  {row.valuation_years.length ? ` / ${formatYearList(row.valuation_years)}` : ""}
                </span>
              ))}
            </div>
            {krTop10PartialRows.length ? (
              <div className="kr-top10-partial-gaps" data-testid="kr-top10-partial-gaps">
                <div className="kr-top10-partial-header">
                  <strong>Partial source-backed gap ledger</strong>
                  <span>{krTop10PartialRows.length} tickers need source notes</span>
                </div>
                {krTop10PartialRows.map((row) => (
                  <div key={row.ticker} className={`kr-top10-partial-row ${row.status}`}>
                    <strong>{row.ticker}</strong>
                    <span>
                      <em>Market</em>
                      {row.market}
                    </span>
                    <span>
                      <em>Metric</em>
                      {row.metric}
                    </span>
                    <span>
                      <em>Diagnostics</em>
                      {row.diagnostics}
                    </span>
                    <p>{row.action}</p>
                    {row.ticker !== krSelectedTicker && onSelectTicker ? (
                      <button
                        type="button"
                        className="inline-secondary-action"
                        data-testid={`kr-top10-partial-focus-${row.ticker}`}
                        onClick={() => onSelectTicker(row.ticker)}
                      >
                        Focus ticker
                      </button>
                    ) : null}
                    <div className="kr-top10-partial-audit-links">
                      {row.auditFacts.map((fact) => (
                        <span key={fact.factId}>
                          <em>{fact.label}</em>
                          <a href={buildFactHref(fact.factId, factQueryString)} target="_blank" rel="noreferrer">
                            Open fact
                          </a>
                          {fact.sourceDocumentId ? (
                            <a href={sourceDocumentHref(fact.sourceDocumentId)} target="_blank" rel="noreferrer">
                              Open source doc
                            </a>
                          ) : null}
                          {fact.factId.startsWith(`${krSelectedTicker}-`) ? (
                            <button
                              type="button"
                              onClick={() => onInspectAuditFact?.(fact.factId)}
                              disabled={!onInspectAuditFact}
                            >
                              Open Data Audit
                            </button>
                          ) : (
                            <small>cross-ticker</small>
                          )}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
            <p data-testid="kr-cache-universe-source-doc">
              {String(krCacheUniverse.source_trace.source_document_id ?? "source_trace_required")}
            </p>
            {krTop10Matrix ? (
              <div className="kr-top10-completion-matrix" data-testid="kr-top10-completion-matrix">
                <div className="kr-top10-completion-header">
                  <strong>KR Top10 completion matrix</strong>
                  <span>{formatCoverageStatus(krTop10Matrix.status)}</span>
                </div>
                <div className="kr-top10-stage-grid" data-testid="kr-top10-stage-grid">
                  <span>
                    <strong>{krTop10Matrix.cacheReady}/{krTop10Matrix.expected}</strong>
                    build/cache
                  </span>
                  <span>
                    <strong>{krTop10Matrix.valuationReady}/{krTop10Matrix.expected}</strong>
                    valuation rows
                  </span>
                  <span>
                    <strong>{krTop10Matrix.warehouseReady}/{krTop10Matrix.expected}</strong>
                    warehouse load
                  </span>
                  <span>
                    <strong>{krTop10Matrix.apiReady}/{krTop10Matrix.expected}</strong>
                    API allowed
                  </span>
                  <span>
                    <strong>{krTop10Matrix.productionCoreReady}/{krTop10Matrix.expected}</strong>
                    production DB
                  </span>
                  <span>
                    <strong>{krTop10Matrix.forecastReady}/{krTop10Matrix.expected}</strong>
                    forecast 1Y-5Y
                  </span>
                </div>
                <p className={`kr-top10-production-gate ${krTop10Matrix.productionStatus}`} data-testid="kr-top10-production-gate">
                  {krTop10Matrix.productionGateLabel}
                </p>
                <div className="kr-top10-matrix-rows" data-testid="kr-top10-completion-rows">
                  {krTop10Matrix.rows.map((row) => (
                    <div key={row.ticker} className={row.status}>
                      <strong>{row.ticker}</strong>
                      <span>{row.build}</span>
                      <span>{row.load}</span>
                      <span>{row.api}</span>
                      <span>{row.db}</span>
                      <span>{row.forecast}</span>
                      <em>{row.gaps}</em>
                    </div>
                  ))}
                </div>
                <div className="kr-top10-command-stack" data-testid="kr-top10-completion-command">
                  <code>pnpm e2e:source:kr:top10:local-dry-run</code>
                  <code>pnpm build:valuation-inputs:kr:top10</code>
                  <code>pnpm load:valuation-warehouse:kr:top10</code>
                  <code>python -m services.ingestion_worker.cli run-priority-e2e --markets KR --years 2020:2025 --persist --continue-on-error --strict</code>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
        {missingRequiredNames.length ? (
          <div className="warnings">
            <AlertTriangle size={14} />
            {missingRequiredNames.join(", ")}
          </div>
        ) : null}
      </section>

      <section>
        <h2>MVP Source Coverage</h2>
        <Metric label="Core ready" value={`${coverage.summary.core_ready}/${coverage.summary.tickers_expected}`} />
        <Metric label="Forecast ready" value={`${coverage.summary.consensus_forecast_ready}/${coverage.summary.tickers_expected}`} />
        <Metric
          label="Base forecast EPS"
          value={`${currentBaseForecastYears}/${minForecastYears} FY / ${currentBaseForecastSnapshots} snapshots`}
        />
        <Metric label="Current ticker" value={currentCoverage ? `${currentCoverage.status} / ${currentCoverage.pattern}` : "not tracked"} />
        <div
          className={`current-source-gate ${currentTickerGateReady ? "ready" : "blocked"}`}
          data-testid="current-source-gate"
        >
          <div>
            <strong>{currentTickerGateLabel}</strong>
            <span data-testid="current-source-gate-readiness">
              core {currentCoverage?.core_ready ? "ready" : "pending"} / forecast{" "}
              {currentCoverage?.consensus_forecast_ready ? "ready" : "pending"}
            </span>
            <span data-testid="current-source-gate-forecast-overlay">
              {forecastOverlayLabel}
            </span>
          </div>
          <p data-testid="current-source-gate-missing">
            {currentMissingRequirements.length
              ? `Missing ${currentMissingRequirements.map((item) => item.replace(/_/g, " ")).join(", ")}`
              : currentCoverage
                ? "No required source gaps"
                : "Open a tracked KR/US/JP priority ticker to inspect source gates."}
          </p>
          {currentTickerAction ? (
            <code data-testid="current-source-gate-command">
              {currentTickerAction.cli_commands[0] ?? "manual source-backed import required"}
            </code>
          ) : null}
        </div>
        {forecastPreflight ? (
          <div
            className={`forecast-preflight-card ${forecastPreflightReady ? "ready" : "blocked"}`}
            data-testid="forecast-csv-preflight"
            aria-label="Forecast CSV preflight"
          >
            <div>
              <strong data-testid="forecast-csv-preflight-status">{forecastPreflightLabel}</strong>
              <span>{forecastPreflight.path}</span>
            </div>
            <p>
              Ready {forecastPreflight.ready_rows}/{forecastPreflight.required_periods} rows | covered{" "}
              {forecastPreflight.covered_periods}/{forecastPreflight.required_periods} FY | values missing{" "}
              {forecastPreflight.missing_value_rows} | trace missing {forecastPreflight.missing_trace_rows} | manual notes missing{" "}
              {forecastPreflight.missing_manual_notes_rows}
            </p>
            <p>
              External consensus {forecastPreflight.external_consensus_ready_rows} | manual assumption{" "}
              {forecastPreflight.manual_assumption_ready_rows} | invalid values {forecastPreflight.invalid_value_rows} | blocked evidence{" "}
              {forecastPreflight.blocked_evidence_rows}
            </p>
            {forecastPreflight.missing_periods.length ? (
              <p>
                Missing FY{" "}
                {forecastPreflight.missing_periods
                  .slice(0, 5)
                  .map(
                    (period) =>
                      `${period.ticker} FY${period.fiscal_year} (${period.estimate_cases_allowed.join("/")})`
                  )
                  .join(" | ")}
                {forecastPreflight.missing_periods.length > 5
                  ? ` | +${forecastPreflight.missing_periods.length - 5} more`
                  : ""}
              </p>
            ) : null}
            {forecastPreflight.error ? <em>{forecastPreflight.error}</em> : null}
            <code>{forecastPreflight.strict_validator}</code>
          </div>
        ) : null}
        <div
          className={`completion-gate-card ${completionGate.status}`}
          data-testid="e2e-completion-gate"
          aria-label="E2E completion gate"
        >
          <div className="completion-gate-header">
            <div>
              <strong>E2E Completion Gate</strong>
              <span data-testid="e2e-completion-status">{formatCompletionStatus(completionGate.status)}</span>
            </div>
            <em>{completionGate.market} / {completionGate.ticker}</em>
          </div>
          <p data-testid="e2e-completion-local-status">{completionGate.localStatus}</p>
          <div className="completion-proof-list" data-testid="e2e-completion-required-proofs">
            {completionGate.requiredProofs.map((proof) => (
              <span key={proof}>{proof}</span>
            ))}
          </div>
          <div className="completion-command-list" data-testid="e2e-completion-commands">
            {completionGate.localCommands.map((command) => (
              <div key={command.id}>
                <strong>{command.id.replace(/_/g, " ")}</strong>
                <code>{command.command}</code>
                <span>{command.proves}</span>
              </div>
            ))}
          </div>
          <div className="completion-deploy-command" data-testid="e2e-completion-deployment-command">
            <strong>Deployment proof</strong>
            <code>{completionGate.deploymentCommand.command}</code>
            <span>{completionGate.deploymentCommand.requires}</span>
          </div>
        </div>
        <div className="priority-universe-card" data-testid="priority-universe-contract">
          <div>
            <strong>{priorityUniverse.label}</strong>
            <span data-testid="priority-universe-count">
              {priorityUniverse.tickers.length} tickers / {priorityUniverse.data_mode}
            </span>
          </div>
          <p>{priorityUniverse.note}</p>
          <div className="priority-universe-meta">
            <span data-testid="priority-universe-source-doc">
              {String(priorityUniverse.source_trace.source_document_id ?? "source_trace_required")}
            </span>
            <span data-testid="priority-universe-rank-label">
              {priorityRankLabel(priorityUniverse)}
            </span>
            <span data-testid="priority-universe-rank-coverage">
              Rank coverage: {formatRankStatus(priorityUniverse.rank_coverage_status)} ({priorityUniverse.rank_count ?? 0}/{priorityUniverse.rank_limit ?? priorityUniverse.tickers.length})
            </span>
            {(priorityUniverse.missing_rank_slots ?? 0) > 0 ? (
              <span data-testid="priority-universe-rank-missing">
                Missing rank slots {priorityUniverse.missing_rank_slots}
              </span>
            ) : null}
          </div>
          <div className="priority-universe-readiness" data-testid="priority-universe-readiness">
            <div>
              <strong>{priorityRankGateLabel}</strong>
              <span>{priorityRankPct}% market-cap rank evidence</span>
            </div>
            <meter
              min={0}
              max={100}
              value={priorityRankPct}
              aria-label="Priority universe market-cap rank evidence readiness"
            />
          </div>
          <div className="priority-universe-list" role="list" aria-label="Priority universe contract rows">
            {priorityUniverse.tickers.map((row) => (
              <div
                key={row.ticker}
                role="listitem"
                className="priority-universe-row"
                data-testid={`priority-universe-row-${row.ticker}`}
              >
                <strong>{row.market_cap_rank ?? row.coverage_priority_order}</strong>
                <span>{row.ticker}</span>
                <em data-testid={`priority-universe-rank-policy-${row.ticker}`}>
                  {row.market} / {row.rank_policy.replace(/_/g, " ")}
                </em>
              </div>
            ))}
          </div>
        </div>
        <div className="coverage-gap-list" aria-label="Source coverage gaps" data-testid="source-coverage-gaps">
          {Object.entries(coverage.summary.missing_by_requirement).length ? (
            Object.entries(coverage.summary.missing_by_requirement).map(([requirement, tickers]) => (
              <span key={requirement}>
                <strong>{requirement.replace(/_/g, " ")}</strong>
                {tickers.join(", ")}
              </span>
            ))
          ) : (
            <span><strong>requirements</strong>ready</span>
          )}
        </div>
        {coverage.remediation.next_actions.length ? (
          <div className="coverage-action-list" aria-label="Source coverage remediation actions" data-testid="source-coverage-actions">
            <h3>Next ingestion actions</h3>
            {coverage.remediation.next_actions.slice(0, 4).map((action) => (
              <div key={`${action.id}-${action.tickers.join("-")}`} className="coverage-action">
                <div>
                  <strong>{action.id.replace(/_/g, " ")}</strong>
                  <span>{action.tickers.join(", ")}</span>
                </div>
                <p>{action.description}</p>
                <code>{action.cli_commands[0] ?? "manual source-backed import required"}</code>
              </div>
            ))}
          </div>
        ) : null}
        <div className="coverage-list" role="list" aria-label="Priority universe source coverage" data-testid="source-coverage-list">
          {coverage.tickers.map((row) => (
            <div
              key={row.ticker}
              role="listitem"
              data-testid={`source-coverage-row-${row.ticker}`}
              className={`coverage-row ${row.core_ready ? "ready" : "missing"}`}
            >
              <div>
                <strong>{row.ticker}</strong>
                <span>{row.pattern.replace(/_/g, " ")}</span>
              </div>
              <div>
                <span>{row.counts.adjusted_years ?? 0} EPSY</span>
                <span>{row.counts.price_years ?? 0} PxY</span>
                <span data-testid={`source-coverage-base-forecast-${row.ticker}`}>
                  Base EPS {row.counts.consensus_valuation_years ?? 0}/{minForecastYears}Y
                </span>
                <span>All est {row.counts.consensus_forecast_years ?? 0}Y</span>
              </div>
              <em>{row.core_ready ? "ready" : row.missing_required.join(", ") || "missing"}</em>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>Graph Key</h2>
        <GraphKeyLedger
          items={graphKeyItems}
          factQueryString={factQueryString}
          buildFactHref={buildFactHref}
          onInspectFact={onInspectGraphKeyFact}
        />
      </section>

      <section>
        <h2>Adjusted EPS Audit</h2>
        <div className="audit-card">
          <strong>{selected?.fiscal_year ?? "-"} EPS</strong>
          <div className="audit-values">
            <span>GAAP {selected?.gaap_eps_diluted ?? "-"}</span>
            <span>Adjusted {selected?.adjusted_eps ?? "-"}</span>
          </div>
          <p>{selected?.source_trace?.source_url ?? selected?.source_trace?.source_type ?? "Source trace pending"}</p>
          {warnings.length ? (
            <div className="warnings">
              <AlertTriangle size={14} />
              {warnings.join(", ")}
            </div>
          ) : null}
        </div>
        <table className="waterfall-table">
          <thead>
            <tr>
              <th>Step</th>
              <th>Impact</th>
              <th>EPS</th>
            </tr>
          </thead>
          <tbody>
            {(selected?.waterfall ?? []).map((step) => (
              <tr key={`${step.label}-${step.category}`}>
                <td>{step.label}</td>
                <td>{step.after_tax_impact ?? "-"}</td>
                <td>{step.eps_impact ? Number(step.eps_impact).toFixed(2) : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </aside>
  );
}

type CompletionCommand = {
  id: string;
  command: string;
  proves: string;
};

type CompletionGate = {
  status: string;
  ticker: string;
  market: string;
  localStatus: string;
  requiredProofs: string[];
  localCommands: CompletionCommand[];
  deploymentCommand: CompletionCommand & { requires: string };
};

type KrTop10CompletionMatrix = {
  status: string;
  expected: number;
  cacheReady: number;
  valuationReady: number;
  warehouseReady: number;
  apiReady: number;
  productionCoreReady: number;
  forecastReady: number;
  productionReady: number;
  productionStatus: string;
  productionGateLabel: string;
  rows: Array<{
    ticker: string;
    status: string;
    build: string;
    load: string;
    api: string;
    db: string;
    forecast: string;
    gaps: string;
  }>;
};

type KrTop10PartialGapRow = {
  ticker: string;
  status: string;
  market: string;
  metric: string;
  diagnostics: string;
  action: string;
  auditFacts: Array<{
    label: string;
    factId: string;
    status?: string;
    sourceDocumentId?: string | null;
  }>;
};

function krTop10PartialGapRows(universe: KrValuationCacheUniverseCoverage): KrTop10PartialGapRow[] {
  return universe.rows
    .filter((row) => {
      const hasMissingYears = row.missing_years.market_input.length || row.missing_years.financial_metric.length;
      const hasDiagnostics = row.market_gap_count || row.financial_gap_count || row.rejected_cache_points;
      return row.coverage_status === "partial_source_backed" || !row.full_coverage_ready || Boolean(hasMissingYears || hasDiagnostics);
    })
    .slice(0, 10)
    .map((row) => {
      const marketMissing = row.missing_years.market_input;
      const metricMissing = row.missing_years.financial_metric;
      const diagnosticCount = row.market_gap_count + row.financial_gap_count + row.rejected_cache_points;
      return {
        ticker: row.ticker,
        status: row.valuation_ready ? "partial" : "blocked",
        market: marketMissing.length ? `missing ${formatYearList(marketMissing)}` : "covered",
        metric: metricMissing.length ? `missing ${formatYearList(metricMissing)}` : "covered",
        diagnostics: diagnosticCount ? `${diagnosticCount} source notes` : "none",
        action: krTop10PartialAction(row),
        auditFacts: krTop10PartialAuditFacts(row)
      };
    });
}

function krTop10PartialAuditFacts(row: KrValuationCacheUniverseCoverage["rows"][number]) {
  const apiFacts = row.gap_audit_refs
    .filter((fact) => fact.factId)
    .map((fact) => ({
      label: fact.label ?? krGapFactLabel(fact.scope, fact.fiscalYear),
      factId: fact.factId,
      status: fact.status,
      sourceDocumentId: fact.sourceDocumentId
    }));
  if (apiFacts.length) {
    return apiFacts.slice(0, 4);
  }

  const facts: Array<{ label: string; factId: string; status?: string; sourceDocumentId?: string | null }> = [];
  for (const year of row.missing_years.market_input) {
    facts.push({
      label: `Market FY${year}`,
      factId: `${row.ticker}-${year}-data_quality.kr_market_gap.source_no_rows_before_first_trade`
    });
  }
  for (const year of row.missing_years.financial_metric) {
    facts.push({
      label: `Metric FY${year}`,
      factId: `${row.ticker}-${year}-data_quality.kr_financial_gap.source_no_data`
    });
  }
  if (!facts.length && row.rejected_cache_points) {
    const fallbackYear = row.valuation_years.at(-1) ?? 0;
    facts.push({
      label: "Rejected cache",
      factId: `${row.ticker}-${fallbackYear}-data_quality.kr_cache_gap.rejected_cache_points`
    });
  }
  return facts.slice(0, 4);
}

function krGapFactLabel(scope: string, fiscalYear?: number) {
  const prefix = scope === "financial" ? "Metric" : scope === "market" ? "Market" : "Gap";
  return fiscalYear ? `${prefix} FY${fiscalYear}` : prefix;
}

function krTop10PartialAction(row: KrValuationCacheUniverseCoverage["rows"][number]) {
  const marketMissing = row.missing_years.market_input.length > 0;
  const metricMissing = row.missing_years.financial_metric.length > 0;
  if (marketMissing && metricMissing) {
    return "Keep partial early-history coverage or add alternate market and OpenDART source evidence.";
  }
  if (marketMissing) {
    return "Keep partial market-history start or add alternate KRX market evidence.";
  }
  if (metricMissing) {
    return "Keep partial financial coverage or add alternate OpenDART financial evidence.";
  }
  if (row.rejected_cache_points) {
    return "Fix rejected cache rows before production display.";
  }
  return row.source_note || "Review source diagnostics before treating as full coverage.";
}

function krTop10CompletionMatrix(
  universe: KrValuationCacheUniverseCoverage,
  coverage: SourceCoverage
): KrTop10CompletionMatrix {
  const isWarehouse = universe.data_backend === "kr_valuation_warehouse";
  const expected = universe.summary.tickers_expected || universe.rows.length;
  const matrixRows = universe.rows.slice(0, 10);
  const coverageByTicker = new Map(coverage.tickers.map((row) => [row.ticker, row]));
  const forecastRequired = coverage.requirements.consensus_forecast_required;
  const postgresReachable = coverage.postgres.reachable;
  const localWarehouseOnly = !postgresReachable && coverage.data_mode === "local_source_backed_warehouse";
  const productionCoreReady = matrixRows.filter((row) => coverageByTicker.get(row.ticker)?.core_ready).length;
  const forecastReady = matrixRows.filter((row) => coverageByTicker.get(row.ticker)?.consensus_forecast_ready).length;
  const productionReady = matrixRows.filter((row) => {
    const tickerCoverage = coverageByTicker.get(row.ticker);
    if (!tickerCoverage?.core_ready) {
      return false;
    }
    return !forecastRequired || tickerCoverage.consensus_forecast_ready;
  }).length;
  const productionStatus = localWarehouseOnly
    ? "local_warehouse_only"
    : !postgresReachable
    ? "postgres_missing"
    : productionReady >= expected
      ? "production_ready"
      : "production_partial";
  return {
    status: universe.coverage_status,
    expected,
    cacheReady: universe.summary.cache_files_found,
    valuationReady: universe.summary.valuation_ready,
    warehouseReady: isWarehouse ? universe.summary.valuation_ready : 0,
    apiReady: universe.summary.financial_numbers_allowed,
    productionCoreReady,
    forecastReady,
    productionReady,
    productionStatus,
    productionGateLabel: krTop10ProductionGateLabel({
      expected,
      productionReady,
      postgresReachable,
      localWarehouseOnly,
      forecastRequired
    }),
    rows: matrixRows.map((row) => {
      const tickerCoverage = coverageByTicker.get(row.ticker);
      return {
        ticker: row.ticker,
        status: row.full_coverage_ready ? "complete" : row.valuation_ready ? "partial" : "missing",
        build: row.cache_found ? "build ok" : "build pending",
        load: row.valuation_ready ? (isWarehouse ? "warehouse ok" : "cache ready") : "load pending",
        api: row.financial_numbers_allowed ? "API allowed" : "API blocked",
        db: krTop10DbLabel(tickerCoverage, postgresReachable, localWarehouseOnly),
        forecast: krTop10ForecastLabel(tickerCoverage, forecastRequired),
        gaps: krTop10GapLabel(row, tickerCoverage)
      };
    })
  };
}

function krTop10ProductionGateLabel({
  expected,
  productionReady,
  postgresReachable,
  localWarehouseOnly,
  forecastRequired
}: {
  expected: number;
  productionReady: number;
  postgresReachable: boolean;
  localWarehouseOnly: boolean;
  forecastRequired: boolean;
}) {
  if (localWarehouseOnly) {
    return "Local warehouse proof ready; Neon/Postgres promotion required";
  }
  if (!postgresReachable) {
    return "Neon/Postgres source coverage not connected";
  }
  if (productionReady >= expected) {
    return forecastRequired
      ? "production gate ready with 1Y-5Y forecast evidence"
      : "production gate ready";
  }
  return `${Math.max(expected - productionReady, 0)} production DB/forecast gaps before deploy`;
}

function krTop10DbLabel(tickerCoverage: SourceCoverageTicker | undefined, postgresReachable: boolean, localWarehouseOnly: boolean) {
  if (localWarehouseOnly) {
    return "local warehouse only";
  }
  if (!postgresReachable) {
    return "DB not connected";
  }
  if (!tickerCoverage) {
    return "DB missing";
  }
  return tickerCoverage.core_ready ? "DB core ok" : `DB gaps ${tickerCoverage.missing_required.length}`;
}

function krTop10ForecastLabel(tickerCoverage: SourceCoverageTicker | undefined, forecastRequired: boolean) {
  if (!forecastRequired) {
    return "forecast optional";
  }
  return tickerCoverage?.consensus_forecast_ready ? "forecast ok" : "forecast pending";
}

function krTop10GapLabel(
  row: KrValuationCacheUniverseCoverage["rows"][number],
  tickerCoverage?: SourceCoverageTicker
) {
  const missingMarket = row.missing_years.market_input.length;
  const missingMetric = row.missing_years.financial_metric.length;
  const diagnosticGaps = row.market_gap_count + row.financial_gap_count + row.rejected_cache_points;
  const dbGaps = tickerCoverage?.missing_required.length ?? 0;
  if (!missingMarket && !missingMetric && !diagnosticGaps && !dbGaps) {
    return "no gaps";
  }
  return [
    missingMarket ? `market ${missingMarket}` : "",
    missingMetric ? `metric ${missingMetric}` : "",
    diagnosticGaps ? `diagnostics ${diagnosticGaps}` : "",
    dbGaps ? `DB ${dbGaps}` : ""
  ].filter(Boolean).join(" / ");
}

function sourceCompletionGate({
  ticker,
  market,
  years,
  currentAction,
  currentCoverage,
  krCacheCoverage,
  requireConsensusForecast
}: {
  ticker?: string;
  market: string | null;
  years: string;
  currentAction?: SourceCoverageRemediationAction;
  currentCoverage?: SourceCoverageTicker;
  krCacheCoverage: KrValuationCacheCoverage | null;
  requireConsensusForecast: boolean;
}): CompletionGate {
  const resolvedTicker = ticker ?? "selected ticker";
  const resolvedMarket = market ?? "ALL";
  const isKr = resolvedMarket === "KR" || resolvedTicker.endsWith(".KS");
  const status = completionStatus(currentCoverage, currentAction, krCacheCoverage, isKr);
  const buildCommand = currentAction?.cli_commands[0] ?? (
    isKr
      ? `python -m services.ingestion_worker.cli build-kr-valuation-inputs --tickers ${resolvedTicker} --years ${years} --strict`
      : `python -m services.ingestion_worker.cli source-coverage --market ${resolvedMarket} --tickers ${resolvedTicker} --strict`
  );
  const consensusFlag = requireConsensusForecast ? " --require-consensus-forecast" : "";
  const localCommands: CompletionCommand[] = isKr
    ? [
        {
          id: "build_kr_valuation_inputs",
          command: buildCommand,
          proves: "raw OpenDART/pykrx/marcap evidence can produce valuation-map input cache"
        },
        {
          id: "load_kr_valuation_warehouse",
          command: `python -m services.ingestion_worker.cli load-kr-valuation-warehouse --tickers ${resolvedTicker} --strict`,
          proves: "source-traced valuation inputs are available through DuckDB/Parquet warehouse"
        },
        {
          id: "api_valuation_map_probe",
          command: "python -m pytest tests/api/test_api.py::test_kr_priority_valuation_map_uses_warehouse_before_cache -q",
          proves: "valuation-map API prefers source-backed warehouse rows before cache fallback"
        }
      ]
    : [
        {
          id: "source_coverage",
          command: buildCommand,
          proves: "source-backed coverage is ready for the selected market"
        }
      ];
  return {
    status,
    ticker: resolvedTicker,
    market: resolvedMarket,
    localStatus: completionLocalStatus(status, krCacheCoverage),
    requiredProofs: completionRequiredProofs(isKr, requireConsensusForecast),
    localCommands,
    deploymentCommand: {
      id: "source_coverage_postgres",
      command: `python -m services.ingestion_worker.cli source-coverage --market ${isKr ? "KR" : resolvedMarket} --tickers ${resolvedTicker}${consensusFlag} --strict`,
      requires: "Requires DATA_BACKEND=postgres and DATABASE_URL",
      proves: "persisted Neon/Postgres source coverage is ready for deployment"
    }
  };
}

function completionStatus(
  currentCoverage: SourceCoverageTicker | undefined,
  currentAction: SourceCoverageRemediationAction | undefined,
  krCacheCoverage: KrValuationCacheCoverage | null,
  isKr: boolean
) {
  if (krCacheCoverage?.data_backend === "kr_valuation_warehouse" && krCacheCoverage.valuation_ready) {
    return "complete";
  }
  if (krCacheCoverage?.valuation_ready) {
    return "ready_for_warehouse_load";
  }
  if (isKr && currentCoverage && !currentCoverage.core_ready && currentAction) {
    return "ready_for_valuation_cache_build";
  }
  if (isKr && currentCoverage) {
    return "ready_for_valuation_cache_build";
  }
  if (currentCoverage?.core_ready) {
    return "ready_for_api_probe";
  }
  return "planned";
}

function completionLocalStatus(status: string, krCacheCoverage: KrValuationCacheCoverage | null) {
  if (status === "complete") {
    return "Local proof complete: warehouse rows are loaded and API can prefer source-backed rows.";
  }
  if (status === "ready_for_warehouse_load") {
    return `Valuation cache ready: ${formatCoverageStatus(krCacheCoverage?.coverage_status)}. Load the warehouse and run the API probe next.`;
  }
  if (status === "ready_for_valuation_cache_build") {
    return "Local raw/source coverage is actionable. Build valuation inputs, load the warehouse, then run the API probe.";
  }
  if (status === "ready_for_api_probe") {
    return "Core source coverage is ready. Run the valuation-map API proof before deployment.";
  }
  return "Completion path is planned; source-backed rows must be collected before financial values are displayed.";
}

function completionRequiredProofs(isKr: boolean, requireConsensusForecast: boolean) {
  const proofs = [
    "raw source files are append-only and source_trace-ready",
    "normalized valuation inputs build without rejected source_trace rows",
    "warehouse load succeeds and rejects non-production rows",
    "local valuation-map API proof prefers source-backed warehouse rows"
  ];
  if (requireConsensusForecast) {
    proofs.push("1Y-5Y forecast snapshots are separated from user input and AI commentary");
  }
  return isKr
    ? ["OpenDART financial facts, pykrx prices, and marcap evidence are present", ...proofs]
    : proofs;
}

function formatCompletionStatus(status: string) {
  return status.replace(/_/g, " ");
}

function priorityRankLabel(priorityUniverse: PriorityUniverse) {
  if (priorityUniverse.rank_coverage_status === "partial_top_market_cap_rank") {
    return "Partial source-backed market cap rank";
  }
  if (priorityUniverse.rank_coverage_status === "complete_top_market_cap_rank") {
    return "Complete source-backed market cap rank";
  }
  return priorityUniverse.data_mode === "source_backed"
    ? "Source-backed market cap rank"
    : "Not live market cap rank";
}

function formatRankStatus(status?: string) {
  return (status ?? "coverage_contract_only").replace(/_/g, " ");
}

function readinessPercent(ready: number, total: number) {
  if (!Number.isFinite(ready) || !Number.isFinite(total) || total <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round((ready / total) * 100)));
}

function formatCoverageStatus(status?: string) {
  return (status ?? "not loaded").replace(/_/g, " ");
}

function krFinancialNumbersAllowed(coverage: KrValuationCacheCoverage) {
  return coverage.financial_numbers_allowed ?? Boolean(coverage.valuation_ready);
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

function formatMissingYears(coverage: KrValuationCacheCoverage) {
  const market = coverage.missing_years.market_input;
  const metric = coverage.missing_years.financial_metric;
  if (!market.length && !metric.length) {
    return "none";
  }
  return [
    market.length ? `market ${formatYearList(market)}` : "",
    metric.length ? `metric ${formatYearList(metric)}` : ""
  ].filter(Boolean).join(" / ");
}

function formatGapDiagnostics(coverage: KrValuationCacheCoverage) {
  const counts = coverage.financial_gap_diagnostics.reduce<Record<string, number>>((acc, gap) => {
    const key = gap.status ?? "unknown";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  return Object.entries(counts)
    .map(([status, count]) => `${status.replace(/_/g, " ")} ${count}`)
    .join(" / ");
}

function formatMarketGapDiagnostics(coverage: KrValuationCacheCoverage) {
  const counts = coverage.market_gap_diagnostics.reduce<Record<string, number>>((acc, gap) => {
    const key = gap.status ?? "unknown";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  return Object.entries(counts)
    .map(([status, count]) => `${status.replace(/_/g, " ")} ${count}`)
    .join(" / ");
}
