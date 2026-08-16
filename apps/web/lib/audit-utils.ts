import type { AuditRow } from "./terminal-types";

export function auditFactHref(factId: string, queryString?: string) {
  const suffix = queryString ? `?${queryString}` : "";
  return `/api/data-audit/${encodeURIComponent(factId)}${suffix}`;
}

export function sourceDocumentHref(sourceDocumentId: string) {
  return `/api/source-documents/resolve?source_document_id=${encodeURIComponent(sourceDocumentId)}`;
}

export function auditTestIdPart(value: string) {
  return value.replace(/[^a-zA-Z0-9_.-]+/g, "_");
}

export function auditTraceSections(trace: Record<string, unknown>, row?: AuditRow) {
  return [
    {
      title: "Source evidence",
      rows: traceRows(trace, [
        ["Source type", "source_type"],
        ["Source document", "source_document_id"],
        ["Source URL", "source_url"],
        ["Filing", "filing_id"],
        ["Accession", "accession_number"],
        ["Available at", "available_at"],
        ["Data backend", "data_backend"],
        ["Warehouse view", "warehouse_view"],
        ["Cache path", "cache_path"],
        ["Loaded at", "loaded_at"],
        ["Period", "period"],
        ["Unit", "unit"],
        ["Currency", "currency"]
      ])
    },
    {
      title: "Calculation",
      rows: traceRows(trace, [
        ["Value", undefined, row?.value],
        ["Method", "method", row?.method],
        ["Policy", undefined, row?.policy],
        ["Confidence", undefined, row?.confidence],
        ["Formula", "formula", row?.formula]
      ])
    },
    {
      title: "Quality",
      rows: traceRows(trace, [
        ["Quality status", "quality_status", row?.quality_status],
        ["Flags", "flags", row?.flags?.length ? row.flags : undefined],
        ["Warnings", "warnings"],
        ["Data mode", "data_mode"],
        ["Forecast case", "forecast_case"],
        ["Scenario label", "scenario_label"]
      ])
    },
    {
      title: "Input traces",
      rows: traceInputRows(trace)
    }
  ].filter((section) => section.rows.length > 0);
}

export function publicTraceSummary(trace: Record<string, unknown> | undefined) {
  if (!trace) {
    return {};
  }
  const allowed = [
    "source_type",
    "source_document_id",
    "filing_id",
    "accession_number",
    "available_at",
    "period",
    "unit",
    "currency",
    "formula",
    "method",
    "quality_status",
    "source_url",
    "data_backend",
    "warehouse_view",
    "cache_path",
    "loaded_at",
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
    "market_cap_usd_source_trace",
    "financial_numbers_allowed",
    "quality_flags",
    "flags",
    "warnings"
  ];
  return Object.fromEntries(
    allowed
      .filter((key) => trace[key] !== undefined && trace[key] !== null)
      .map((key) => [key, trace[key]])
  );
}

function traceRows(
  trace: Record<string, unknown>,
  rows: Array<[label: string, key?: string, fallback?: unknown]>
) {
  return rows
    .map(([label, key, fallback]) => ({
      label,
      value: traceDisplayValue((key ? trace[key] : undefined) ?? fallback)
    }))
    .filter((row) => row.value !== "-");
}

function traceInputRows(trace: Record<string, unknown>) {
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
  return inputKeys
    .filter((key) => trace[key] !== undefined && trace[key] !== null)
    .map((key) => ({
      label: key,
      value: traceDisplayValue(trace[key])
    }));
}

function traceDisplayValue(value: unknown): string {
  if (value === undefined || value === null || value === "") {
    return "-";
  }
  if (Array.isArray(value)) {
    return value.length ? `${value.length} item${value.length === 1 ? "" : "s"}` : "-";
  }
  if (typeof value === "object") {
    if (isTraceRecord(value)) {
      const sourceType = value.source_type ? String(value.source_type) : null;
      const sourceDocument = value.source_document_id ? String(value.source_document_id) : null;
      if (sourceType || sourceDocument) {
        return [sourceType, sourceDocument].filter(Boolean).join(" / ");
      }
      return `${Object.keys(value).length} fields`;
    }
    return String(value);
  }
  return String(value);
}

function isTraceRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
