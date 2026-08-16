"use client";

import { useEffect, useMemo, useState } from "react";
import type {
  AuditRow,
  IndustrySeriesRow,
  KrValuationCacheCoverage,
  MacroSeriesRow,
  SourceSeriesMeta
} from "../lib/terminal-types";
import {
  auditFactHref,
  auditTestIdPart,
  auditTraceSections,
  publicTraceSummary,
  sourceDocumentHref
} from "../lib/audit-utils";
import { Metric } from "./terminal-primitives";

export function DataAuditPanel({
  rows,
  auditQueryString,
  macroSeries,
  macroMeta,
  industrySeries,
  industryMeta,
  krCacheCoverage,
  focusedFactId,
  focusedFactFamily,
  onFocusedFactIdChange,
  onFocusedFactFamilyChange
}: {
  rows: AuditRow[];
  auditQueryString: string;
  macroSeries: MacroSeriesRow[];
  macroMeta: SourceSeriesMeta;
  industrySeries: IndustrySeriesRow[];
  industryMeta: SourceSeriesMeta;
  krCacheCoverage?: KrValuationCacheCoverage | null;
  focusedFactId?: string | null;
  focusedFactFamily?: string | null;
  onFocusedFactIdChange?: (factId: string) => void;
  onFocusedFactFamilyChange?: (factFamily: string) => void;
}) {
  const sourceFactRows = rows.filter(isFinancialFactAuditRow);
  const derivedRows = rows.length - sourceFactRows.length;
  const qualityFlagCount = rows.filter((row) => {
    const quality = row.source_trace.quality_status ?? row.quality_status;
    return quality && !String(quality).includes("passed");
  }).length;
  const namespaceCounts = useMemo(() => {
    const counts = new Map<string, number>();
    rows.forEach((row) => {
      const namespace = auditRowNamespace(row);
      counts.set(namespace, (counts.get(namespace) ?? 0) + 1);
    });
    return Array.from(counts, ([key, count]) => ({
      key,
      label: auditNamespaceLabel(key),
      count
    })).sort((left, right) => {
      const leftRank = auditNamespaceRank(left.key);
      const rightRank = auditNamespaceRank(right.key);
      return leftRank === rightRank ? left.label.localeCompare(right.label) : leftRank - rightRank;
    });
  }, [rows]);
  const factFamilyCounts = useMemo(() => buildAuditFactFamilyCounts(rows), [rows]);
  const [activeNamespace, setActiveNamespace] = useState("all");
  const [activeFactFamily, setActiveFactFamily] = useState("all");
  const applyFactFamily = (factFamily: string) => {
    setActiveFactFamily(factFamily);
    onFocusedFactFamilyChange?.(factFamily);
  };
  const applyNamespace = (namespace: string) => {
    setActiveNamespace(namespace);
    applyFactFamily("all");
  };
  useEffect(() => {
    if (activeNamespace !== "all" && !namespaceCounts.some((item) => item.key === activeNamespace)) {
      setActiveNamespace("all");
    }
  }, [activeNamespace, namespaceCounts]);
  useEffect(() => {
    const nextFactFamily = focusedFactFamily ?? "all";
    const exists = nextFactFamily === "all" || factFamilyCounts.some((item) => item.key === nextFactFamily);
    if (exists && activeFactFamily !== nextFactFamily) {
      setActiveFactFamily(nextFactFamily);
    }
  }, [activeFactFamily, factFamilyCounts, focusedFactFamily]);
  useEffect(() => {
    if (activeFactFamily !== "all" && !factFamilyCounts.some((item) => item.key === activeFactFamily)) {
      applyFactFamily("all");
    }
  }, [activeFactFamily, factFamilyCounts]);
  const visibleRows = useMemo(
    () =>
      rows.filter((row) => {
        const namespaceMatches = activeNamespace === "all" || auditRowNamespace(row) === activeNamespace;
        const familyMatches = activeFactFamily === "all" || auditFactFamily(row).key === activeFactFamily;
        return namespaceMatches && familyMatches;
      }),
    [activeFactFamily, activeNamespace, rows]
  );
  const [selectedFactId, setSelectedFactId] = useState<string | null>(null);
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false);
  const [sourceDocumentState, setSourceDocumentState] = useState<SourceDocumentState | null>(null);
  const effectiveSelectedFactId = focusedFactId ?? selectedFactId;
  const selectedAuditRow = useMemo(
    () => {
      const selectedVisibleRow = visibleRows.find((row) => row.fact_id === effectiveSelectedFactId);
      if (selectedVisibleRow) {
        return selectedVisibleRow;
      }
      if (activeNamespace !== "all" || activeFactFamily !== "all") {
        return visibleRows[0] ?? rows.find((row) => row.fact_id === effectiveSelectedFactId);
      }
      return rows.find((row) => row.fact_id === effectiveSelectedFactId) ?? visibleRows[0];
    },
    [activeFactFamily, activeNamespace, effectiveSelectedFactId, rows, visibleRows]
  );
  const selectFactId = (factId: string) => {
    const row = rows.find((candidate) => candidate.fact_id === factId);
    setSelectedFactId(factId);
    setMobileInspectorOpen(true);
    onFocusedFactIdChange?.(factId);
    if (row) {
      onFocusedFactFamilyChange?.(auditFactFamily(row).key);
    }
  };
  const openSourceDocument = async (sourceDocumentId: string) => {
    setSourceDocumentState({ sourceDocumentId, status: "loading" });
    try {
      const response = await fetch(sourceDocumentHref(sourceDocumentId));
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(String(payload?.detail ?? "source document resolver failed"));
      }
      setSourceDocumentState({
        sourceDocumentId,
        status: "loaded",
        data: normalizeSourceDocumentResolution(payload?.data, sourceDocumentId)
      });
    } catch (error) {
      setSourceDocumentState({
        sourceDocumentId,
        status: "error",
        error: error instanceof Error ? error.message : String(error)
      });
    }
  };

  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Data Audit</h1>
          <p>Every displayed valuation, forecast, snapshot, and financial fact carries source document, filing id, period, unit, currency, formula, and quality status.</p>
        </div>
        <div className="facts-row">
          <Metric label="Source facts" value={String(sourceFactRows.length)} />
          <Metric label="Derived rows" value={String(derivedRows)} />
          <Metric label="Macro obs" value={String(macroSeries.length)} />
          <Metric label="Industry obs" value={String(industrySeries.length)} />
          <Metric label="Quality flags" value={String(qualityFlagCount)} />
        </div>
        <div className="source-trace-guard-chip" data-testid="data-audit-source-guard">
          No source_trace = reject display
        </div>
      </div>
      <section className="source-ledger" aria-label="Macro and industry evidence">
        <div className="panel-header compact">
          <div>
            <h2>Macro & Industry Evidence</h2>
            <p>Source-backed FRED, ECOS, KOSIS, and e-Stat observations exposed without fixture substitution.</p>
          </div>
          <div className="facts-row">
            <Metric label="Macro mode" value={macroMeta.data_mode} />
            <Metric label="Industry mode" value={industryMeta.data_mode} />
          </div>
        </div>
        <div className="source-ledger-grid">
          <SourceSeriesTable
            title="Macro Series"
            rows={macroSeries.map((row) => ({
              key: `${row.source}-${row.series_id}-${row.observation_date}`,
              scope: row.source,
              label: row.series_id,
              date: row.observation_date,
              value: row.value,
              unit: row.unit ?? "-",
              quality: String(row.source_trace.quality_status ?? macroMeta.quality_status),
              sourceDocument: row.source_document_id,
              trace: row.source_trace
            }))}
            emptyText={macroMeta.source_note}
            onOpenSourceDocument={openSourceDocument}
          />
          <SourceSeriesTable
            title="Industry Series"
            rows={industrySeries.map((row) => ({
              key: `${row.source}-${row.series_id}-${row.observation_date}`,
              scope: row.market,
              label: row.industry || row.category || row.series_id,
              date: row.observation_date,
              value: row.value,
              unit: row.unit ?? "-",
              quality: String(row.source_trace.quality_status ?? industryMeta.quality_status),
              sourceDocument: row.source_document_id,
              trace: row.source_trace
            }))}
            emptyText={industryMeta.source_note}
            onOpenSourceDocument={openSourceDocument}
          />
        </div>
      </section>
      <KrCacheDiagnostics
        coverage={krCacheCoverage}
        auditRows={rows}
        factQueryString={auditQueryString}
        onInspectFact={selectFactId}
        onOpenSourceDocument={openSourceDocument}
      />
      <section className="audit-family-ledger" aria-label="Data Audit fact family filters" data-testid="audit-family-ledger">
        <div className="panel-header compact">
          <div>
            <h2>Fact Families</h2>
            <p>Group audit rows by user-facing evidence type: source warehouse metrics, source prices, and valuation-derived calculations.</p>
          </div>
          <div className="facts-row">
            <Metric label="Active family" value={activeFactFamily === "all" ? "All" : auditFactFamilyLabel(activeFactFamily)} />
            <Metric label="Visible rows" value={String(visibleRows.length)} />
          </div>
        </div>
        <div className="audit-family-grid">
          <button
            type="button"
            className={`audit-family-button ${activeFactFamily === "all" ? "active" : ""}`}
            data-testid="audit-family-all"
            aria-pressed={activeFactFamily === "all"}
            onClick={() => applyFactFamily("all")}
          >
            <span>All evidence</span>
            <strong>{rows.length}</strong>
            <small>All source and derived facts</small>
          </button>
          {factFamilyCounts.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`audit-family-button ${activeFactFamily === item.key ? "active" : ""}`}
              data-testid={`audit-family-${auditTestIdPart(item.key)}`}
              aria-label={`Filter audit fact family ${item.label}`}
              aria-pressed={activeFactFamily === item.key}
              onClick={() => applyFactFamily(item.key)}
            >
              <span>{item.label}</span>
              <strong>{item.count}</strong>
              <small>{item.detail}</small>
            </button>
          ))}
        </div>
      </section>
      <section className="audit-namespace-ledger" aria-label="Data Audit namespace filters" data-testid="audit-namespace-ledger">
        <div className="panel-header compact">
          <div>
            <h2>Audit Namespaces</h2>
            <p>Filter source-traced facts by valuation area, including price points, scenario lines, and transactions.</p>
          </div>
          <div className="facts-row">
            <Metric label="Visible rows" value={String(visibleRows.length)} />
            <Metric label="Namespaces" value={String(namespaceCounts.length)} />
          </div>
        </div>
        <div className="audit-namespace-grid">
          <button
            type="button"
            className={`audit-namespace-button ${activeNamespace === "all" ? "active" : ""}`}
            data-testid="audit-namespace-all"
            aria-pressed={activeNamespace === "all"}
            onClick={() => applyNamespace("all")}
          >
            <span>All rows</span>
            <strong>{rows.length}</strong>
          </button>
          {namespaceCounts.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`audit-namespace-button ${activeNamespace === item.key ? "active" : ""}`}
              data-testid={`audit-namespace-${auditTestIdPart(item.key)}`}
              aria-label={`Filter audit namespace ${item.label}`}
              aria-pressed={activeNamespace === item.key}
              onClick={() => applyNamespace(item.key)}
            >
              <span>{item.label}</span>
              <strong>{item.count}</strong>
            </button>
          ))}
        </div>
      </section>
      <SelectedAuditTrace
        row={selectedAuditRow}
        fallbackLabel="data audit source_trace"
        factQueryString={auditQueryString}
        onOpenSourceDocument={openSourceDocument}
      />
      <RawEvidenceDrawer
        state={sourceDocumentState}
        onClose={() => setSourceDocumentState(null)}
      />
      <MobileAuditDrawer
        row={selectedAuditRow}
        open={mobileInspectorOpen}
        factQueryString={auditQueryString}
        onClose={() => setMobileInspectorOpen(false)}
        onOpenSourceDocument={openSourceDocument}
      />
      <table className="terminal-table wide audit-grid">
        <thead>
          <tr>
            <th>Scope</th>
            <th>Fact</th>
            <th>Value</th>
            <th>FY</th>
            <th>Method</th>
            <th>Source doc</th>
            <th>Filing id</th>
            <th>Available at</th>
            <th>Period</th>
            <th>Unit</th>
            <th>Currency</th>
            <th>Formula</th>
            <th>Quality</th>
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((row) => {
            const isSourceFact = isFinancialFactAuditRow(row);
            const namespace = auditRowNamespace(row);
            const family = auditFactFamily(row);
            const isSelected = selectedAuditRow?.fact_id === row.fact_id;
            return (
              <tr
                key={row.fact_id}
                className={[
                  isSourceFact ? "audit-row-source" : "",
                  isSelected ? "audit-row-selected" : ""
                ].filter(Boolean).join(" ") || undefined}
              >
                <td>
                  <span className={`audit-scope ${isSourceFact ? "source" : ""}`}>
                    {isSourceFact ? "XBRL fact" : auditNamespaceLabel(namespace)}
                  </span>
                  <small className="audit-family-label">{family.label}</small>
                </td>
                <td title={row.fact_id}>
                  <button
                    type="button"
                    className="audit-cell-button"
                    data-testid={`data-audit-fact-${auditTestIdPart(row.fact_id)}`}
                    aria-label={`Inspect data audit fact ${row.fact_name ?? row.fact_id}`}
                    onClick={() => selectFactId(row.fact_id)}
                  >
                    {row.fact_name ?? row.fact_id}
                  </button>
                </td>
                <td>
                  <button
                    type="button"
                    className="audit-cell-button"
                    data-testid={`data-audit-value-${auditTestIdPart(row.fact_id)}`}
                    aria-label={`Inspect data audit value ${row.fact_name ?? row.fact_id}`}
                    onClick={() => selectFactId(row.fact_id)}
                  >
                    {row.value ?? "-"}
                  </button>
                </td>
                <td>{row.fiscal_year}</td>
                <td>{row.method}</td>
                <td>{renderSourceDocumentLink(row.source_trace.source_document_id, openSourceDocument)}</td>
                <td>{row.source_trace.filing_id ?? "-"}</td>
                <td>{String(row.source_trace.available_at ?? "-")}</td>
                <td>{row.source_trace.period ?? "-"}</td>
                <td>{row.source_trace.unit ?? "-"}</td>
                <td>{row.source_trace.currency ?? "-"}</td>
                <td>{row.source_trace.formula ?? row.formula ?? "-"}</td>
                <td>{row.source_trace.quality_status ?? row.quality_status}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function KrCacheDiagnostics({
  coverage,
  auditRows,
  factQueryString,
  onInspectFact,
  onOpenSourceDocument
}: {
  coverage?: KrValuationCacheCoverage | null;
  auditRows: AuditRow[];
  factQueryString: string;
  onInspectFact: (factId: string) => void;
  onOpenSourceDocument?: (sourceDocumentId: string) => void;
}) {
  if (!coverage) {
    return null;
  }
  const marketGaps = coverage.market_gap_diagnostics;
  const financialGaps = coverage.financial_gap_diagnostics;
  const hasGaps = marketGaps.length > 0 || financialGaps.length > 0;
  return (
    <section className="source-ledger" aria-label="KR valuation cache diagnostics" data-testid="data-audit-kr-diagnostics">
      <div className="panel-header compact">
        <div>
          <h2>KR Cache Diagnostics</h2>
          <p>Partial source-backed coverage is explained by source rows, market history start, and OpenDART annual filing availability.</p>
        </div>
        <div className="facts-row">
          <Metric label="Coverage" value={String(coverage.coverage_status ?? "unknown").replace(/_/g, " ")} />
          <Metric label="Market gaps" value={String(marketGaps.length)} />
          <Metric label="Financial gaps" value={String(financialGaps.length)} />
        </div>
      </div>
      {hasGaps ? (
        <div className="source-ledger-grid">
          <DiagnosticTable
            title="Market Input Gaps"
            rows={marketGaps.map((gap) => {
              const auditRow = findKrGapAuditRow(auditRows, "market", gap.fiscal_year, gap.status);
              return {
                key: `market-${gap.fiscal_year}-${gap.status}`,
                fiscalYear: gap.fiscal_year,
                status: gap.status,
                reason: gap.reason,
                nextAction: gap.next_action,
                source: gap.pykrx_source_document_id ||
                  gap.marcap_source_document_id ||
                  auditRow?.source_trace?.source_document_id ||
                  gap.first_available_market_date ||
                  "-",
                auditFactId: auditRow?.fact_id
              };
            })}
            testId="data-audit-kr-market-gaps"
            factQueryString={factQueryString}
            onInspectFact={onInspectFact}
            onOpenSourceDocument={onOpenSourceDocument}
            emptyText="No market input gaps."
          />
          <DiagnosticTable
            title="Financial Metric Gaps"
            rows={financialGaps.map((gap) => {
              const auditRow = findKrGapAuditRow(auditRows, "financial", gap.fiscal_year, gap.status);
              return {
                key: `financial-${gap.fiscal_year}-${gap.status}`,
                fiscalYear: gap.fiscal_year,
                status: gap.status,
                reason: gap.reason,
                nextAction: gap.next_action,
                source: gap.source_document_id ||
                  gap.filing_id ||
                  auditRow?.source_trace?.source_document_id ||
                  gap.opendart_status ||
                  "-",
                auditFactId: auditRow?.fact_id
              };
            })}
            testId="data-audit-kr-financial-gaps"
            factQueryString={factQueryString}
            onInspectFact={onInspectFact}
            onOpenSourceDocument={onOpenSourceDocument}
            emptyText="No financial metric gaps."
          />
        </div>
      ) : (
        <div className="empty-source-ledger" data-testid="data-audit-kr-diagnostics-empty">
          KR valuation cache has no market or financial gap diagnostics.
        </div>
      )}
    </section>
  );
}

function findKrGapAuditRow(
  rows: AuditRow[],
  scope: "market" | "financial",
  fiscalYear?: number,
  status?: string
) {
  const factName = `data_quality.kr_${scope}_gap.${status ?? "unknown_gap"}`;
  return rows.find((row) => row.fiscal_year === fiscalYear && row.fact_name === factName);
}

function DiagnosticTable({
  title,
  rows,
  testId,
  factQueryString,
  onInspectFact,
  onOpenSourceDocument,
  emptyText
}: {
  title: string;
  rows: Array<{
    key: string;
    fiscalYear?: number;
    status?: string;
    reason?: string;
    nextAction?: string;
    source?: string;
    auditFactId?: string;
  }>;
  testId: string;
  factQueryString: string;
  onInspectFact: (factId: string) => void;
  onOpenSourceDocument?: (sourceDocumentId: string) => void;
  emptyText: string;
}) {
  return (
    <div className="source-series-table" data-testid={testId}>
      <h3>{title}</h3>
      {rows.length ? (
        <table className="terminal-table wide">
          <thead>
            <tr>
              <th>FY</th>
              <th>Status</th>
              <th>Reason</th>
              <th>Next action</th>
              <th>Source</th>
              <th>Audit</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>{row.fiscalYear ?? "-"}</td>
                <td>{String(row.status ?? "-").replace(/_/g, " ")}</td>
                <td>{row.reason ?? "-"}</td>
                <td>{String(row.nextAction ?? "-").replace(/_/g, " ")}</td>
                <td title={row.source ?? "-"}>{renderSourceDocumentLink(row.source, onOpenSourceDocument)}</td>
                <td>
                  {row.auditFactId ? (
                    <span className="table-actions">
                      <button type="button" onClick={() => onInspectFact(row.auditFactId!)}>
                        Inspect
                      </button>
                      <a href={auditFactHref(row.auditFactId, factQueryString)} target="_blank" rel="noreferrer">
                        Open fact
                      </a>
                    </span>
                  ) : (
                    "-"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="empty-source-ledger">{emptyText}</div>
      )}
    </div>
  );
}

function MobileAuditDrawer({
  row,
  open,
  factQueryString,
  onClose,
  onOpenSourceDocument
}: {
  row?: AuditRow;
  open: boolean;
  factQueryString?: string;
  onClose: () => void;
  onOpenSourceDocument?: (sourceDocumentId: string) => void;
}) {
  if (!open || !row) {
    return null;
  }
  const trace = (row.source_trace ?? {}) as Record<string, unknown>;
  const storageContract = auditStorageContract(trace, row);
  const factHref = auditFactHref(row.fact_id, factQueryString);
  const formula = String(trace.formula ?? row.formula ?? "-");
  const quality = String(trace.quality_status ?? row.quality_status ?? "-");
  const traceSummary = publicTraceSummary(trace);
  const sourceDocumentId = traceString(trace.source_document_id);
  const sourceHref = sourceDocumentId ? sourceDocumentHref(sourceDocumentId) : null;
  return (
    <aside className="mobile-audit-drawer" data-testid="mobile-audit-drawer" aria-label="Mobile source trace inspector">
      <div className="mobile-audit-drawer-header">
        <div>
          <span>Source Trace Inspector</span>
          <strong>{row.fact_name ?? row.fact_id}</strong>
        </div>
        <button type="button" data-testid="mobile-audit-drawer-close" onClick={onClose}>
          Close
        </button>
      </div>
      <div
        className={`mobile-audit-status ${storageContract.complete ? "allowed" : "rejected"}`}
        data-testid="mobile-audit-status"
      >
        {storageContract.complete ? "display allowed" : `source_trace incomplete: ${storageContract.missingLabels.join(", ")}`}
      </div>
      <dl className="mobile-audit-drawer-grid">
        <TraceField label="Value" value={row.value ?? "-"} />
        <TraceField label="Method" value={row.method ?? trace.method ?? "-"} />
        <TraceField label="Confidence" value={row.confidence ?? "-"} />
        <TraceField label="Source doc" value={trace.source_document_id ?? "-"} />
        <TraceField label="Filing" value={trace.filing_id ?? trace.accession_number ?? "-"} />
        <TraceField label="Available at" value={trace.available_at ?? "-"} />
        <TraceField label="Period" value={trace.period ?? "-"} />
        <TraceField label="Unit" value={trace.unit ?? "-"} />
        <TraceField label="Currency" value={trace.currency ?? "-"} />
        <TraceField label="Formula" value={formula} />
        <TraceField label="Quality" value={quality} />
        <TraceField label="Flags" value={row.flags?.length ? row.flags.join(", ") : "-"} />
      </dl>
      <section className="mobile-audit-formula-card">
        <h3>Formula lineage</h3>
        <p>normalized_facts -&gt; derived_metrics -&gt; valuation_series -&gt; UI cell</p>
        <p>Every derived row carries formula + input_fact_ids.</p>
      </section>
      <section className="mobile-audit-formula-card">
        <h3>Adjusted EPS bridge</h3>
        <p>GAAP metric + add-backs / removals - tax effects = adjusted operating metric.</p>
        <p>Policy toggle: street/core.</p>
      </section>
      <a className="mobile-audit-open-fact" href={factHref} target="_blank" rel="noreferrer">
        Open fact
      </a>
      {sourceHref ? (
        <a className="mobile-audit-open-fact" href={sourceHref} target="_blank" rel="noreferrer">
          Open source doc
        </a>
      ) : null}
      {sourceDocumentId && onOpenSourceDocument ? (
        <button
          type="button"
          className="mobile-audit-open-fact as-button"
          onClick={() => onOpenSourceDocument(sourceDocumentId)}
        >
          Inspect source doc
        </button>
      ) : null}
      <code className="mobile-audit-raw-preview">{JSON.stringify(traceSummary, null, 2)}</code>
    </aside>
  );
}

function TraceField({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd title={String(value ?? "-")}>{String(value ?? "-")}</dd>
    </div>
  );
}

type SourceDocumentResolution = {
  source_document_id: string;
  status: string;
  source: string | null;
  content_hash: string | null;
  local_path: string | null;
  source_url: string | null;
  filing_url: string | null;
  content_type: string | null;
  preview_available: boolean;
  preview_text: string | null;
  resolver: string;
  metadata: Record<string, unknown>;
};

type SourceDocumentState = {
  sourceDocumentId: string;
  status: "loading" | "loaded" | "error";
  data?: SourceDocumentResolution;
  error?: string;
};

function RawEvidenceDrawer({
  state,
  onClose
}: {
  state: SourceDocumentState | null;
  onClose: () => void;
}) {
  if (!state) {
    return null;
  }
  const data = state.data;
  const metadata = data?.metadata ?? {};
  return (
    <aside className="raw-evidence-drawer" data-testid="raw-evidence-drawer" aria-label="Raw source evidence drawer">
      <div className="raw-evidence-header">
        <div>
          <span>Raw Evidence</span>
          <strong data-testid="raw-evidence-source-id">{state.sourceDocumentId}</strong>
        </div>
        <button type="button" onClick={onClose} data-testid="raw-evidence-close">
          Close
        </button>
      </div>
      <div className={`raw-evidence-status ${data?.status ?? state.status}`} data-testid="raw-evidence-status">
        {state.status === "loading" ? "loading source evidence" : state.status === "error" ? "resolver error" : data?.status}
      </div>
      {state.status === "error" ? (
        <p className="raw-evidence-error">{state.error}</p>
      ) : null}
      {data ? (
        <>
          <dl className="raw-evidence-grid">
            <TraceField label="Status" value={data.status} />
            <TraceField label="Source" value={data.source ?? "-"} />
            <TraceField label="Resolver" value={data.resolver} />
            <TraceField label="Content type" value={data.content_type ?? "-"} />
            <TraceField label="Content hash" value={data.content_hash ?? "-"} />
            <TraceField label="Local path" value={data.local_path ?? "-"} />
          </dl>
          <div className="raw-evidence-actions">
            {data.source_url ? (
              <a href={data.source_url} target="_blank" rel="noreferrer">
                Open source URL
              </a>
            ) : null}
            {data.filing_url ? (
              <a href={data.filing_url} target="_blank" rel="noreferrer">
                Open filing URL
              </a>
            ) : null}
          </div>
          {data.preview_available && data.preview_text ? (
            <section className="raw-evidence-preview">
              <h3>Preview</h3>
              <code data-testid="raw-evidence-preview">{data.preview_text}</code>
            </section>
          ) : (
            <section className="raw-evidence-preview muted" data-testid="raw-evidence-preview-unavailable">
              <h3>Preview</h3>
              <p>{rawEvidencePreviewMessage(data)}</p>
            </section>
          )}
          <section className="raw-evidence-preview">
            <h3>Metadata</h3>
            <code>{JSON.stringify(metadata, null, 2)}</code>
          </section>
        </>
      ) : null}
    </aside>
  );
}

function normalizeSourceDocumentResolution(value: unknown, sourceDocumentId: string): SourceDocumentResolution {
  const record = isRecord(value) ? value : {};
  return {
    source_document_id: String(record.source_document_id ?? sourceDocumentId),
    status: String(record.status ?? "missing"),
    source: optionalString(record.source),
    content_hash: optionalString(record.content_hash),
    local_path: optionalString(record.local_path),
    source_url: optionalString(record.source_url),
    filing_url: optionalString(record.filing_url),
    content_type: optionalString(record.content_type),
    preview_available: Boolean(record.preview_available),
    preview_text: optionalString(record.preview_text),
    resolver: String(record.resolver ?? "unknown"),
    metadata: isRecord(record.metadata) ? record.metadata : {}
  };
}

function rawEvidencePreviewMessage(data: SourceDocumentResolution) {
  if (data.status === "logical_only") {
    return "This audit id is deterministic provenance. It does not map to a standalone raw file.";
  }
  if (data.status === "missing") {
    return "No stored raw file was found for this source_document_id.";
  }
  return "Preview is unavailable for this content type or storage record.";
}

function optionalString(value: unknown) {
  if (value === undefined || value === null || value === "") {
    return null;
  }
  return String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function renderSourceDocumentLink(value: unknown, onOpenSourceDocument?: (sourceDocumentId: string) => void) {
  const sourceDocumentId = traceString(value);
  if (!sourceDocumentId) {
    return "-";
  }
  if (!looksLikeSourceDocumentId(sourceDocumentId)) {
    return sourceDocumentId;
  }
  if (onOpenSourceDocument) {
    return (
      <button
        type="button"
        className="link-button source-document-button"
        onClick={() => onOpenSourceDocument(sourceDocumentId)}
      >
        {sourceDocumentId}
      </button>
    );
  }
  return (
    <a href={sourceDocumentHref(sourceDocumentId)} target="_blank" rel="noreferrer">
      {sourceDocumentId}
    </a>
  );
}

function traceString(value: unknown) {
  if (typeof value !== "string") {
    return value === undefined || value === null ? "" : String(value);
  }
  const text = value.trim();
  return text === "-" ? "" : text;
}

function looksLikeSourceDocumentId(value: string) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value) || /^\d{3}$/.test(value)) {
    return false;
  }
  return (
    value.startsWith("raw:") ||
    value.startsWith("derived:") ||
    value.startsWith("kr-cache:") ||
    value.startsWith("opendart:") ||
    /^[0-9a-f]{32,}$/i.test(value) ||
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) ||
    value.includes("source") ||
    value.includes("filing") ||
    value.includes("doc")
  );
}

export function SelectedAuditTrace({
  row,
  fallbackTrace,
  fallbackLabel = "valuation source_trace",
  factQueryString,
  onOpenSourceDocument
}: {
  row?: AuditRow;
  fallbackTrace?: Record<string, unknown>;
  fallbackLabel?: string;
  factQueryString?: string;
  onOpenSourceDocument?: (sourceDocumentId: string) => void;
}) {
  const trace = row?.source_trace ?? fallbackTrace ?? {};
  const factHref = row?.fact_id ? auditFactHref(row.fact_id, factQueryString) : null;
  const traceSections = auditTraceSections(trace, row);
  const storageContract = auditStorageContract(trace, row);
  const inputTraceCount = auditWorkbenchInputTraceCount(trace);
  const flags = auditWorkbenchFlags(trace, row);
  const methodValue = row?.method ?? String(trace.method ?? trace.source_type ?? "-");
  const formulaValue = String(trace.formula ?? row?.formula ?? "-");
  const qualityValue = String(trace.quality_status ?? row?.quality_status ?? "-");
  const adjustedBridgeState = auditWorkbenchAdjustedBridgeState(row, trace);
  const tracePolicy = String((trace as Record<string, unknown>).policy ?? "source_trace policy");
  const sourceDocumentId = traceString(trace.source_document_id);
  const sourceHref = sourceDocumentId ? sourceDocumentHref(sourceDocumentId) : null;
  const inputLineageItems = auditInputLineageItems(trace);
  const selectedAuditSummary = [
    { key: "method", label: "Method", value: methodValue },
    { key: "source", label: "Source", value: traceString(trace.source) ?? traceString(trace.source_type) ?? "-" },
    { key: "period", label: "Period", value: traceString(trace.period) ?? "-" },
    {
      key: "confidence",
      label: "Confidence",
      value: row?.confidence ?? traceString((trace as Record<string, unknown>)["confidence"]) ?? "-"
    },
    { key: "quality", label: "Quality", value: qualityValue }
  ];
  return (
    <div className="selected-audit-trace" data-testid="selected-audit-trace">
      <div>
        <span>Selected Source Trace</span>
        <strong>{row?.fact_name ?? fallbackLabel}</strong>
        {factHref ? (
          <a href={factHref} target="_blank" rel="noreferrer">
            Open fact
          </a>
        ) : null}
        {sourceHref ? (
          <a href={sourceHref} target="_blank" rel="noreferrer">
            Open source doc
          </a>
        ) : null}
        {sourceDocumentId && onOpenSourceDocument ? (
          <button
            type="button"
            className="link-button"
            onClick={() => onOpenSourceDocument(sourceDocumentId)}
          >
            Inspect source doc
          </button>
        ) : null}
      </div>
      <div className="selected-audit-summary-strip" data-testid="selected-audit-summary-strip">
        {selectedAuditSummary.map((item) => (
          <article key={item.key} data-testid={`selected-audit-summary-${item.key}`}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </article>
        ))}
      </div>
      <section
        className={`selected-audit-storage-contract ${storageContract.complete ? "complete" : "incomplete"}`}
        data-testid="selected-audit-storage-contract"
      >
        <div>
          <span>Storage gate</span>
          <strong data-testid="selected-audit-storage-status">
            {storageContract.complete ? "display allowed" : "source_trace incomplete"}
          </strong>
        </div>
        <dl>
          {storageContract.fields.map((field) => (
            <div
              key={field.key}
              className={field.ready ? "ready" : "missing"}
              data-testid={`selected-audit-storage-field-${auditTestIdPart(field.key)}`}
            >
              <dt>{field.label}</dt>
              <dd>{field.ready ? "ready" : "missing"}</dd>
            </div>
          ))}
        </dl>
        <p data-testid="selected-audit-storage-missing">
          {storageContract.complete
            ? "no missing storage fields"
            : `missing ${storageContract.missingLabels.join(", ")}`}
        </p>
      </section>
      <section className="selected-audit-workbench" data-testid="data-audit-workbench">
        <div className="selected-audit-workbench-header">
          <span>Audit Workbench</span>
          <strong>Storage, formula, bridge, and quality are checked before display.</strong>
        </div>
        <div className="selected-audit-workbench-grid">
          <article data-testid="data-audit-workbench-storage">
            <span>Storage gate</span>
            <strong>{storageContract.complete ? "display allowed" : "source_trace incomplete"}</strong>
            <small>{storageContract.complete ? "All required source_trace fields are present." : `Missing ${storageContract.missingLabels.join(", ")}`}</small>
          </article>
          <article data-testid="data-audit-workbench-formula">
            <span>Formula lineage</span>
            <strong>{formulaValue === "-" ? "formula missing" : "formula present"}</strong>
            <small>{inputTraceCount ? `${inputTraceCount} input trace group${inputTraceCount === 1 ? "" : "s"}` : "Direct source fact or no input trace summary."}</small>
          </article>
          <article data-testid="data-audit-workbench-adjusted-bridge">
            <span>GAAP -&gt; Adjusted bridge</span>
            <strong>{adjustedBridgeState.label}</strong>
            <small>{adjustedBridgeState.detail}</small>
          </article>
          <article data-testid="data-audit-workbench-policy">
            <span>Method / policy</span>
            <strong>{methodValue}</strong>
            <small>{row?.policy ?? tracePolicy}</small>
          </article>
          <article data-testid="data-audit-workbench-quality">
            <span>Quality flags</span>
            <strong>{qualityValue}</strong>
            <small>{flags.length ? flags.join(", ") : "No quality flags for selected fact."}</small>
          </article>
        </div>
      </section>
      <section className="selected-audit-input-lineage" data-testid="data-audit-input-lineage">
        <div className="selected-audit-input-lineage-header">
          <span>Input lineage</span>
          <strong>
            {inputLineageItems.length
              ? `${inputLineageItems.length} upstream trace${inputLineageItems.length === 1 ? "" : "s"}`
              : "direct source fact"}
          </strong>
        </div>
        {inputLineageItems.length ? (
          <div className="selected-audit-input-lineage-grid">
            {inputLineageItems.slice(0, 6).map((item) => (
              <article key={item.key} data-testid={`data-audit-input-lineage-item-${auditTestIdPart(item.key)}`}>
                <span>{item.label}</span>
                <strong title={item.sourceDocumentId || item.source || item.sourceType || "-"}>
                  {item.sourceDocumentId
                    ? renderSourceDocumentLink(item.sourceDocumentId, onOpenSourceDocument)
                    : item.source || item.sourceType || "-"}
                </strong>
                <small>{[item.method, item.period, item.quality].filter(Boolean).join(" / ") || "source_trace"}</small>
                <code>{item.formula || item.factId || item.filingId || "input trace"}</code>
              </article>
            ))}
          </div>
        ) : (
          <p>No upstream input trace groups are attached. Treat this as a direct source fact or an incomplete lineage.</p>
        )}
        {inputLineageItems.length > 6 ? <p>+{inputLineageItems.length - 6} more upstream traces in raw source_trace JSON.</p> : null}
      </section>
      <dl>
        <div>
          <dt>Value</dt>
          <dd>{row?.value ?? "-"}</dd>
        </div>
        <div>
          <dt>Method</dt>
          <dd>{row?.method ?? String(trace.source_type ?? "-")}</dd>
        </div>
        <div>
          <dt>Source doc</dt>
          <dd>{renderSourceDocumentLink(sourceDocumentId, onOpenSourceDocument)}</dd>
        </div>
        <div>
          <dt>Filing</dt>
          <dd>{String(trace.filing_id ?? "-")}</dd>
        </div>
        <div>
          <dt>Available at</dt>
          <dd>{String(trace.available_at ?? "-")}</dd>
        </div>
        <div>
          <dt>Period</dt>
          <dd>{String(trace.period ?? "-")}</dd>
        </div>
        <div>
          <dt>Formula</dt>
          <dd>{String(trace.formula ?? row?.formula ?? "-")}</dd>
        </div>
        <div>
          <dt>Quality</dt>
          <dd>{String(trace.quality_status ?? row?.quality_status ?? "-")}</dd>
        </div>
      </dl>
      <div className="audit-trace-sections" data-testid="audit-trace-sections">
        {traceSections.map((section) => (
          <section
            className="audit-trace-section"
            data-testid={`audit-trace-section-${auditTestIdPart(section.title.toLowerCase())}`}
            key={section.title}
          >
            <h3>{section.title}</h3>
            <dl>
              {section.rows.map((item) => (
                <div key={item.label}>
                  <dt>{item.label}</dt>
                  <dd title={item.value}>{item.label === "Source document" ? renderSourceDocumentLink(item.value, onOpenSourceDocument) : item.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
      <code data-testid="selected-audit-raw-json">{JSON.stringify(publicTraceSummary(trace), null, 2)}</code>
    </div>
  );
}

function auditWorkbenchInputTraceCount(trace: Record<string, unknown>) {
  const keys = [
    "input_trace_summary",
    "calculation_inputs",
    "metric_input_traces",
    "forecast_metric_trace",
    "price_source_trace",
    "price_source_traces",
    "dividend_source_trace",
    "dividend_source_traces",
    "input_source_trace",
    "input_traces",
    "source_traces_by_year",
    "market_cap_source_trace",
    "market_cap_usd_source_trace"
  ];
  return keys.filter((key) => hasTraceValue(trace[key])).length;
}

function auditWorkbenchFlags(trace: Record<string, unknown>, row?: AuditRow) {
  const traceFlags = [trace.flags, trace.quality_flags, trace.warnings]
    .flatMap((value) => Array.isArray(value) ? value : hasTraceValue(value) ? [value] : [])
    .map(String);
  return Array.from(new Set([...(row?.flags ?? []), ...traceFlags]));
}

function auditWorkbenchAdjustedBridgeState(row: AuditRow | undefined, trace: Record<string, unknown>) {
  const factName = row?.fact_name ?? row?.fact_id ?? "";
  const method = `${row?.method ?? ""} ${trace.method ?? ""} ${trace.source_type ?? ""}`.toLowerCase();
  const policy = `${row?.policy ?? ""}`.toLowerCase();
  if (policy.includes("adjusted") || factName.includes("adjusted") || factName.includes("valuation.metric")) {
    return {
      label: "bridge visible",
      detail: "GAAP metric + add-backs / removals - tax effects = adjusted operating metric."
    };
  }
  if (method.includes("gaap_fallback") || method.includes("s4")) {
    return {
      label: "GAAP fallback",
      detail: "Displayed as fallback, not as reconstructed adjusted operating earnings."
    };
  }
  return {
    label: "not selected",
    detail: "Open an adjusted EPS or valuation metric fact to inspect the bridge."
  };
}

function auditStorageContract(trace: Record<string, unknown>, row?: AuditRow) {
  const fields = [
    {
      key: "source",
      label: "Source",
      ready: hasTraceValue(trace.source) || hasTraceValue(trace.source_type)
    },
    {
      key: "source_document_id",
      label: "Source doc",
      ready: hasTraceValue(trace.source_document_id)
    },
    {
      key: "filing",
      label: "Filing",
      ready: hasTraceValue(trace.filing_id) || hasTraceValue(trace.accession_number)
    },
    {
      key: "period",
      label: "Period",
      ready: hasTraceValue(trace.period)
    },
    {
      key: "unit",
      label: "Unit",
      ready: hasTraceValue(trace.unit)
    },
    {
      key: "currency",
      label: "Currency",
      ready: hasTraceValue(trace.currency)
    },
    {
      key: "method",
      label: "Method",
      ready: hasTraceValue(trace.method) || hasTraceValue(row?.method)
    },
    {
      key: "formula",
      label: "Formula",
      ready: hasTraceValue(trace.formula) || hasTraceValue(row?.formula)
    }
  ];
  const missingLabels = fields.filter((field) => !field.ready).map((field) => field.label);
  return {
    complete: missingLabels.length === 0,
    fields,
    missingLabels
  };
}

function hasTraceValue(value: unknown) {
  return value !== undefined && value !== null && value !== "";
}

type AuditInputLineageItem = {
  key: string;
  label: string;
  sourceDocumentId: string;
  source: string;
  sourceType: string;
  method: string;
  period: string;
  quality: string;
  formula: string;
  factId: string;
  filingId: string;
};

function auditInputLineageItems(trace: Record<string, unknown>) {
  const inputKeys = [
    "input_trace_summary",
    "calculation_inputs",
    "metric_input_traces",
    "forecast_metric_trace",
    "price_source_trace",
    "price_source_traces",
    "dividend_source_trace",
    "dividend_source_traces",
    "input_source_trace",
    "input_traces",
    "source_traces_by_year",
    "market_cap_source_trace",
    "market_cap_usd_source_trace"
  ];
  const seen = new Set<string>();
  const items: AuditInputLineageItem[] = [];
  for (const key of inputKeys) {
    collectAuditInputLineage(trace[key], key, items, seen, 0);
  }
  return items;
}

function collectAuditInputLineage(
  value: unknown,
  label: string,
  items: AuditInputLineageItem[],
  seen: Set<string>,
  depth: number
) {
  if (!hasTraceValue(value) || items.length >= 18 || depth > 3) {
    return;
  }
  if (Array.isArray(value)) {
    const before = items.length;
    value.forEach((item, index) => collectAuditInputLineage(item, `${label}.${index + 1}`, items, seen, depth + 1));
    if (items.length === before && depth <= 1) {
      addAuditInputLineageItem(rawAuditInputLineageItem(value, label), items, seen);
    }
    return;
  }
  if (!isRecord(value)) {
    return;
  }
  if (looksLikeTraceRecord(value)) {
    addAuditInputLineageItem(auditInputLineageItemFromRecord(value, label), items, seen);
    return;
  }
  const before = items.length;
  Object.entries(value).forEach(([nestedKey, nestedValue]) => {
    collectAuditInputLineage(nestedValue, `${label}.${nestedKey}`, items, seen, depth + 1);
  });
  if (items.length === before && depth <= 1) {
    addAuditInputLineageItem(rawAuditInputLineageItem(value, label), items, seen);
  }
}

function addAuditInputLineageItem(
  item: AuditInputLineageItem,
  items: AuditInputLineageItem[],
  seen: Set<string>
) {
  const dedupeKey = [
    item.sourceDocumentId,
    item.sourceType,
    item.method,
    item.period,
    item.formula,
    item.factId,
    item.filingId,
    item.label
  ].join("|");
  if (seen.has(dedupeKey)) {
    return;
  }
  seen.add(dedupeKey);
  items.push(item);
}

function looksLikeTraceRecord(record: Record<string, unknown>) {
  return [
    "source_document_id",
    "source_type",
    "source",
    "method",
    "formula",
    "fact_id",
    "filing_id",
    "period",
    "quality_status"
  ].some((key) => hasTraceValue(record[key]));
}

function auditInputLineageItemFromRecord(record: Record<string, unknown>, label: string): AuditInputLineageItem {
  return {
    key: `${label}:${traceString(record.source_document_id) || traceString(record.fact_id) || traceString(record.formula) || traceString(record.method) || "input"}`,
    label: formatInputLineageLabel(label),
    sourceDocumentId: traceString(record.source_document_id),
    source: traceString(record.source),
    sourceType: traceString(record.source_type),
    method: traceString(record.method),
    period: traceString(record.period),
    quality: traceString(record.quality_status),
    formula: traceString(record.formula),
    factId: traceString(record.fact_id),
    filingId: traceString(record.filing_id) || traceString(record.accession_number)
  };
}

function rawAuditInputLineageItem(value: unknown, label: string): AuditInputLineageItem {
  return {
    key: `raw:${label}:${summarizeRawInputTrace(value)}`,
    label: formatInputLineageLabel(label),
    sourceDocumentId: "",
    source: "raw input trace group",
    sourceType: "raw_input_trace_group",
    method: "",
    period: "",
    quality: "",
    formula: summarizeRawInputTrace(value),
    factId: "",
    filingId: ""
  };
}

function summarizeRawInputTrace(value: unknown) {
  if (Array.isArray(value)) {
    return `${value.length} input item${value.length === 1 ? "" : "s"}`;
  }
  if (isRecord(value)) {
    const keys = Object.keys(value).slice(0, 8);
    return keys.length ? `keys: ${keys.join(", ")}` : "empty input group";
  }
  return String(value);
}

function formatInputLineageLabel(label: string) {
  return label.replace(/[._]/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isFinancialFactAuditRow(row: AuditRow) {
  return row.policy === "financial_facts" || (row.fact_name ?? "").startsWith("financial_facts.");
}

function auditRowNamespace(row: AuditRow) {
  if (isFinancialFactAuditRow(row)) {
    return "financial_facts";
  }
  if (row.policy) {
    return row.policy;
  }
  const factName = row.fact_name ?? row.fact_id;
  return factName.includes(".") ? factName.split(".")[0] : "unscoped";
}

function auditNamespaceLabel(namespace: string) {
  const labels: Record<string, string> = {
    adjusted_earnings: "Adjusted EPS",
    analyst_scorecard: "Analyst Scorecard",
    financial_facts: "XBRL facts",
    forecast_assumption: "Forecast Inputs",
    forecast_case: "Case comparison",
    forecast_scenario: "Scenario lines",
    forecast_snapshot: "Consensus snapshots",
    fun_graphs: "FUN Graphs",
    health_check: "Health Check",
    kr_warehouse_normalized_fact: "KR Warehouse Facts",
    performance: "Performance",
    portfolio: "Portfolio",
    portfolio_transaction: "Transactions",
    price_points: "Price points",
    research_report_derived: "Research Report",
    terminal_snapshot: "Terminal Snapshot",
    use_of_cash: "Use of Cash",
    valuation_map: "Valuation Map"
  };
  return labels[namespace] ?? namespace.replace(/_/g, " ");
}

function auditNamespaceRank(namespace: string) {
  const order = [
    "financial_facts",
    "kr_warehouse_normalized_fact",
    "valuation_map",
    "adjusted_earnings",
    "price_points",
    "forecast_assumption",
    "forecast_snapshot",
    "forecast_case",
    "forecast_scenario",
    "portfolio",
    "portfolio_transaction",
    "performance",
    "research_report_derived",
    "fun_graphs",
    "health_check",
    "analyst_scorecard",
    "use_of_cash",
    "terminal_snapshot"
  ];
  const index = order.indexOf(namespace);
  return index === -1 ? order.length : index;
}

type AuditFactFamily = {
  key: string;
  label: string;
  detail: string;
};

function buildAuditFactFamilyCounts(rows: AuditRow[]) {
  const counts = new Map<string, AuditFactFamily & { count: number }>();
  rows.forEach((row) => {
    const family = auditFactFamily(row);
    const existing = counts.get(family.key);
    counts.set(family.key, {
      ...family,
      count: (existing?.count ?? 0) + 1
    });
  });
  return Array.from(counts.values()).sort((left, right) => {
    const leftRank = auditFactFamilyRank(left.key);
    const rightRank = auditFactFamilyRank(right.key);
    return leftRank === rightRank ? left.label.localeCompare(right.label) : leftRank - rightRank;
  });
}

function auditFactFamily(row: AuditRow): AuditFactFamily {
  const factName = row.fact_name ?? row.fact_id ?? "";
  const policy = row.policy ?? "";
  if (factName.startsWith("kr_warehouse.") && factName.includes("price")) {
    return {
      key: "warehouse_price",
      label: "Warehouse Price",
      detail: "DuckDB normalized price facts"
    };
  }
  if (factName.startsWith("kr_warehouse.")) {
    return {
      key: "warehouse_metric",
      label: "Warehouse EPS / Metric",
      detail: "DuckDB normalized EPS and metric facts"
    };
  }
  if (factName.startsWith("price_point.") || policy === "price_points") {
    return {
      key: "price_points",
      label: "Price Points",
      detail: "Source-traced close-price rows"
    };
  }
  if (
    factName.startsWith("valuation.") ||
    factName.startsWith("chart_key.") ||
    policy === "valuation_map" ||
    policy === "chart_key"
  ) {
    return {
      key: "valuation_derived",
      label: "Valuation Derived",
      detail: "Formula outputs from source facts"
    };
  }
  if (factName.startsWith("forecast") || policy.startsWith("forecast")) {
    return {
      key: "forecast",
      label: "Forecast",
      detail: "Forecast assumptions and scenario rows"
    };
  }
  if (isFinancialFactAuditRow(row)) {
    return {
      key: "xbrl_source",
      label: "XBRL Source",
      detail: "Primary filing facts"
    };
  }
  return {
    key: "other",
    label: "Other Evidence",
    detail: "Portfolio, score, report, or workflow facts"
  };
}

function auditFactFamilyLabel(key: string) {
  return {
    forecast: "Forecast",
    other: "Other Evidence",
    price_points: "Price Points",
    valuation_derived: "Valuation Derived",
    warehouse_metric: "Warehouse EPS / Metric",
    warehouse_price: "Warehouse Price",
    xbrl_source: "XBRL Source"
  }[key] ?? key.replace(/_/g, " ");
}

function auditFactFamilyRank(key: string) {
  const order = [
    "warehouse_metric",
    "warehouse_price",
    "valuation_derived",
    "price_points",
    "forecast",
    "xbrl_source",
    "other"
  ];
  const index = order.indexOf(key);
  return index === -1 ? order.length : index;
}

function SourceSeriesTable({
  title,
  rows,
  emptyText,
  onOpenSourceDocument
}: {
  title: string;
  rows: Array<{
    key: string;
    scope: string;
    label: string;
    date: string;
    value: string;
    unit: string;
    quality: string;
    sourceDocument: string;
    trace: Record<string, unknown>;
  }>;
  emptyText: string;
  onOpenSourceDocument?: (sourceDocumentId: string) => void;
}) {
  return (
    <div className="source-series-table">
      <h3>{title}</h3>
      {rows.length ? (
        <table className="terminal-table wide">
          <thead>
            <tr>
              <th>Scope</th>
              <th>Series</th>
              <th>Date</th>
              <th>Value</th>
              <th>Unit</th>
              <th>Quality</th>
              <th>Source doc</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>{row.scope}</td>
                <td title={row.label}>{row.label}</td>
                <td>{row.date}</td>
                <td>{row.value}</td>
                <td>{row.unit}</td>
                <td>{row.quality}</td>
                <td title={JSON.stringify(publicTraceSummary(row.trace))}>
                  {renderSourceDocumentLink(row.sourceDocument || traceString(row.trace.source_document_id), onOpenSourceDocument)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="empty-source-ledger">{emptyText}</div>
      )}
    </div>
  );
}
