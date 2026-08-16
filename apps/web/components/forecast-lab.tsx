"use client";

import { useMemo } from "react";
import { auditFactHref } from "../lib/audit-utils";
import { forecastCalculationLines } from "../lib/terminal-chart";
import { KrSourceReadinessCard } from "./kr-source-readiness-card";
import type {
  AskConsensusEvidence,
  AuditRow,
  ForecastAiReviewNote,
  ForecastEvidence,
  ForecastMeta,
  KrValuationCacheCoverage,
  KrValuationCacheUniverseCoverage,
  ValuationRow
} from "../lib/terminal-types";

export function ForecastLab({
  ticker,
  valuation,
  auditRows,
  auditQueryString,
  forecastMeta,
  forecastEvidence,
  forecastYears,
  forecastMode,
  onForecastModeChange,
  manualEps,
  onManualEpsChange,
  hiddenScenarioLines,
  onToggleScenarioLine,
  onFocusAuditFact,
  onOpenDataAudit,
  krCacheCoverage,
  krCacheUniverse
}: {
  ticker?: string | null;
  valuation: ValuationRow[];
  auditRows: AuditRow[];
  auditQueryString: string;
  forecastMeta: ForecastMeta;
  forecastEvidence: ForecastEvidence;
  forecastYears: number;
  forecastMode: string;
  onForecastModeChange: (mode: string) => void;
  manualEps: string[];
  onManualEpsChange: (index: number, value: string) => void;
  hiddenScenarioLines: string[];
  onToggleScenarioLine: (label: string) => void;
  onFocusAuditFact: (factId: string) => void;
  onOpenDataAudit: () => void;
  krCacheCoverage?: KrValuationCacheCoverage | null;
  krCacheUniverse?: KrValuationCacheUniverseCoverage | null;
}) {
  const visibleInputs = Array.from({ length: 5 }, (_, index) => index);
  const calculationLines = forecastCalculationLines(forecastMeta);
  const activeCalculationLineCount = calculationLines.filter((line) => !hiddenScenarioLines.includes(line.label)).length;
  const consensus = forecastMeta.consensus;
  const missingConsensusYears = consensus?.missing_years ?? [];
  const currentRevision = forecastEvidence.revisions.find((row) => row.as_of_label === "current");
  const assumptionLedger = forecastAssumptionLedger(forecastMeta, forecastEvidence, forecastYears, manualEps);
  const projectionRows = useMemo(
    () => valuation.filter((row) => row.forecast_flag).slice(0, forecastYears),
    [forecastYears, valuation]
  );
  const calculatorCards = useMemo(
    () =>
      buildForecastCalculatorCards({
        forecastMeta,
        forecastEvidence,
        projectionRows,
        forecastYears,
        forecastMode,
        manualEps,
        auditRows,
        auditQueryString
      }),
    [auditQueryString, auditRows, forecastEvidence, forecastMeta, forecastMode, forecastYears, manualEps, projectionRows]
  );
  const scenarioWorkbenchRows = useMemo(
    () =>
      buildScenarioWorkbenchRows({
        valuation,
        projectionRows,
        calculationLines,
        hiddenScenarioLines,
        auditRows,
        auditQueryString
      }),
    [auditQueryString, auditRows, calculationLines, hiddenScenarioLines, projectionRows, valuation]
  );
  const caseComparisonRows = useMemo(
    () =>
      buildForecastCaseComparisonRows({
        forecastEvidence,
        auditRows,
        auditQueryString
      }),
    [auditQueryString, auditRows, forecastEvidence]
  );
  const scenarioChart = useMemo(
    () => buildForecastScenarioChart({
      projectionRows,
      calculationLines,
      hiddenScenarioLines,
      targetMultiple: forecastMeta.target_multiple
    }),
    [calculationLines, forecastMeta.target_multiple, hiddenScenarioLines, projectionRows]
  );
  const aiReviewNotes = useMemo(
    () =>
      buildForecastAiReviewNotes({
        forecastMeta,
        forecastEvidence,
        projectionRows,
        forecastYears,
        manualEps,
        hiddenScenarioLines,
        auditRows,
        auditQueryString
      }),
    [auditQueryString, auditRows, forecastEvidence, forecastMeta, forecastYears, hiddenScenarioLines, manualEps, projectionRows]
  );
  const traceGuard = forecastSourceTraceGuard(forecastMeta);
  const manualOverrideCount = manualEps.slice(0, forecastYears).filter((value) => value.trim()).length;
  const forecastAuditRows = useMemo(
    () => auditRows.filter((row) => isForecastP1AuditFact(row.fact_name)),
    [auditRows]
  );
  const forecastSourceContract = useMemo(
    () => buildForecastSourceContract(projectionRows, forecastAuditRows, forecastMeta),
    [forecastAuditRows, forecastMeta, projectionRows]
  );
  const forecastAssumptionAuditRows = useMemo(
    () => auditRows.filter((row) => row.fact_name?.startsWith("forecast_assumption.")),
    [auditRows]
  );
  const forecastInputLanes = [
    {
      key: "consensus",
      label: "Consensus snapshots",
      value: missingConsensusYears.length ? `Missing ${missingConsensusYears.length}/${forecastYears}` : `${forecastEvidence.cases.length} cases`,
      detail: missingConsensusYears.length
        ? `Missing FY ${missingConsensusYears.join(", ")}`
        : `Selected ${forecastMeta.case ?? consensus?.case ?? "median"} case`,
      quality: consensus?.quality_status ?? forecastEvidence.meta?.quality_status ?? "not_loaded"
    },
    {
      key: "manual",
      label: "User EPS overrides",
      value: `${manualOverrideCount}/${forecastYears}`,
      detail: manualOverrideCount ? "Explicit user inputs override forecast EPS slots" : "No manual EPS overrides active",
      quality: manualOverrideCount ? "explicit_user_input" : "not_used"
    },
    {
      key: "formula",
      label: "Deterministic formulas",
      value: `${projectionRows.length} rows`,
      detail: "target price, CAGR, MoS, total return",
      quality: "source_trace_required"
    },
    {
      key: "ai",
      label: "AI commentary",
      value: "commentary only",
      detail: "llm_generated_numbers=false",
      quality: "non_numeric_review"
    }
  ];
  const projectionAuditRows = useMemo(
    () => new Map(
      auditRows
        .filter((row) => row.fact_name?.startsWith("forecast.") || row.fact_name?.startsWith("forecast_snapshot."))
        .map((row) => [`${row.fiscal_year}:${row.fact_name}`, row])
    ),
    [auditRows]
  );
  const forecastAuditRow = (row: ValuationRow, factName: string) =>
    projectionAuditRows.get(`${row.fiscal_year}:forecast.${factName}`);
  const forecastAuditFactId = (row: ValuationRow, factName: string) => forecastAuditRow(row, factName)?.fact_id;
  const projectionAuditFacts = (row: ValuationRow) =>
    [
      { factName: "metric", label: "EPS" },
      { factName: "price", label: "Target" },
      { factName: "price_cagr_pct", label: "Price CAGR" },
      { factName: "total_return_cagr_pct", label: "Return" },
      { factName: "margin_of_safety_pct", label: "MoS" },
      { factName: "dividend", label: "Div" }
    ]
      .map((fact) => ({ ...fact, factId: forecastAuditFactId(row, fact.factName) }))
      .filter((fact): fact is { factName: string; label: string; factId: string } => Boolean(fact.factId));
  const forecastAuditHref = (row: ValuationRow, factName: string) => {
    const auditRow = forecastAuditRow(row, factName);
    return auditRow ? auditFactHref(auditRow.fact_id, auditQueryString) : null;
  };
  const linkedForecastValue = (row: ValuationRow, factName: string, value: string) => {
    const href = forecastAuditHref(row, factName);
    if (!href) {
      return value;
    }
    return (
      <a href={href} target="_blank" rel="noreferrer" aria-label={`${row.fiscal_year} ${factName} audit`}>
        {value}
      </a>
    );
  };
  const linkedConsensusEstimate = (row: ForecastEvidence["cases"][number]) => {
    const auditRow = projectionAuditRows.get(`${forecastEvidence.forecast_year}:forecast_snapshot.${row.case}.estimate_eps`);
    const value = formatNumber(row.estimate_eps);
    if (!auditRow) {
      return value;
    }
    return (
      <a href={auditFactHref(auditRow.fact_id, auditQueryString)} target="_blank" rel="noreferrer" aria-label={`${row.case} consensus estimate audit`}>
        {value}
      </a>
    );
  };
  const inspectAuditFact = (factId?: string) => {
    if (!factId) {
      return;
    }
    onFocusAuditFact(factId);
    onOpenDataAudit();
  };
  const firstProjectionFactId = (factName: string) => {
    for (const row of projectionRows) {
      const factId = forecastAuditFactId(row, factName);
      if (factId) {
        return factId;
      }
    }
    return undefined;
  };
  const assumptionFactId =
    forecastAssumptionAuditRows.find((row) => row.fact_name === "forecast_assumption.formula")?.fact_id ??
    forecastAssumptionAuditRows[0]?.fact_id;
  const forecastRailAuditFactId = firstProjectionFactId("total_return_cagr_pct") ?? firstProjectionFactId("price") ?? assumptionFactId;
  const forecastDecisionRail = buildForecastDecisionRail({
    forecastMeta,
    forecastEvidence,
    forecastYears,
    projectionRows,
    forecastSourceContract,
    manualOverrideCount,
    forecastAuditRows
  });
  const consensusPreflight = buildForecastConsensusPreflight({
    ticker,
    forecastMeta,
    forecastEvidence,
    forecastYears,
    missingConsensusYears,
    forecastSourceContract
  });
  const forecastSourceTargets = [
    { key: "eps-cell", label: "forecast EPS cell", detail: "forecast.metric", factId: firstProjectionFactId("metric") },
    { key: "target-price", label: "target price", detail: "forecast.price", factId: firstProjectionFactId("price") },
    { key: "cagr", label: "CAGR", detail: "forecast.total_return_cagr_pct", factId: firstProjectionFactId("total_return_cagr_pct") },
    { key: "dividend-return", label: "dividend return", detail: "forecast.dividend", factId: firstProjectionFactId("dividend") },
    { key: "margin-of-safety", label: "margin of safety", detail: "forecast.margin_of_safety_pct", factId: firstProjectionFactId("margin_of_safety_pct") },
    { key: "assumption-row", label: "assumption row", detail: "forecast_assumption.formula", factId: assumptionFactId }
  ];
  const forecastStateChips = forecastP1StateChips({
    forecastMeta,
    forecastEvidence,
    forecastAuditRows,
    projectionRows,
    manualOverrideCount
  });
  const totalScenarioLines = calculationLines.length;
  const forecastWorkflowSteps = [
    {
      key: "horizon",
      label: "Horizon",
      value: `${forecastYears}/5Y runway`,
      detail: `${projectionRows.length} projection rows`
    },
    {
      key: "mode",
      label: "Calculator",
      value: forecastMode,
      detail: `${forecastMeta.target_multiple}x target multiple`
    },
    {
      key: "consensus",
      label: "Consensus lane",
      value: missingConsensusYears.length ? `missing ${missingConsensusYears.length}` : `${forecastEvidence.cases.length} cases`,
      detail: missingConsensusYears.length
        ? `FY ${missingConsensusYears.join(", ")} needs source`
        : `selected ${forecastMeta.case ?? consensus?.case ?? "median"} case`
    },
    {
      key: "manual",
      label: "Manual EPS",
      value: `${manualOverrideCount}/${forecastYears}`,
      detail: manualOverrideCount ? "explicit user overrides active" : "no user overrides"
    },
    {
      key: "formula",
      label: "Formula output",
      value: `${forecastSourceContract.projectionComplete}/${forecastSourceContract.projectionRows}`,
      detail: "target, CAGR, MoS, total return"
    },
    {
      key: "scenarios",
      label: "Scenario lines",
      value: totalScenarioLines ? `${activeCalculationLineCount}/${totalScenarioLines}` : "0/0",
      detail: "toggle visible valuation paths"
    },
    {
      key: "ai",
      label: "AI boundary",
      value: "commentary only",
      detail: "llm_generated_numbers=false"
    }
  ];
  return (
    <section className="forecast-lab" aria-label="Forecast assumptions">
      <div>
        <span>Forecast model</span>
        <strong>{forecastMeta.mode}</strong>
        <em>{forecastMeta.source ?? "deterministic"}</em>
      </div>
      <div>
        <span>Forecast case</span>
        <strong>{forecastMeta.case ?? consensus?.case ?? "median"}</strong>
        <em>{consensus?.quality_status ?? "user or deterministic"}</em>
      </div>
      <div>
        <span>Forecast evidence</span>
        <strong>{consensus?.quality_status ?? forecastMeta.source ?? "deterministic"}</strong>
        <em>
          {missingConsensusYears.length
            ? `missing FY ${missingConsensusYears.join(", ")}`
            : consensus?.revision_status ?? "manual or historical projection"}
        </em>
      </div>
      <div>
        <span>Growth</span>
        <strong>{Number(forecastMeta.growth_rate_pct).toFixed(1)}%</strong>
        <em>{forecastMeta.analyst_count ? `${forecastMeta.analyst_count} analysts` : "user or deterministic"}</em>
      </div>
      <div>
        <span>Consensus range</span>
        <strong>{formatConsensusRange(consensus)}</strong>
        <em>{consensus?.source_note ?? "low / median / high"}</em>
      </div>
      <div>
        <span>Revision ledger</span>
        <strong>{currentRevision?.estimate_eps ?? "not loaded"}</strong>
        <em>{forecastEvidence.sentiment.net_revision_score_pct}% vs 3M prior</em>
      </div>
      <div>
        <span>Analyst Sentiment</span>
        <strong>{forecastEvidence.sentiment.label}</strong>
        <em>
          {forecastEvidence.sentiment.up_revisions} up / {forecastEvidence.sentiment.down_revisions} down / {forecastEvidence.sentiment.unchanged} flat
        </em>
      </div>
      <div>
        <span>Calculation lines</span>
        <strong>{calculationLines.length ? activeCalculationLineCount : 11} active lines</strong>
        <em>{forecastMeta.target_multiple}x center</em>
      </div>
      <div className="forecast-underwriting-workflow" data-testid="forecast-underwriting-workflow" aria-label="Forecast underwriting workflow">
        <div className="forecast-workflow-header">
          <span>1Y-5Y underwriting workflow</span>
          <strong>{forecastYears}Y forward model</strong>
          <em>Consensus, user inputs, deterministic formulas, scenario lines, and AI commentary stay separated.</em>
        </div>
        <div className="forecast-workflow-grid">
          {forecastWorkflowSteps.map((step) => (
            <section key={step.key} data-testid={`forecast-workflow-${step.key}`}>
              <span>{step.label}</span>
              <strong>{step.value}</strong>
              <small>{step.detail}</small>
            </section>
          ))}
        </div>
      </div>
      <div className="forecast-decision-rail" data-testid="forecast-decision-rail" aria-label="Forecast decision rail">
        <div className="forecast-decision-rail-header">
          <span>Forecast Decision Rail</span>
          <strong>{forecastDecisionRail.title}</strong>
          <em>{forecastDecisionRail.subtitle}</em>
        </div>
        <div className="forecast-decision-strip" data-testid="forecast-decision-strip">
          {forecastDecisionRail.cards.map((card) => (
            <div key={card.label} data-testid={`forecast-decision-card-${card.key}`}>
              <span>{card.label}</span>
              <strong>{card.value}</strong>
              <small>{card.detail}</small>
            </div>
          ))}
        </div>
        <dl className="forecast-decision-audit-grid">
          {forecastDecisionRail.audit.map((item) => (
            <div key={item.label} data-testid={`forecast-decision-audit-${item.key}`}>
              <dt>{item.label}</dt>
              <dd title={item.value}>{item.value}</dd>
            </div>
          ))}
        </dl>
        <div className="forecast-underwriting-gates" data-testid="forecast-underwriting-gates" aria-label="Forecast underwriting gates">
          {forecastDecisionRail.gates.map((gate) => (
            <section key={gate.key} className={gate.tone} data-testid={`forecast-gate-${gate.key}`}>
              <span>{gate.label}</span>
              <strong>{gate.value}</strong>
              <small>{gate.detail}</small>
            </section>
          ))}
        </div>
        <button
          type="button"
          className="forecast-decision-open-audit"
          data-testid="forecast-decision-open-audit"
          disabled={!forecastRailAuditFactId}
          onClick={() => inspectAuditFact(forecastRailAuditFactId)}
        >
          Open forecast audit
        </button>
      </div>
      <div className="forecast-p1-contract" aria-label="Figma Forecast P-1 handoff contract" data-testid="forecast-p1-contract">
        <span>Figma Forecast P-1 contract</span>
        <div className="forecast-contract-grid">
          <section>
            <span>Route</span>
            <strong>/forecast</strong>
            <em>Compare 1Y-5Y return scenarios without AI-generated numbers.</em>
          </section>
          <section>
            <span>Data dependencies</span>
            <strong>valuation-map, adjusted/GAAP metric series, estimates snapshots, user assumptions, dividend policy, source_trace guard</strong>
            <em>Every forward value is rejected until lineage is visible.</em>
          </section>
          <section>
            <span>Interactions</span>
            <strong>calculator mode switch, EPS growth inputs, target multiple input, bear/base/bull toggle, source row inspect, export scenario</strong>
            <em>Controls mutate assumptions, not source facts.</em>
          </section>
          <section>
            <span>Screen model</span>
            <strong>Estimator cards, Scenario fan chart, User input table, Return summary, Assumption audit, AI commentary guard</strong>
            <em>Compact terminal layout derived from the Figma handoff.</em>
          </section>
          <section className="wide">
            <span>Acceptance</span>
            <strong>Consensus/user/formula/AI lanes separated</strong>
            <em>Every computed return has formula and input_fact_ids; inactive calculators show run-to-calculate.</em>
          </section>
        </div>
        <div className="forecast-state-chips" data-testid="forecast-state-chips">
          {forecastStateChips.map((chip) => (
            <strong key={chip.label} className={chip.tone}>
              {chip.label}
              <em>{chip.value}</em>
            </strong>
          ))}
        </div>
        <div className="forecast-source-contract-card" data-testid="forecast-source-contract-card">
          <div className="forecast-source-contract-heading">
            <span>Forecast source contract</span>
            <strong data-testid="forecast-source-contract-status">{forecastSourceContract.statusLabel}</strong>
          </div>
          <dl>
            <div data-testid="forecast-source-contract-projections">
              <dt>Projection rows</dt>
              <dd>{forecastSourceContract.projectionComplete}/{forecastSourceContract.projectionRows}</dd>
            </div>
            <div data-testid="forecast-source-contract-audit">
              <dt>Audit facts</dt>
              <dd>{forecastSourceContract.auditComplete}/{forecastSourceContract.auditRows}</dd>
            </div>
            <div data-testid="forecast-source-contract-meta">
              <dt>Assumption trace</dt>
              <dd>{forecastSourceContract.metaReady ? "ready" : "pending"}</dd>
            </div>
          </dl>
          <small data-testid="forecast-source-contract-missing">{forecastSourceContract.firstMissingLabel}</small>
        </div>
        <div
          className={`forecast-consensus-preflight ${consensusPreflight.tone}`}
          data-testid="forecast-consensus-preflight"
          aria-label="Consensus forecast preflight"
        >
          <div className="forecast-consensus-preflight-heading">
            <span>Consensus preflight</span>
            <strong data-testid="forecast-consensus-preflight-status">{consensusPreflight.status}</strong>
            <em data-testid="forecast-consensus-missing-years">{consensusPreflight.missingYearsLabel}</em>
          </div>
          <div className="forecast-consensus-preflight-grid">
            {consensusPreflight.checks.map((check) => (
              <section key={check.key} className={check.tone} data-testid={`forecast-consensus-check-${check.key}`}>
                <span>{check.label}</span>
                <strong>{check.value}</strong>
                <small>{check.detail}</small>
              </section>
            ))}
          </div>
          <div className="forecast-consensus-command-list" data-testid="forecast-consensus-command-list">
            {consensusPreflight.commands.map((command) => (
              <code key={command.key} data-testid={`forecast-consensus-command-${command.key}`}>
                {command.value}
              </code>
            ))}
          </div>
        </div>
        <KrSourceReadinessCard
          title="Forecast KR source gate"
          testIdPrefix="forecast-kr"
          ticker={ticker}
          krCacheCoverage={krCacheCoverage}
          krCacheUniverse={krCacheUniverse}
        />
        <div className="forecast-source-targets" data-testid="forecast-source-targets">
          {forecastSourceTargets.map((target) => (
            <button
              key={target.key}
              type="button"
              data-testid={`forecast-target-${target.key}`}
              disabled={!target.factId}
              onClick={() => inspectAuditFact(target.factId)}
            >
              <span>{target.label}</span>
              <small>{target.detail}</small>
            </button>
          ))}
        </div>
      </div>
      <div className="forecast-provenance-lanes" aria-label="Forecast input provenance lanes" data-testid="forecast-provenance-lanes">
        <span>Forecast input lanes</span>
        <div>
          {forecastInputLanes.map((lane) => (
            <section key={lane.key} data-testid={`forecast-lane-${lane.key}`}>
              <span>{lane.label}</span>
              <strong>{lane.value}</strong>
              <em>{lane.detail}</em>
              <small>{lane.quality}</small>
            </section>
          ))}
        </div>
        <em>Forward values are accepted only from consensus snapshots, explicit user inputs, or deterministic formulas; AI remains non-numeric commentary.</em>
      </div>
      <div className="forecast-calculator-switchboard" aria-label="Forecast calculator modes" data-testid="forecast-calculator-switchboard">
        <span>Forecast calculators</span>
        <div>
          {calculatorCards.map((card) => (
            <div key={card.mode} className={`forecast-calculator-card ${card.active ? "active" : ""}`}>
              <button
                type="button"
                aria-pressed={card.active}
                data-testid={`forecast-calculator-card-${card.mode}`}
                onClick={() => onForecastModeChange(card.mode)}
              >
                <strong>{card.title}</strong>
                <small>{card.description}</small>
                <dl>
                  <div><dt>Input</dt><dd>{card.input}</dd></div>
                  <div><dt>Target</dt><dd>{card.target}</dd></div>
                  <div><dt>Return</dt><dd>{card.returnLabel}</dd></div>
                  <div><dt>MoS</dt><dd>{card.marginOfSafety}</dd></div>
                  <div><dt>Quality</dt><dd>{card.quality}</dd></div>
                </dl>
              </button>
              {card.active && card.auditHref ? (
                <a href={card.auditHref} target="_blank" rel="noreferrer">
                  Open audit
                </a>
              ) : (
                <em>run-to-calculate</em>
              )}
            </div>
          ))}
        </div>
        <em>Five calculators share the same deterministic forecast dataset. Inactive cards do not display uncomputed financial values.</em>
      </div>
      <div className="forecast-assumption-ledger" aria-label="Forecast assumption ledger" data-testid="forecast-assumption-ledger">
        <span>Assumption ledger</span>
        <table>
          <thead>
            <tr>
              <th>Input</th>
              <th>Value</th>
              <th>Source</th>
              <th>Quality</th>
            </tr>
          </thead>
          <tbody>
            {assumptionLedger.map((row) => (
              <tr key={row.label}>
                <td>{row.label}</td>
                <td>{row.value}</td>
                <td>{row.source}</td>
                <td>{row.quality}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <em>Forecast values are sourced from explicit inputs, consensus snapshots, or deterministic formulas.</em>
      </div>
      <div className="forecast-ai-review" aria-label="AI review memo" data-testid="forecast-ai-review-panel">
        <span>AI Review memo</span>
        <table>
          <thead>
            <tr>
              <th>Check</th>
              <th>Read</th>
              <th>Source</th>
              <th>Quality</th>
            </tr>
          </thead>
          <tbody>
            {aiReviewNotes.map((note) => (
              <tr key={note.label}>
                <td>{note.label}</td>
                <td>
                  {note.href ? (
                    <a href={note.href} target="_blank" rel="noreferrer">
                      {note.value}
                    </a>
                  ) : (
                    note.value
                  )}
                  <em>{note.detail}</em>
                </td>
                <td>{note.method}</td>
                <td>{note.quality}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="forecast-trace-guard" data-testid="forecast-trace-guard">
          <span>Source-trace guard</span>
          {traceGuard.map((item) => (
            <strong key={item.label}>
              {item.label}
              <em>{item.value}</em>
            </strong>
          ))}
        </div>
        <em>AI review mode is commentary only. EPS, target price, and return values remain source-backed or deterministic.</em>
      </div>
      <div className="forecast-case-table" aria-label="Consensus case matrix" data-testid="forecast-case-table">
        <span>Consensus case matrix</span>
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th>Growth</th>
              <th>Estimate EPS</th>
              <th>Quality</th>
            </tr>
          </thead>
          <tbody>
            {forecastEvidence.cases.map((row) => (
              <tr key={`forecast-case-${row.case}`}>
                <td>{row.case}</td>
                <td>{formatMaybeGrowth(row.growth_rate_pct)}</td>
                <td>{linkedConsensusEstimate(row)}</td>
                <td>{String(row.source_trace?.quality_status ?? consensus?.quality_status ?? forecastEvidence.meta?.quality_status ?? "unknown")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <em>Consensus rows are point-in-time snapshots when loaded; fixture proxies remain clearly quality-labeled.</em>
      </div>
      <div className="forecast-case-comparison" aria-label="Bear base bull scenario comparison" data-testid="forecast-case-comparison">
        <span>Bear / Base / Bull comparison</span>
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th>EPS</th>
              <th>Growth</th>
              <th>Target</th>
              <th>Total CAGR</th>
              <th>MoS</th>
              <th>Quality</th>
              <th>Audit</th>
            </tr>
          </thead>
          <tbody>
            {caseComparisonRows.map((row) => (
              <tr key={`forecast-case-comparison-${row.caseName}`}>
                <td>
                  <strong>{row.label}</strong>
                  <em>{row.caseName}</em>
                </td>
                <td>
                  {row.estimateHref ? (
                    <a href={row.estimateHref} target="_blank" rel="noreferrer" aria-label={`${row.caseName} case EPS audit`}>
                      {row.estimateEps}
                    </a>
                  ) : (
                    row.estimateEps
                  )}
                </td>
                <td>{row.growthRate}</td>
                <td>
                  {row.targetHref ? (
                    <a href={row.targetHref} target="_blank" rel="noreferrer" aria-label={`${row.caseName} case target audit`}>
                      {row.targetPrice}
                    </a>
                  ) : (
                    row.targetPrice
                  )}
                </td>
                <td>{row.totalReturnCagr}</td>
                <td>{row.marginOfSafety}</td>
                <td>{row.quality}</td>
                <td>
                  {row.auditFactId ? (
                    <button
                      type="button"
                      data-testid={`forecast-case-inspect-${row.caseName}`}
                      onClick={() => inspectAuditFact(row.auditFactId)}
                    >
                      Inspect
                    </button>
                  ) : (
                    <span className="forecast-case-pending">pending</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <em>Comparison values are read from forecast_case audit facts; missing source_trace leaves the row pending.</em>
      </div>
      <div className="forecast-projection-table" aria-label="Forecast projection return table" data-testid="forecast-projection-table">
        <span>Forecast return calculator</span>
        <table>
          <thead>
            <tr>
              <th>FY</th>
              <th>EPS/metric</th>
              <th>Target price</th>
              <th>Price CAGR</th>
              <th>Dividend-incl CAGR</th>
              <th>MoS</th>
              <th>Dividend</th>
              <th>Source</th>
              <th>Audit</th>
            </tr>
          </thead>
          <tbody>
            {projectionRows.map((row) => {
              const auditFacts = projectionAuditFacts(row);
              return (
                <tr key={`forecast-projection-${row.fiscal_year}`}>
                  <td>{row.fiscal_year}E</td>
                  <td>{linkedForecastValue(row, "metric", formatNumber(row.metric))}</td>
                  <td>{linkedForecastValue(row, "price", formatNumber(row.price))}</td>
                  <td>{linkedForecastValue(row, "price_cagr_pct", formatMaybeGrowth(row.price_cagr_pct))}</td>
                  <td>{linkedForecastValue(row, "total_return_cagr_pct", formatMaybeGrowth(row.total_return_cagr_pct))}</td>
                  <td>{linkedForecastValue(row, "margin_of_safety_pct", formatMaybeGrowth(row.margin_of_safety_pct))}</td>
                  <td>{linkedForecastValue(row, "dividend", formatNumber(row.dividend))}</td>
                  <td>{row.forecast_source ?? forecastMeta.source ?? "deterministic"}</td>
                  <td className="forecast-projection-audit-actions">
                    {auditFacts.length ? (
                      auditFacts.map((fact) => (
                        <button
                          key={fact.factName}
                          type="button"
                          data-testid={`forecast-projection-inspect-${row.fiscal_year}-${auditTestIdPart(fact.factName)}`}
                          onClick={() => inspectAuditFact(fact.factId)}
                        >
                          {fact.label}
                        </button>
                      ))
                    ) : (
                      <span className="forecast-projection-pending">pending</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <em>Target price and returns update from the active horizon, manual EPS, growth, target multiple, and dividend assumptions.</em>
      </div>
      {scenarioChart ? (
        <div className="forecast-scenario-chart" aria-label="Forecast scenario fan chart" data-testid="forecast-scenario-chart">
          <div className="forecast-scenario-chart-header">
            <span>1Y-5Y scenario fan</span>
            <strong>{scenarioChart.visibleLineCount}/{scenarioChart.totalLineCount} lines</strong>
            <em>{forecastMeta.target_multiple}x center multiple</em>
          </div>
          <svg viewBox="0 0 760 260" role="img" aria-label="Forecast scenario line visualization">
            <rect x="0" y="0" width="760" height="260" rx="0" />
            {scenarioChart.yTicks.map((tick) => (
              <g key={`forecast-y-${tick.label}`}>
                <line x1="48" x2="704" y1={tick.y} y2={tick.y} />
                <text x="14" y={tick.y + 4}>{tick.label}</text>
              </g>
            ))}
            {scenarioChart.xTicks.map((tick) => (
              <g key={`forecast-x-${tick.label}`}>
                <line x1={tick.x} x2={tick.x} y1="24" y2="218" />
                <text x={tick.x} y="242">{tick.label}</text>
              </g>
            ))}
            {scenarioChart.lines.map((line) => (
              <polyline
                key={line.label}
                className={line.isCenter ? "scenario-fan-line center" : "scenario-fan-line"}
                points={line.points}
              />
            ))}
            <polyline className="scenario-fan-projection" points={scenarioChart.projectionPoints} />
            {scenarioChart.terminal ? (
              <g className="scenario-fan-terminal">
                <circle cx={scenarioChart.terminal.x} cy={scenarioChart.terminal.y} r="5" />
                <text x={Number(scenarioChart.terminal.x) + 9} y={Number(scenarioChart.terminal.y) - 8}>
                  {scenarioChart.terminal.label}
                </text>
              </g>
            ) : null}
          </svg>
          <em>Fan lines are visual overlays from the active scenario line set; source values stay in the forecast table and Data Audit links.</em>
        </div>
      ) : null}
      <div className="scenario-line-control">
        <span>Scenario line toggles</span>
        <div>
          {calculationLines.map((line) => {
            const visible = !hiddenScenarioLines.includes(line.label);
            return (
              <button
                key={line.label}
                type="button"
                className={`scenario-toggle ${visible ? "on" : ""}`}
                aria-pressed={visible}
                aria-label={`Toggle ${line.label} scenario line`}
                data-testid="scenario-line-toggle"
                onClick={() => onToggleScenarioLine(line.label)}
              >
                {line.label}
              </button>
            );
          })}
        </div>
        <em>Turn individual valuation lines on or off</em>
      </div>
      <div className="forecast-scenario-workbench" aria-label="Forecast scenario line workbench" data-testid="forecast-scenario-workbench">
        <span>Scenario line workbench</span>
        <table>
          <thead>
            <tr>
              <th>Line</th>
              <th>Multiple</th>
              <th>Terminal</th>
              <th>Total CAGR</th>
              <th>Source</th>
              <th>Audit</th>
              <th>Visible</th>
            </tr>
          </thead>
          <tbody>
            {scenarioWorkbenchRows.map((row) => (
              <tr key={`scenario-workbench-${row.label}`} className={row.visible ? "visible" : "hidden"}>
                <td>{row.label}</td>
                <td>{row.multiple}</td>
                <td>
                  {row.href ? (
                    <a href={row.href} target="_blank" rel="noreferrer" aria-label={`${row.label} terminal scenario audit`}>
                      {row.terminalYear}E / {formatNumber(row.terminalTargetPrice)}
                    </a>
                  ) : (
                    `${row.terminalYear}E / ${formatNumber(row.terminalTargetPrice)}`
                  )}
                </td>
                <td>{formatMaybeGrowth(row.totalReturnCagrPct)}</td>
                <td>
                  <strong>{row.method}</strong>
                  <em>{row.quality}</em>
                </td>
                <td>
                  {row.auditFactId ? (
                    <button
                      type="button"
                      className="scenario-workbench-inspect"
                      data-testid={`scenario-workbench-inspect-${auditTestIdPart(row.label)}`}
                      onClick={() => inspectAuditFact(row.auditFactId)}
                    >
                      Inspect
                    </button>
                  ) : (
                    <span className="scenario-workbench-pending">pending</span>
                  )}
                </td>
                <td>
                  <button
                    type="button"
                    className={`scenario-workbench-toggle ${row.visible ? "on" : ""}`}
                    aria-pressed={row.visible}
                    aria-label={`${row.visible ? "Hide" : "Show"} ${row.label} line from scenario workbench`}
                    onClick={() => onToggleScenarioLine(row.label)}
                  >
                    {row.visible ? "Shown" : "Hidden"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <em>Each line is a display overlay. Terminal target prices link to forecast_scenario audit facts when available.</em>
      </div>
      <div className="forecast-revisions">
        <span>Earnings revisions</span>
        <div className="revision-strip">
          {forecastEvidence.revisions.map((row) => (
            <strong key={row.as_of_label}>
              {row.as_of_label}
              <em>{row.estimate_eps} EPS / {row.analyst_count} analysts</em>
            </strong>
          ))}
        </div>
        <em>{forecastEvidence.meta?.source_note ?? consensus?.source_note}</em>
      </div>
      <div className="manual-eps-grid">
        {visibleInputs.map((index) => (
          <label key={index} className={index >= forecastYears ? "disabled" : ""}>
            Year {index + 1} EPS
            <input
              aria-label={`Year ${index + 1} EPS`}
              value={manualEps[index]}
              disabled={index >= forecastYears}
              inputMode="decimal"
              onChange={(event) => onManualEpsChange(index, event.target.value)}
              placeholder={index >= forecastYears ? "-" : "auto"}
            />
          </label>
        ))}
      </div>
    </section>
  );
}

function buildForecastScenarioChart({
  projectionRows,
  calculationLines,
  hiddenScenarioLines,
  targetMultiple
}: {
  projectionRows: ValuationRow[];
  calculationLines: ReturnType<typeof forecastCalculationLines>;
  hiddenScenarioLines: string[];
  targetMultiple: string;
}) {
  if (!projectionRows.length || !calculationLines.length) {
    return null;
  }
  const visibleLines = calculationLines.filter((line) => !hiddenScenarioLines.includes(line.label));
  const activeLines = visibleLines.length ? visibleLines : calculationLines.slice(0, 1);
  const allValues = [
    ...activeLines.flatMap((line) => line.points.map((point) => toNumberOrNull(point.target_price) ?? 0)),
    ...projectionRows.map((row) => toNumberOrNull(row.price) ?? 0)
  ].filter((value) => value > 0);
  if (!allValues.length) {
    return null;
  }
  const maxValue = Math.max(...allValues) * 1.08;
  const left = 48;
  const right = 704;
  const top = 24;
  const bottom = 218;
  const innerWidth = right - left;
  const innerHeight = bottom - top;
  const yearIndex = new Map(projectionRows.map((row, index) => [row.fiscal_year, index]));
  const xFor = (index: number) =>
    projectionRows.length <= 1
      ? left + innerWidth / 2
      : left + (index / (projectionRows.length - 1)) * innerWidth;
  const yFor = (value: number) => bottom - Math.min(1, Math.max(0, value / maxValue)) * innerHeight;
  const targetMultipleNumber = Number(targetMultiple);
  const centerLabel = Number.isFinite(targetMultipleNumber)
    ? `${targetMultipleNumber.toFixed(2).replace(/\.00$/, "")}x`
    : "";
  const lines = activeLines.map((line) => ({
    label: line.label,
    isCenter: line.label === centerLabel,
    points: line.points
      .map((point) => {
        const index = yearIndex.get(point.fiscal_year);
        const targetPrice = toNumberOrNull(point.target_price);
        if (index === undefined || targetPrice === null) {
          return null;
        }
        return `${xFor(index).toFixed(1)},${yFor(targetPrice).toFixed(1)}`;
      })
      .filter((point): point is string => Boolean(point))
      .join(" ")
  }));
  const projectionPoints = projectionRows
    .map((row, index) => {
      const targetPrice = toNumberOrNull(row.price);
      if (targetPrice === null) {
        return null;
      }
      return `${xFor(index).toFixed(1)},${yFor(targetPrice).toFixed(1)}`;
    })
    .filter((point): point is string => Boolean(point))
    .join(" ");
  const terminalRow = projectionRows.at(-1);
  const terminalValue = toNumberOrNull(terminalRow?.price);
  const terminalIndex = terminalRow ? yearIndex.get(terminalRow.fiscal_year) : undefined;
  return {
    lines,
    projectionPoints,
    totalLineCount: calculationLines.length,
    visibleLineCount: visibleLines.length,
    yTicks: [0, maxValue / 2, maxValue].map((value) => ({
      y: yFor(value).toFixed(1),
      label: formatNumber(value)
    })),
    xTicks: projectionRows.map((row, index) => ({
      x: xFor(index).toFixed(1),
      label: `${row.fiscal_year}E`
    })),
    terminal:
      terminalValue !== null && terminalIndex !== undefined
        ? {
            x: xFor(terminalIndex).toFixed(1),
            y: yFor(terminalValue).toFixed(1),
            label: `${terminalRow?.fiscal_year}E ${formatNumber(terminalValue)}`
          }
        : null
  };
}

function buildForecastDecisionRail({
  forecastMeta,
  forecastEvidence,
  forecastYears,
  projectionRows,
  forecastSourceContract,
  manualOverrideCount,
  forecastAuditRows
}: {
  forecastMeta: ForecastMeta;
  forecastEvidence: ForecastEvidence;
  forecastYears: number;
  projectionRows: ValuationRow[];
  forecastSourceContract: ReturnType<typeof buildForecastSourceContract>;
  manualOverrideCount: number;
  forecastAuditRows: AuditRow[];
}) {
  const terminalProjection = projectionRows.at(-1);
  const consensus = forecastMeta.consensus;
  const trace = forecastMeta.source_trace ?? {};
  const llmGeneratedNumbers = trace.llm_generated_numbers === false
    ? "No LLM numbers"
    : trace.llm_generated_numbers === true
      ? "LLM generated"
      : "Not declared";
  const sourceLabel = traceText(trace.source) ?? forecastMeta.source ?? "deterministic_formula";
  const methodLabel = traceText(trace.method) ?? forecastMeta.mode;
  const qualityLabel =
    consensus?.quality_status ??
    forecastEvidence.meta?.quality_status ??
    traceText(trace.quality_status) ??
    "deterministic";
  const firstAuditFlag = forecastAuditRows.flatMap((row) => row.flags).find(Boolean);
  const missingYears = consensus?.missing_years ?? [];
  const aiRoleLabel = traceText(trace.ai_role)?.replaceAll("_", " ") ?? "commentary only";
  const terminalReturn = formatMaybeGrowth(terminalProjection?.total_return_cagr_pct);
  const inputBasis = manualOverrideCount
    ? `${manualOverrideCount} user EPS override${manualOverrideCount === 1 ? "" : "s"}`
    : consensus
      ? `${consensus.case ?? forecastMeta.case ?? "base"} consensus`
      : `${forecastMeta.source ?? "deterministic"} formula`;
  const sourceReady = forecastSourceContract.statusLabel.includes("all forecast rows");
  const riskStatus = firstAuditFlag ?? (missingYears.length ? `missing ${missingYears.length} FY` : "quality flags clear");
  return {
    title: `${projectionRows.length}/${forecastYears}Y runway - ${forecastMeta.mode}`,
    subtitle: `${sourceLabel} / ${qualityLabel}`,
    cards: [
      {
        key: "terminal-return",
        label: "Terminal return",
        value: terminalReturn,
        detail: terminalProjection ? `${terminalProjection.fiscal_year}E dividend-incl CAGR` : "waiting for forecast row"
      },
      {
        key: "target-multiple",
        label: "Target multiple",
        value: `${formatNumber(forecastMeta.target_multiple)}x`,
        detail: `${forecastMeta.case ?? consensus?.case ?? "custom"} case`
      },
      {
        key: "audit-coverage",
        label: "Audit coverage",
        value: `${forecastSourceContract.auditComplete}/${forecastSourceContract.auditRows}`,
        detail: forecastSourceContract.statusLabel
      },
      {
        key: "manual-inputs",
        label: "Manual EPS",
        value: `${manualOverrideCount}/${forecastYears}`,
        detail: manualOverrideCount ? "user override active" : "formula path active"
      },
      {
        key: "consensus",
        label: "Consensus",
        value: missingYears.length ? `missing ${missingYears.length}` : `${forecastEvidence.cases.length} cases`,
        detail: missingYears.length ? `FY ${missingYears.join(", ")}` : consensus?.revision_status ?? "snapshot ready"
      },
      {
        key: "ai-guard",
        label: "AI guard",
        value: llmGeneratedNumbers,
        detail: aiRoleLabel
      }
    ],
    audit: [
      { key: "source", label: "Source", value: sourceLabel },
      { key: "method", label: "Method", value: methodLabel },
      { key: "formula", label: "Formula", value: forecastMeta.formula },
      { key: "flags", label: "Flags", value: firstAuditFlag ?? forecastSourceContract.firstMissingLabel }
    ],
    gates: [
      {
        key: "input-basis",
        label: "Inputs separated",
        value: inputBasis,
        detail: "consensus / user / formula lanes",
        tone: manualOverrideCount ? "warn" : "ok"
      },
      {
        key: "return-calculated",
        label: "Return calculated",
        value: terminalReturn,
        detail: "target price + dividend CAGR",
        tone: terminalProjection ? "ok" : "warn"
      },
      {
        key: "risk-check",
        label: "Risk checked",
        value: riskStatus,
        detail: "MoS, missing years, quality flags",
        tone: firstAuditFlag || missingYears.length ? "warn" : "ok"
      },
      {
        key: "audit-ready",
        label: "Audit ready",
        value: sourceReady ? "source trace ready" : "source gaps",
        detail: `${forecastSourceContract.auditComplete}/${forecastSourceContract.auditRows} audit facts`,
        tone: sourceReady ? "ok" : "warn"
      },
      {
        key: "ai-boundary",
        label: "AI boundary",
        value: llmGeneratedNumbers,
        detail: aiRoleLabel,
        tone: llmGeneratedNumbers === "No LLM numbers" ? "ok" : "warn"
      }
    ]
  };
}

function buildForecastConsensusPreflight({
  ticker,
  forecastMeta,
  forecastEvidence,
  forecastYears,
  missingConsensusYears,
  forecastSourceContract
}: {
  ticker?: string | null;
  forecastMeta: ForecastMeta;
  forecastEvidence: ForecastEvidence;
  forecastYears: number;
  missingConsensusYears: number[];
  forecastSourceContract: ReturnType<typeof buildForecastSourceContract>;
}) {
  const targetTicker = ticker?.trim() || "005930.KS";
  const consensusCsvPath = forecastConsensusCsvPath(targetTicker);
  const consensusWorkpaperPath = forecastConsensusWorkpaperPath(consensusCsvPath);
  const consensusQuality =
    forecastMeta.consensus?.quality_status ??
    forecastEvidence.meta?.quality_status ??
    traceText(forecastMeta.source_trace?.quality_status) ??
    "not_loaded";
  const hasSourceBackedConsensus =
    consensusQuality.includes("source_backed") || consensusQuality.includes("user_provided");
  const usesFixtureProxy = consensusQuality.includes("fixture") || consensusQuality.includes("proxy");
  const consensusReady =
    hasSourceBackedConsensus &&
    !usesFixtureProxy &&
    missingConsensusYears.length === 0 &&
    forecastEvidence.cases.length > 0;
  const status = consensusReady
    ? "source-backed consensus ready"
    : missingConsensusYears.length
      ? "source-backed consensus incomplete"
      : "source-backed consensus required";
  const missingYearsLabel = missingConsensusYears.length
    ? `Missing FY ${missingConsensusYears.join(", ")}`
    : consensusReady
      ? `${forecastYears}Y consensus runway loaded`
      : "No production consensus snapshot loaded";
  const auditReady =
    forecastSourceContract.auditRows > 0 &&
    forecastSourceContract.auditComplete === forecastSourceContract.auditRows;

  return {
    tone: consensusReady ? "ready" : "blocked",
    status,
    missingYearsLabel,
    checks: [
      {
        key: "snapshots",
        label: "Snapshot source",
        value: consensusReady ? "production" : consensusQuality,
        detail: "requires point-in-time consensus CSV or API snapshot",
        tone: consensusReady ? "ok" : "warn"
      },
      {
        key: "runway",
        label: "1Y-5Y runway",
        value: missingConsensusYears.length ? `missing ${missingConsensusYears.length}` : `${forecastYears}/${forecastYears} years`,
        detail: missingYearsLabel,
        tone: missingConsensusYears.length ? "warn" : "ok"
      },
      {
        key: "cases",
        label: "Case coverage",
        value: `${forecastEvidence.cases.length} cases`,
        detail: "low / median or current / high remain separated",
        tone: forecastEvidence.cases.length ? "ok" : "warn"
      },
      {
        key: "lineage",
        label: "Trace anchor",
        value: auditReady ? "audit linked" : "audit pending",
        detail: "source_url, source_document_id, filing_id, formula required",
        tone: auditReady ? "ok" : "warn"
      }
    ],
    commands: [
      {
        key: "workpaper",
        value: `python -m services.ingestion_worker.cli consensus-workpaper --tickers ${targetTicker} --csv-path ${consensusCsvPath} --template-cases median --validation-cases median,current --case-mode any --out ${consensusWorkpaperPath}`
      },
      {
        key: "template",
        value: `python -m services.ingestion_worker.cli export-consensus-template --tickers ${targetTicker} --cases median --out ${consensusCsvPath}`
      },
      {
        key: "validate",
        value: `python -m services.ingestion_worker.cli validate-consensus-csv --path ${consensusCsvPath} --tickers ${targetTicker} --cases median,current --case-mode any --strict`
      },
      {
        key: "import",
        value: `python -m services.ingestion_worker.cli import-consensus-csv --path ${consensusCsvPath} --persist`
      }
    ]
  };
}

function forecastConsensusCsvPath(ticker: string): string {
  const normalized = ticker.trim().toUpperCase();
  if (!normalized || normalized.includes(",")) {
    return "storage/imports/consensus_estimates.csv";
  }
  const withoutMarketSuffix = normalized.replace(/\.(KS|KQ|T|US)$/u, "");
  const slug = withoutMarketSuffix
    .replace(/[^A-Z0-9]+/gu, "_")
    .replace(/^_+|_+$/gu, "")
    .replace(/_+/gu, "_")
    .toLowerCase();
  return `storage/imports/consensus_${slug || "custom"}.csv`;
}

function forecastConsensusWorkpaperPath(csvPath: string): string {
  return csvPath.endsWith(".csv") ? `${csvPath.slice(0, -4)}_workpaper.md` : `${csvPath}_workpaper.md`;
}

function forecastSourceTraceGuard(forecastMeta: ForecastMeta): Array<{ label: string; value: string }> {
  const trace = forecastMeta.source_trace ?? {};
  const inputDocuments = Array.isArray(trace.input_source_document_ids)
    ? trace.input_source_document_ids
        .map((value) => String(value))
        .filter(Boolean)
    : [];
  const llmGenerated = trace.llm_generated_numbers;
  const llmLabel =
    llmGenerated === false
      ? "No LLM numbers"
      : llmGenerated === true
        ? "LLM generated"
        : "Not declared";
  return [
    { label: "Method", value: traceText(trace.method) ?? forecastMeta.source ?? forecastMeta.mode },
    { label: "Numbers", value: llmLabel },
    { label: "AI role", value: traceText(trace.ai_role) ?? "not_used" },
    { label: "Assumption ID", value: traceText(trace.source_document_id) ?? "pending" },
    { label: "Period", value: traceText(trace.period) ?? `${forecastMeta.years}Y` },
    { label: "Inputs", value: inputDocuments.length ? `${inputDocuments.length} source docs` : "source trace pending" }
  ];
}

function isForecastP1AuditFact(factName: string | undefined) {
  return Boolean(
    factName?.startsWith("forecast.") ||
      factName?.startsWith("forecast_snapshot.") ||
      factName?.startsWith("forecast_case.") ||
      factName?.startsWith("forecast_scenario.") ||
      factName?.startsWith("forecast_assumption.")
  );
}

const forecastSourceTraceRequiredFields = [
  "source",
  "source_document_id",
  "filing_id",
  "period",
  "unit",
  "currency",
  "method",
  "formula"
] as const;

function buildForecastSourceContract(
  projectionRows: ValuationRow[],
  forecastAuditRows: AuditRow[],
  forecastMeta: ForecastMeta
) {
  const projectionComplete = projectionRows.filter((row) => missingForecastTraceFields(row.source_trace).length === 0).length;
  const auditComplete = forecastAuditRows.filter((row) => missingForecastTraceFields(row.source_trace).length === 0).length;
  const metaMissingFields = missingForecastTraceFields(forecastMeta.source_trace);
  const firstProjectionGap = projectionRows.find((row) => missingForecastTraceFields(row.source_trace).length > 0);
  const firstAuditGap = forecastAuditRows.find((row) => missingForecastTraceFields(row.source_trace).length > 0);
  const allReady =
    projectionRows.length > 0 &&
    projectionComplete === projectionRows.length &&
    forecastAuditRows.length > 0 &&
    auditComplete === forecastAuditRows.length &&
    metaMissingFields.length === 0;

  return {
    projectionRows: projectionRows.length,
    projectionComplete,
    auditRows: forecastAuditRows.length,
    auditComplete,
    metaReady: metaMissingFields.length === 0,
    statusLabel: allReady ? "all forecast rows storage-ready" : "forecast source gaps",
    firstMissingLabel: forecastMissingLabel(firstProjectionGap, firstAuditGap, metaMissingFields)
  };
}

function forecastMissingLabel(
  firstProjectionGap: ValuationRow | undefined,
  firstAuditGap: AuditRow | undefined,
  metaMissingFields: string[]
) {
  if (firstProjectionGap) {
    return `${firstProjectionGap.fiscal_year}E projection missing ${missingForecastTraceFields(firstProjectionGap.source_trace).join(", ")}`;
  }
  if (firstAuditGap) {
    return `${firstAuditGap.fact_name ?? firstAuditGap.fact_id} missing ${missingForecastTraceFields(firstAuditGap.source_trace).join(", ")}`;
  }
  if (metaMissingFields.length) {
    return `forecast assumptions missing ${metaMissingFields.join(", ")}`;
  }
  return "no missing forecast storage fields";
}

function missingForecastTraceFields(trace: Record<string, unknown> | undefined) {
  return forecastSourceTraceRequiredFields.filter((field) => {
    const value = trace?.[field];
    return value === null || value === undefined || value === "";
  });
}

function forecastP1StateChips({
  forecastMeta,
  forecastEvidence,
  forecastAuditRows,
  projectionRows,
  manualOverrideCount
}: {
  forecastMeta: ForecastMeta;
  forecastEvidence: ForecastEvidence;
  forecastAuditRows: AuditRow[];
  projectionRows: ValuationRow[];
  manualOverrideCount: number;
}): Array<{ label: string; value: string; tone: string }> {
  const hasConsensus = Boolean(forecastMeta.consensus) || forecastEvidence.cases.length > 0;
  const staleEstimate = forecastAuditRows.some(
    (row) =>
      row.quality_status.toLowerCase().includes("stale") ||
      row.flags.some((flag) => flag.toLowerCase().includes("stale"))
  );
  const hasForecastLineage = projectionRows.length > 0 && forecastAuditRows.length > 0;
  const userOnlyAvailable =
    manualOverrideCount > 0 ||
    forecastMeta.mode === "custom" ||
    forecastMeta.source === "user_input" ||
    !hasConsensus;

  return [
    { label: "loading estimates", value: hasConsensus ? "resolved" : "active", tone: hasConsensus ? "ok" : "warn" },
    { label: "no consensus", value: hasConsensus ? "not active" : "active", tone: hasConsensus ? "ok" : "warn" },
    { label: "user-only", value: userOnlyAvailable ? "available" : "standby", tone: userOnlyAvailable ? "ok" : "neutral" },
    { label: "stale estimate", value: staleEstimate ? "active" : "not active", tone: staleEstimate ? "warn" : "ok" },
    { label: hasForecastLineage ? "no source rejected" : "source_trace required", value: `${forecastAuditRows.length} audit rows`, tone: hasForecastLineage ? "ok" : "warn" }
  ];
}

function traceText(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function buildForecastCaseComparisonRows({
  forecastEvidence,
  auditRows,
  auditQueryString
}: {
  forecastEvidence: ForecastEvidence;
  auditRows: AuditRow[];
  auditQueryString: string;
}) {
  const labels: Record<string, string> = {
    low: "Bear",
    median: "Base",
    high: "Bull"
  };
  const auditFor = (caseName: string, factName: string) =>
    auditRows.find(
      (row) =>
        row.fiscal_year === forecastEvidence.forecast_year &&
        row.fact_name === `forecast_case.${caseName}.${factName}`
    );
  const snapshotAuditFor = (caseName: string) =>
    auditRows.find(
      (row) =>
        row.fiscal_year === forecastEvidence.forecast_year &&
        row.fact_name === `forecast_snapshot.${caseName}.estimate_eps`
    );

  return forecastEvidence.cases.map((row) => {
    const targetAudit = auditFor(row.case, "target_price");
    const totalReturnAudit = auditFor(row.case, "total_return_cagr_pct");
    const marginOfSafetyAudit = auditFor(row.case, "margin_of_safety_pct");
    const estimateAudit = snapshotAuditFor(row.case);
    return {
      caseName: row.case,
      label: labels[row.case] ?? row.case,
      estimateEps: formatNumber(row.estimate_eps),
      estimateHref: estimateAudit ? auditFactHref(estimateAudit.fact_id, auditQueryString) : undefined,
      growthRate: formatMaybeGrowth(row.growth_rate_pct),
      targetPrice: formatNumber(targetAudit?.value),
      targetHref: targetAudit ? auditFactHref(targetAudit.fact_id, auditQueryString) : undefined,
      totalReturnCagr: formatMaybeGrowth(totalReturnAudit?.value),
      marginOfSafety: formatMaybeGrowth(marginOfSafetyAudit?.value),
      quality:
        totalReturnAudit?.quality_status ??
        targetAudit?.quality_status ??
        estimateAudit?.quality_status ??
        String(row.source_trace?.quality_status ?? forecastEvidence.meta?.quality_status ?? "pending_audit_row"),
      auditFactId: totalReturnAudit?.fact_id ?? targetAudit?.fact_id ?? estimateAudit?.fact_id
    };
  });
}

function buildScenarioWorkbenchRows({
  valuation,
  projectionRows,
  calculationLines,
  hiddenScenarioLines,
  auditRows,
  auditQueryString
}: {
  valuation: ValuationRow[];
  projectionRows: ValuationRow[];
  calculationLines: ReturnType<typeof forecastCalculationLines>;
  hiddenScenarioLines: string[];
  auditRows: AuditRow[];
  auditQueryString: string;
}) {
  const latestHistoricalRow = [...valuation]
    .reverse()
    .find((row) => !row.forecast_flag && Number(row.price) > 0);
  const terminalProjectionRow = projectionRows.at(-1);
  const startPrice = toNumberOrNull(latestHistoricalRow?.price);
  const startYear = latestHistoricalRow?.fiscal_year ?? null;
  return calculationLines.map((line) => {
    const terminalPoint =
      line.points.find((point) => point.fiscal_year === terminalProjectionRow?.fiscal_year) ??
      line.points.at(-1);
    const terminalYear = terminalPoint?.fiscal_year ?? terminalProjectionRow?.fiscal_year ?? 0;
    const terminalTargetPrice = toNumberOrNull(terminalPoint?.target_price);
    const years = startYear !== null && terminalYear > startYear ? terminalYear - startYear : projectionRows.length;
    const cumulativeDividend = projectionRows
      .filter((row) => row.fiscal_year <= terminalYear)
      .reduce((total, row) => total + (toNumberOrNull(row.dividend) ?? 0), 0);
    const totalReturnCagrPct =
      startPrice !== null && terminalTargetPrice !== null && startPrice > 0 && terminalTargetPrice + cumulativeDividend > 0 && years > 0
        ? (Math.pow((terminalTargetPrice + cumulativeDividend) / startPrice, 1 / years) - 1) * 100
        : null;
    const auditRow = auditRows.find(
      (row) => row.fiscal_year === terminalYear && row.fact_name === `forecast_scenario.${line.label}.target_price`
    );
    return {
      label: line.label,
      multiple: `${formatNumber(line.multiple)}x`,
      terminalYear,
      terminalTargetPrice,
      totalReturnCagrPct,
      visible: !hiddenScenarioLines.includes(line.label),
      method: auditRow?.method ?? "forecast_scenario_derived",
      quality: auditRow?.quality_status ?? "pending_audit_row",
      auditFactId: auditRow?.fact_id,
      href: auditRow ? auditFactHref(auditRow.fact_id, auditQueryString) : undefined
    };
  });
}

function buildForecastAiReviewNotes({
  forecastMeta,
  forecastEvidence,
  projectionRows,
  forecastYears,
  manualEps,
  hiddenScenarioLines,
  auditRows,
  auditQueryString
}: {
  forecastMeta: ForecastMeta;
  forecastEvidence: ForecastEvidence;
  projectionRows: ValuationRow[];
  forecastYears: number;
  manualEps: string[];
  hiddenScenarioLines: string[];
  auditRows: AuditRow[];
  auditQueryString: string;
}): ForecastAiReviewNote[] {
  const terminalRow = projectionRows.at(-1);
  const terminalAuditRow = valuationAuditRowFor(terminalRow, auditRows, "total_return_cagr_pct");
  const terminalReturn = toNumberOrNull(terminalRow?.total_return_cagr_pct);
  const consensusEvidence = selectedForecastConsensusEvidence({
    forecastEvidence,
    forecastMeta,
    forecastCase: forecastMeta.case ?? forecastMeta.consensus?.case ?? "median",
    auditRows,
    auditQueryString
  });
  const missingConsensusYears = forecastMeta.consensus?.missing_years ?? [];
  const manualCount = manualEps.slice(0, forecastYears).filter((value) => value.trim()).length;
  const calculationLines = forecastCalculationLines(forecastMeta);
  const activeLineCount = calculationLines.filter((line) => !hiddenScenarioLines.includes(line.label)).length;
  const sourceQuality =
    forecastMeta.consensus?.quality_status ??
    forecastEvidence.meta?.quality_status ??
    String(forecastMeta.source_trace?.quality_status ?? "deterministic");

  return [
    {
      label: "Return setup",
      value: terminalReturn !== null ? `${formatMaybeGrowth(terminalReturn)} ${forecastYears}Y CAGR` : "-",
      detail: terminalReturn === null
        ? "Terminal return is not available for the active forecast horizon."
        : terminalReturn < 0
          ? "The current assumptions imply a negative terminal return profile."
          : "The current assumptions imply a positive terminal return profile.",
      method: terminalAuditRow?.method ?? terminalRow?.forecast_source ?? forecastMeta.source ?? "deterministic_forecast",
      quality: terminalAuditRow?.quality_status ?? sourceQuality,
      href: terminalAuditRow ? auditFactHref(terminalAuditRow.fact_id, auditQueryString) : undefined
    },
    {
      label: "Consensus evidence",
      value: consensusEvidence
        ? `${formatNumber(consensusEvidence.estimateEps)} FY${consensusEvidence.fiscalYear} ${consensusEvidence.caseLabel}`
        : "pending",
      detail: missingConsensusYears.length
        ? `Missing source-backed consensus years: ${missingConsensusYears.join(", ")}.`
        : "Selected consensus snapshot is separated from manual EPS inputs.",
      method: consensusEvidence?.method ?? "forecast_consensus_snapshot",
      quality: consensusEvidence?.quality ?? sourceQuality,
      href: consensusEvidence?.href
    },
    {
      label: "Assumption layer",
      value: manualCount ? `${manualCount}/${forecastYears} manual EPS inputs` : `${forecastMeta.mode} mode`,
      detail: forecastMeta.mode === "ai_review"
        ? "AI Review uses a deterministic review blend and does not generate EPS."
        : "Switch to AI Review to inspect assumptions without changing source contracts.",
      method: forecastMeta.source ?? "forecast_mode",
      quality: manualCount ? "user_input" : sourceQuality
    },
    {
      label: "Scenario controls",
      value: calculationLines.length ? `${activeLineCount}/${calculationLines.length} valuation lines active` : "not generated",
      detail: "Scenario lines are display overlays; hiding a line does not change the forecast dataset.",
      method: "ui_visibility_state",
      quality: "user_controlled"
    }
  ];
}

function selectedForecastConsensusEvidence({
  forecastEvidence,
  forecastMeta,
  forecastCase,
  auditRows,
  auditQueryString
}: {
  forecastEvidence: ForecastEvidence;
  forecastMeta: ForecastMeta;
  forecastCase: string;
  auditRows: AuditRow[];
  auditQueryString: string;
}): AskConsensusEvidence | undefined {
  const supportedCases = new Set(["low", "median", "high"]);
  const preferredCase = [forecastCase, forecastMeta.case, forecastMeta.consensus?.case, "median"].find(
    (item): item is string => Boolean(item && supportedCases.has(item))
  );
  const selectedCase =
    forecastEvidence.cases.find((row) => row.case === preferredCase) ??
    forecastEvidence.cases.find((row) => row.case === "median") ??
    forecastEvidence.cases[0];
  if (!selectedCase) {
    return undefined;
  }

  const factName = `forecast_snapshot.${selectedCase.case}.estimate_eps`;
  const auditRow = auditRows.find(
    (row) => row.fiscal_year === forecastEvidence.forecast_year && row.fact_name === factName
  );
  const trace = selectedCase.source_trace ?? {};
  return {
    caseLabel: selectedCase.case,
    fiscalYear: forecastEvidence.forecast_year,
    estimateEps: selectedCase.estimate_eps,
    growthRatePct: selectedCase.growth_rate_pct,
    analystCount: forecastMeta.consensus?.analyst_count ?? forecastMeta.analyst_count ?? null,
    method: auditRow?.method ?? String(trace.source_type ?? forecastMeta.source ?? "forecast_consensus_snapshot"),
    quality: auditRow?.quality_status ?? String(trace.quality_status ?? forecastMeta.consensus?.quality_status ?? forecastEvidence.meta?.quality_status ?? "pending_audit_row"),
    href: auditRow ? auditFactHref(auditRow.fact_id, auditQueryString) : undefined
  };
}

function forecastAssumptionLedger(
  forecastMeta: ForecastMeta,
  forecastEvidence: ForecastEvidence,
  forecastYears: number,
  manualEps: string[]
) {
  const trace = forecastMeta.source_trace ?? forecastEvidence.source_trace ?? {};
  const source = String(trace.source_type ?? forecastMeta.source ?? "deterministic_formula");
  const quality = String(
    trace.quality_status ??
    forecastMeta.consensus?.quality_status ??
    forecastEvidence.meta?.quality_status ??
    "unknown_quality_status"
  );
  const manualOverrides = manualEps.slice(0, forecastYears).filter((value) => value.trim()).length;
  return [
    {
      label: "Mode",
      value: forecastMeta.mode,
      source,
      quality
    },
    {
      label: "Case",
      value: forecastMeta.case ?? forecastMeta.consensus?.case ?? "median",
      source: forecastMeta.consensus?.source_note ?? source,
      quality: forecastMeta.consensus?.quality_status ?? quality
    },
    {
      label: "Growth rate",
      value: formatMaybeGrowth(forecastMeta.growth_rate_pct),
      source,
      quality
    },
    {
      label: "Target multiple",
      value: `${forecastMeta.target_multiple}x`,
      source: "user_input_or_selected_multiple",
      quality
    },
    {
      label: "Manual EPS overrides",
      value: `${manualOverrides}/${forecastYears}`,
      source: manualOverrides ? "user_input" : "none",
      quality: manualOverrides ? "explicit_user_input" : "not_used"
    },
    {
      label: "Formula",
      value: forecastMeta.formula,
      source: "deterministic_formula",
      quality
    }
  ];
}

function buildForecastCalculatorCards({
  forecastMeta,
  forecastEvidence,
  projectionRows,
  forecastYears,
  forecastMode,
  manualEps,
  auditRows,
  auditQueryString
}: {
  forecastMeta: ForecastMeta;
  forecastEvidence: ForecastEvidence;
  projectionRows: ValuationRow[];
  forecastYears: number;
  forecastMode: string;
  manualEps: string[];
  auditRows: AuditRow[];
  auditQueryString: string;
}) {
  const selectedMode = normalizeForecastMode(forecastMode);
  const activeProjection = projectionRows.at(-1);
  const activeTarget = activeProjection ? formatNumber(activeProjection.price) : "-";
  const activeReturn = activeProjection ? formatMaybeGrowth(activeProjection.total_return_cagr_pct) : "-";
  const activeMarginOfSafety = activeProjection ? formatMaybeGrowth(activeProjection.margin_of_safety_pct) : "-";
  const manualCount = manualEps.slice(0, forecastYears).filter((value) => value.trim()).length;
  const formulaAuditRow = auditRows.find((row) => row.fact_name === "forecast_assumption.formula");
  const targetAuditRow = auditRows.find((row) => row.fact_name === "forecast_assumption.target_multiple");
  const formulaAuditHref = formulaAuditRow ? auditFactHref(formulaAuditRow.fact_id, auditQueryString) : undefined;
  const targetAuditHref = targetAuditRow ? auditFactHref(targetAuditRow.fact_id, auditQueryString) : formulaAuditHref;
  const quality =
    forecastMeta.consensus?.quality_status ??
    forecastEvidence.meta?.quality_status ??
    String(forecastMeta.source_trace?.quality_status ?? "deterministic");
  const cardBase = [
    {
      mode: "estimates",
      aliases: ["estimates", "consensus"],
      title: "Estimates",
      description: "Consensus EPS snapshot path",
      input: forecastMeta.consensus
        ? `${forecastMeta.case ?? forecastMeta.consensus.case} / ${forecastMeta.consensus.analyst_count} analysts`
        : "consensus not loaded",
      quality,
      auditHref: formulaAuditHref
    },
    {
      mode: "normal_multiple",
      aliases: ["normal_multiple"],
      title: "Normal Multiple",
      description: "Forward EPS at historical normal P/E",
      input: forecastMeta.normal_multiple?.window_years
        ? `${forecastMeta.normal_multiple.window_years}FY normal window`
        : "selected normal multiple",
      quality,
      auditHref: targetAuditHref
    },
    {
      mode: "lt_growth",
      aliases: ["lt_growth"],
      title: "LT Growth",
      description: "Long-term growth assumption path",
      input: formatMaybeGrowth(forecastMeta.consensus?.lt_growth_rate_pct),
      quality,
      auditHref: formulaAuditHref
    },
    {
      mode: "historical_cagr",
      aliases: ["historical_cagr"],
      title: "Historical CAGR",
      description: "Past growth carried forward",
      input: selectedMode === "historical_cagr" ? formatMaybeGrowth(forecastMeta.growth_rate_pct) : "run to calculate",
      quality: String(forecastMeta.source_trace?.quality_status ?? "deterministic_formula"),
      auditHref: formulaAuditHref
    },
    {
      mode: "custom",
      aliases: ["custom"],
      title: "Custom",
      description: "User EPS and target multiple",
      input: `${manualCount}/${forecastYears} manual EPS`,
      quality: manualCount ? "explicit_user_input" : "deterministic_formula",
      auditHref: formulaAuditHref
    }
  ];

  return cardBase.map((card) => {
    const active = card.aliases.includes(selectedMode);
    return {
      ...card,
      active,
      target: active ? activeTarget : "run to calculate",
      returnLabel: active ? activeReturn : "run to calculate",
      marginOfSafety: active ? activeMarginOfSafety : "run to calculate"
    };
  });
}

function normalizeForecastMode(mode: string) {
  const normalized = mode.toLowerCase().replace(/-/g, "_");
  return normalized === "consensus" ? "estimates" : normalized;
}

function valuationAuditRowFor(row: ValuationRow | undefined, auditRows: AuditRow[], factName: string) {
  if (!row) {
    return undefined;
  }
  const scope = row.forecast_flag ? "forecast" : "valuation";
  return auditRows.find(
    (auditRow) => auditRow.fiscal_year === row.fiscal_year && auditRow.fact_name === `${scope}.${factName}`
  );
}

function auditTestIdPart(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function formatConsensusRange(consensus: ForecastMeta["consensus"] | undefined) {
  if (!consensus) {
    return "not loaded";
  }
  const low = formatMaybeGrowth(consensus.low_growth_rate_pct);
  const median = formatMaybeGrowth(consensus.median_growth_rate_pct);
  const high = formatMaybeGrowth(consensus.high_growth_rate_pct);
  if (low !== "-" && median !== "-" && high !== "-") {
    return `${low} / ${median} / ${high}`;
  }
  return `selected ${formatMaybeGrowth(consensus.selected_growth_rate_pct)}`;
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

function formatMaybeGrowth(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return String(raw);
  }
  return `${value.toFixed(1)}%`;
}

function toNumberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
