"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { Download, FileJson, FileText, Table } from "lucide-react";
import { SelectedAuditTrace } from "./data-audit-panel";
import { Metric } from "./terminal-primitives";
import { API_TIMEOUT_MS } from "../lib/terminal-config";
import { auditTestIdPart, publicTraceSummary } from "../lib/audit-utils";
import type {
  AuditRow,
  ChartEvidenceSummary,
  LineVisibility,
  ResearchMetadata,
  ResearchReport,
  ResearchReportEvidence
} from "../lib/terminal-types";

type ResearchReportPanelProps = {
  report: ResearchReport | null;
  researchMetadata: ResearchMetadata;
  auditRows: AuditRow[];
  ticker: string;
  metric: string;
  forecastMode: string;
  forecastCase: string;
  forecastYears: number;
  rangeStartYear: string;
  rangeEndYear: string;
  normalMultipleYears: number;
  growth: number;
  targetMultiple: number;
  manualEps: string[];
  visibility: LineVisibility;
  hiddenScenarioLines: string[];
};

export function ResearchReportPanel({
  report,
  researchMetadata,
  auditRows,
  ticker,
  metric,
  forecastMode,
  forecastCase,
  forecastYears,
  rangeStartYear,
  rangeEndYear,
  normalMultipleYears,
  growth,
  targetMultiple,
  manualEps,
  visibility,
  hiddenScenarioLines
}: ResearchReportPanelProps) {
  const [chartRun, setChartRun] = useState<{
    status: string;
    chart_run_id?: string;
    svg_url?: string;
    png_url?: string;
    evidence_summary?: ChartEvidenceSummary;
  }>({ status: "ready" });
  const [selectedEvidenceKey, setSelectedEvidenceKey] = useState("valuation-Valuation gap");
  const encodedTicker = encodeURIComponent(ticker);
  const chartQuery = new URLSearchParams({
    metric,
    forecast_mode: forecastMode,
    forecast_case: forecastCase,
    forecast_years: String(forecastYears),
    normal_multiple_years: String(normalMultipleYears),
    user_growth_rate: String(growth),
    manual_eps_values: manualEps.slice(0, forecastYears).join(","),
    show_price: String(visibility.price),
    show_metric_area: String(visibility.metricArea),
    show_fair_value: String(visibility.fairValue),
    show_normal_multiple: String(visibility.normalMultiple),
    show_current_valuation: String(visibility.currentValuation),
    show_custom_valuation: String(visibility.customValuation),
    custom_valuation_multiple: String(targetMultiple),
    show_dividend_floor: String(visibility.dividendFloor),
    show_payout_ratio: String(visibility.payoutRatio),
    show_dividend_yield: String(visibility.dividendYield),
    show_recession_bands: String(visibility.recessionBands),
    show_forecast: String(visibility.forecast),
    show_scenario_lines: String(visibility.scenarioLines)
  });
  if (rangeStartYear.trim()) {
    chartQuery.set("start_year", rangeStartYear.trim());
  }
  if (rangeEndYear.trim()) {
    chartQuery.set("end_year", rangeEndYear.trim());
  }
  if (forecastMode === "custom") {
    chartQuery.set("target_multiple", String(targetMultiple));
  }
  if (hiddenScenarioLines.length) {
    chartQuery.set("hidden_scenario_lines", hiddenScenarioLines.join(","));
  }
  const chartQueryString = chartQuery.toString();
  const exports = [
    {
      label: "Markdown export",
      detail: "Research report",
      href: `/api/v1/companies/${encodedTicker}/exports/research-report.md?${chartQueryString}`,
      icon: <FileText size={16} />
    },
    {
      label: "JSON bundle",
      detail: "Report + audit rows",
      href: `/api/v1/companies/${encodedTicker}/exports/research-report.json?${chartQueryString}`,
      icon: <FileJson size={16} />
    },
    {
      label: "Data Audit CSV",
      detail: "Fact-level traces",
      href: `/api/v1/companies/${encodedTicker}/exports/data-audit.csv?${chartQueryString}`,
      icon: <Table size={16} />
    },
    {
      label: "Chart SVG",
      detail: "Current metric image",
      href: `/api/v1/charts/valuation-map/${encodedTicker}.svg?${chartQueryString}`,
      icon: <Download size={16} />
    },
    {
      label: "Chart PNG",
      detail: "Current metric image",
      href: `/api/v1/charts/valuation-map/${encodedTicker}.png?${chartQueryString}`,
      icon: <Download size={16} />
    }
  ];

  async function createChartRun() {
    setChartRun({ status: "saving" });
    try {
      const response = await withTimeout(
        fetch("/api/v1/charts/valuation-map/runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            company_id: ticker,
            metric,
            forecast_mode: forecastMode,
            forecast_case: forecastCase,
            forecast_years: forecastYears,
            start_year: rangeStartYear.trim() ? Number(rangeStartYear) : null,
            end_year: rangeEndYear.trim() ? Number(rangeEndYear) : null,
            normal_multiple_years: normalMultipleYears,
            user_growth_rate: String(growth),
            target_multiple: forecastMode === "custom" ? String(targetMultiple) : null,
            show_price: visibility.price,
            show_metric_area: visibility.metricArea,
            show_fair_value: visibility.fairValue,
            show_normal_multiple: visibility.normalMultiple,
            show_current_valuation: visibility.currentValuation,
            show_custom_valuation: visibility.customValuation,
            custom_valuation_multiple: String(targetMultiple),
            show_dividend_floor: visibility.dividendFloor,
            show_payout_ratio: visibility.payoutRatio,
            show_dividend_yield: visibility.dividendYield,
            show_recession_bands: visibility.recessionBands,
            show_forecast: visibility.forecast,
            show_scenario_lines: visibility.scenarioLines,
            hidden_scenario_lines: hiddenScenarioLines,
            manual_eps_values: manualEps.slice(0, forecastYears).join(",")
          })
        }),
        API_TIMEOUT_MS
      );
      if (!response.ok) {
        throw new Error("chart run failed");
      }
      const payload = await response.json();
      const data = payload.data ?? {};
      setChartRun({
        status: `saved ${String(data.chart_run_id ?? "").slice(0, 8)}`,
        chart_run_id: data.chart_run_id,
        svg_url: data.svg_url,
        png_url: data.png_url,
        evidence_summary: chartEvidenceSummaryFrom(data.evidence_summary)
      });
    } catch {
      setChartRun({ status: "save failed" });
    }
  }

  if (!report) {
    return (
      <section className="single-panel">
        <div className="panel-header">
          <div>
            <h1>Research Report</h1>
            <p>Report data is unavailable for this request. Existing valuation and audit data remain visible in their own tabs.</p>
          </div>
        </div>
        <div className="report-summary">
          <strong>Research report unavailable</strong>
          <ul>
            <li>No fixture report is substituted when the report API fails.</li>
          </ul>
        </div>
      </section>
    );
  }

  const flags = Array.from(new Set([
    ...(report.flags ?? []),
    ...report.sections.flatMap((section) => section.flags ?? [])
  ])).sort();
  const evidenceRows = report.sections.flatMap((section) =>
    section.evidence.map((item) => ({ ...item, section: section.title }))
  );
  const externalMetadataRows = researchMetadata.items ?? [];
  const selectedEvidence = evidenceRows.find((row) => researchEvidenceKey(row) === selectedEvidenceKey) ?? evidenceRows[0];
  const selectedResearchFactName = selectedEvidence ? researchReportFactName(selectedEvidence) : null;
  const selectedResearchAuditRow = selectedResearchFactName
    ? auditRows.find(
        (row) =>
          row.fact_name === selectedResearchFactName &&
          (!report.fiscal_year || row.fiscal_year === report.fiscal_year)
      ) ?? auditRows.find((row) => row.fact_name === selectedResearchFactName)
    : undefined;

  return (
    <section className="single-panel">
      <div className="panel-header">
        <div>
          <h1>Research Report</h1>
          <p>Deterministic source-audited report assembled from valuation, forecast, quality, and capital allocation facts.</p>
        </div>
        <div className="facts-row">
          <Metric label="FY" value={String(report.fiscal_year ?? "-")} />
          <Metric label="Quality" value={report.quality_status} />
          <Metric label="Mode" value={report.data_mode} />
          <Metric label="Sections" value={String(report.sections.length)} />
        </div>
      </div>
      <div className="export-grid" aria-label="Export Center">
        {exports.map((item) => (
          <a key={item.label} className="export-link" href={item.href} download>
            {item.icon}
            <span>
              <strong>{item.label}</strong>
              <em>{item.detail}</em>
            </span>
          </a>
        ))}
        <button className="export-link" type="button" onClick={createChartRun} disabled={chartRun.status === "saving"}>
          <Download size={16} />
          <span>
            <strong>Create chart run</strong>
            <em>{chartRun.status}</em>
          </span>
        </button>
        {chartRun.svg_url ? (
          <a className="export-link" href={chartRun.svg_url} download>
            <Download size={16} />
            <span>
              <strong>Replay SVG</strong>
              <em>{chartRun.chart_run_id}</em>
            </span>
          </a>
        ) : null}
        {chartRun.png_url ? (
          <a className="export-link" href={chartRun.png_url} download>
            <Download size={16} />
            <span>
              <strong>Replay PNG</strong>
              <em>{chartRun.chart_run_id}</em>
            </span>
          </a>
        ) : null}
      </div>
      {chartRun.evidence_summary ? (
        <div className="chart-run-evidence" aria-label="Chart run evidence summary">
          <div>
            <span>Methods</span>
            <strong>{formatSummaryList(chartRun.evidence_summary.methods)}</strong>
          </div>
          <div>
            <span>Sources</span>
            <strong>{formatSummaryList(chartRun.evidence_summary.sources)}</strong>
          </div>
          <div>
            <span>Documents</span>
            <strong>{formatEvidenceNumber(chartRun.evidence_summary.source_document_count)}</strong>
          </div>
          <div>
            <span>Filings</span>
            <strong>{formatEvidenceNumber(chartRun.evidence_summary.filing_count)}</strong>
          </div>
          <div>
            <span>Periods</span>
            <strong>
              {formatEvidenceNumber(chartRun.evidence_summary.actual_periods)} actual /{" "}
              {formatEvidenceNumber(chartRun.evidence_summary.forecast_periods)} forecast
            </strong>
          </div>
          <div>
            <span>Quality</span>
            <strong>{formatSummaryList(chartRun.evidence_summary.quality_statuses)}</strong>
          </div>
          <div>
            <span>Available at</span>
            <strong>{chartRun.evidence_summary.latest_available_at ?? "-"}</strong>
          </div>
        </div>
      ) : null}
      <div className="report-summary">
        <strong>{report.title}</strong>
        <ul>
          {report.executive_summary.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
      <table className="terminal-table wide" aria-label="Research Report sections">
        <thead>
          <tr>
            <th>Section</th>
            <th>Verdict</th>
            <th>Primary bullets</th>
            <th>Quality</th>
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {report.sections.map((section) => (
            <tr key={section.section_key}>
              <td>{section.title}</td>
              <td>{section.verdict}</td>
              <td>{section.bullets.join(" ")}</td>
              <td>{section.quality_status}</td>
              <td>{section.flags.length ? section.flags.join(", ") : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="terminal-table wide" aria-label="Research Report evidence">
        <thead>
          <tr>
            <th>Section</th>
            <th>Evidence</th>
            <th>Value</th>
            <th>Unit</th>
            <th>Source</th>
            <th>Quality</th>
          </tr>
        </thead>
        <tbody>
          {evidenceRows.map((row) => (
            <tr key={`${row.section}-${row.label}`}>
              <td>{row.section}</td>
              <td>{row.label}</td>
              <td>
                <ResearchReportAuditCellButton row={row} onSelect={setSelectedEvidenceKey}>
                  {formatAnyValue(row.value)}
                </ResearchReportAuditCellButton>
              </td>
              <td>{row.unit}</td>
              <td>{String(row.source_trace?.source_type ?? "-")}</td>
              <td>{String(row.source_trace?.quality_status ?? "-")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="report-summary">
        <strong>External research metadata</strong>
        <ul>
          <li>
            {externalMetadataRows.length
              ? `${externalMetadataRows.length} source-backed metadata rows loaded.`
              : "No source-backed external research metadata is loaded for this ticker."}
          </li>
          <li>Policy: {researchMetadata.policy}. External report numbers are not used in valuation calculations.</li>
        </ul>
      </div>
      <table className="terminal-table wide" aria-label="External research metadata">
        <thead>
          <tr>
            <th>Source</th>
            <th>Title</th>
            <th>Identifier</th>
            <th>Items</th>
            <th>Quality</th>
            <th>Trace</th>
          </tr>
        </thead>
        <tbody>
          {externalMetadataRows.length ? (
            externalMetadataRows.map((row, index) => (
              <tr key={`${row.source}-${row.identifier}-${index}`}>
                <td>{row.source_label || row.source}</td>
                <td>
                  {row.link ? (
                    <a href={row.link} target="_blank" rel="noreferrer">
                      {row.title}
                    </a>
                  ) : (
                    row.title
                  )}
                </td>
                <td>{row.identifier}</td>
                <td>{row.item_count}</td>
                <td>{String(row.source_trace?.quality_status ?? researchMetadata.quality_status)}</td>
                <td>{String(row.source_trace?.method ?? "metadata_only_no_financial_numbers")}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={6}>Source-backed metadata not loaded. Run the research metadata ingestion job for this ticker.</td>
            </tr>
          )}
        </tbody>
      </table>
      <SelectedAuditTrace
        row={selectedResearchAuditRow}
        fallbackTrace={selectedEvidence?.source_trace ?? report.source_trace}
        fallbackLabel={selectedResearchFactName ?? `research report evidence: ${selectedEvidence?.label ?? "source_trace"}`}
      />
      <div className="source-box">
        <strong>Research Report trace</strong>
        <code>{JSON.stringify(publicTraceSummary(report.source_trace), null, 2)}</code>
      </div>
      <div className="source-box">
        <strong>External research metadata trace</strong>
        <code>{JSON.stringify(publicTraceSummary(researchMetadata.source_trace), null, 2)}</code>
      </div>
      <div className="source-box">
        <strong>Flags</strong>
        <code>{JSON.stringify(flags, null, 2)}</code>
      </div>
    </section>
  );
}

function researchEvidenceKey(row: ResearchReportEvidence & { section: string }) {
  return `${row.section}-${row.label}`;
}

function researchReportFactName(row: ResearchReportEvidence & { section: string }) {
  if (row.label === "Valuation gap") {
    return "research_report.valuation_gap_pct";
  }
  if (row.label === "Health score" || row.label === "Rating") {
    return "research_report.health_score";
  }
  if (row.label === "Total return CAGR") {
    return "research_report.forecast_total_return_cagr_pct";
  }
  if (row.section === "Data Quality") {
    return "research_report.section_count";
  }
  return null;
}

function ResearchReportAuditCellButton({
  row,
  onSelect,
  children
}: {
  row: ResearchReportEvidence & { section: string };
  onSelect: (key: string) => void;
  children: ReactNode;
}) {
  const key = researchEvidenceKey(row);
  return (
    <button
      className="audit-cell-button"
      type="button"
      data-testid={`research-report-audit-cell-${auditTestIdPart(key)}`}
      aria-label={`Audit research report ${row.section} ${row.label}`}
      onClick={() => onSelect(key)}
    >
      {children}
    </button>
  );
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("request timeout")), timeoutMs);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        window.clearTimeout(timer);
        reject(error);
      }
    );
  });
}

function chartEvidenceSummaryFrom(raw: unknown): ChartEvidenceSummary | undefined {
  return raw && typeof raw === "object" ? (raw as ChartEvidenceSummary) : undefined;
}

function formatSummaryList(values: string[] | undefined) {
  return values?.length ? values.join(", ") : "-";
}

function formatEvidenceNumber(value: number | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "-";
}

function formatAnyValue(raw: string | number | null | undefined) {
  if (raw === null || raw === undefined || raw === "") {
    return "-";
  }
  const value = Number(raw);
  return Number.isFinite(value) ? formatNumber(value) : String(raw);
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
