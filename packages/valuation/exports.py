from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

EXPORT_VERSION = "research_export_v1"


def build_research_export_bundle(
    ticker: str,
    report: dict[str, Any],
    audit_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = {
        "export_version": EXPORT_VERSION,
        "ticker": ticker,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source": "deterministic_research_report_export",
        "format_contract": {
            "report": "source-audited research report payload",
            "data_audit": "all fact-level source traces used by the company view",
        },
        "quality_status": report.get("quality_status"),
        "flags": report.get("flags") or [],
        "source_trace": report.get("source_trace") or {},
    }
    return {
        "manifest": manifest,
        "report": report,
        "data_audit": audit_rows_with_trace_sections(audit_rows),
    }


def research_report_to_markdown(bundle: dict[str, Any]) -> str:
    report = bundle["report"]
    manifest = bundle["manifest"]
    audit_rows = bundle["data_audit"]
    lines = [
        f"# {report.get('title') or manifest['ticker'] + ' Research Report'}",
        "",
        "## Export Manifest",
        "",
        f"- Ticker: `{manifest['ticker']}`",
        f"- Export version: `{manifest['export_version']}`",
        f"- Generated at: `{manifest['generated_at']}`",
        f"- Quality: `{manifest.get('quality_status') or 'unknown'}`",
        f"- Flags: `{', '.join(manifest.get('flags') or []) or 'none'}`",
        "",
        "## Executive Summary",
        "",
    ]
    lines.extend(f"- {_markdown_escape(item)}" for item in report.get("executive_summary", []))
    lines.extend(["", "## Sections", ""])
    for section in report.get("sections", []):
        section_title = section.get("title") or section.get("section_key") or "Section"
        lines.extend(
            [
                f"### {_markdown_escape(section_title)}",
                "",
                f"- Verdict: `{section.get('verdict') or 'not_available'}`",
                f"- Quality: `{section.get('quality_status') or 'unknown'}`",
                f"- Flags: `{', '.join(section.get('flags') or []) or 'none'}`",
                "",
            ]
        )
        lines.extend(f"- {_markdown_escape(item)}" for item in section.get("bullets", []))
        lines.extend(
            [
                "",
                "| Evidence | Value | Unit | Source | Quality |",
                "|---|---:|---|---|---|",
            ]
        )
        for evidence in section.get("evidence", []):
            trace = evidence.get("source_trace") or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_escape(evidence.get("label")),
                        _markdown_escape(_display(evidence.get("value"))),
                        _markdown_escape(evidence.get("unit")),
                        _markdown_escape(trace.get("source_type")),
                        _markdown_escape(trace.get("quality_status")),
                    ]
                )
                + " |"
            )
        lines.append("")
    lines.extend(
        [
            "## Audit Facts",
            "",
            "| Fact | FY | Value | Method | Quality | Formula |",
            "|---|---:|---:|---|---|---|",
        ]
    )
    for row in audit_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_escape(row.get("fact_name")),
                    _markdown_escape(row.get("fiscal_year")),
                    _markdown_escape(row.get("value")),
                    _markdown_escape(row.get("method")),
                    _markdown_escape(row.get("quality_status")),
                    _markdown_escape(row.get("formula")),
                ]
            )
            + " |"
        )
    source_trace_json = json.dumps(
        _json_safe(report.get("source_trace") or {}),
        indent=2,
        sort_keys=True,
    )
    lines.extend(["", "## Source Trace", "", "```json", source_trace_json, "```", ""])
    return "\n".join(lines)


def research_bundle_to_json(bundle: dict[str, Any]) -> str:
    return json.dumps(_json_safe(bundle), indent=2, sort_keys=True) + "\n"


def audit_rows_to_csv(rows: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    fieldnames = [
        "fact_id",
        "fact_name",
        "fiscal_year",
        "value",
        "method",
        "policy",
        "confidence",
        "quality_status",
        "flags",
        "formula",
        "source_document_id",
        "filing_id",
        "period",
        "unit",
        "currency",
        "source_url",
        "source_type",
        "accession_number",
        "input_trace_keys",
        "calculation_inputs_json",
        "source_trace_json",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        trace = row.get("source_trace") or {}
        writer.writerow(
            {
                "fact_id": row.get("fact_id"),
                "fact_name": row.get("fact_name"),
                "fiscal_year": row.get("fiscal_year"),
                "value": row.get("value"),
                "method": row.get("method"),
                "policy": row.get("policy"),
                "confidence": row.get("confidence"),
                "quality_status": row.get("quality_status"),
                "flags": ",".join(row.get("flags") or []),
                "formula": row.get("formula"),
                "source_document_id": trace.get("source_document_id"),
                "filing_id": trace.get("filing_id"),
                "period": trace.get("period"),
                "unit": trace.get("unit"),
                "currency": trace.get("currency"),
                "source_url": trace.get("source_url") or trace.get("filing_url"),
                "source_type": trace.get("source_type"),
                "accession_number": trace.get("accession_number"),
                "input_trace_keys": ",".join(_input_trace_keys(trace)),
                "calculation_inputs_json": _json_cell(trace.get("calculation_inputs")),
                "source_trace_json": _json_cell(trace),
            }
        )
    return output.getvalue()


def audit_rows_with_trace_sections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [audit_row_with_trace_sections(row) for row in rows]


def audit_row_with_trace_sections(row: dict[str, Any]) -> dict[str, Any]:
    detail = dict(row)
    detail["trace_sections"] = audit_trace_sections(detail)
    return detail


def audit_trace_sections(row: dict[str, Any]) -> list[dict[str, Any]]:
    trace = _json_safe(dict(row.get("source_trace") or {}))
    sections = [
        {
            "title": "Source evidence",
            "rows": _audit_trace_rows(
                trace,
                [
                    ("Source type", "source_type", None),
                    ("Source document", "source_document_id", None),
                    ("Source URL", "source_url", None),
                    ("Filing", "filing_id", None),
                    ("Accession", "accession_number", None),
                    ("Period", "period", None),
                    ("Unit", "unit", None),
                    ("Currency", "currency", None),
                ],
            ),
        },
        {
            "title": "Calculation",
            "rows": _audit_trace_rows(
                trace,
                [
                    ("Fact", None, row.get("fact_name")),
                    ("Value", None, row.get("value")),
                    ("Method", "source_type", row.get("method")),
                    ("Policy", None, row.get("policy")),
                    ("Confidence", None, row.get("confidence")),
                    ("Formula", "formula", row.get("formula")),
                ],
            ),
        },
        {
            "title": "Quality",
            "rows": _audit_trace_rows(
                trace,
                [
                    ("Quality status", "quality_status", row.get("quality_status")),
                    ("Flags", "flags", row.get("flags")),
                    ("Warnings", "warnings", None),
                    ("Data mode", "data_mode", None),
                    ("Forecast case", "forecast_case", None),
                    ("Scenario label", "scenario_label", None),
                ],
            ),
        },
        {
            "title": "Input traces",
            "rows": _audit_trace_input_rows(trace),
        },
    ]
    return [section for section in sections if section["rows"]]


def _audit_trace_rows(
    trace: dict[str, Any],
    row_specs: list[tuple[str, str | None, object | None]],
) -> list[dict[str, str | None]]:
    rows = []
    for label, key, fallback in row_specs:
        raw_value = trace.get(key) if key else None
        if raw_value in (None, ""):
            raw_value = fallback
        value = _audit_trace_display_value(raw_value)
        if value != "-":
            rows.append({"label": label, "key": key, "value": value})
    return rows


def _audit_trace_input_rows(trace: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for key in _input_trace_keys(trace):
        rows.append(
            {
                "label": key,
                "key": key,
                "value": _audit_trace_display_value(trace.get(key)),
            }
        )
    return rows


def _audit_trace_display_value(value: Any) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, list):
        return f"{len(value)} item{'s' if len(value) != 1 else ''}" if value else "-"
    if isinstance(value, dict):
        source_type = value.get("source_type")
        source_document = value.get("source_document_id")
        if source_type or source_document:
            return " / ".join(str(item) for item in [source_type, source_document] if item)
        return f"{len(value)} fields" if value else "-"
    return str(value)


def _input_trace_keys(trace: dict[str, Any]) -> list[str]:
    keys = [
        "input_trace_summary",
        "calculation_inputs",
        "metric_input_traces",
        "forecast_snapshot_trace",
        "forecast_assumption_trace",
        "forecast_metric_trace",
        "start_price_trace",
        "price_source_trace",
        "price_source_traces",
        "dividend_source_trace",
        "dividend_source_traces",
        "input_source_trace",
        "input_traces",
        "source_traces_by_year",
        "market_cap_source_trace",
        "market_cap_usd_source_trace",
    ]
    return [key for key in keys if trace.get(key) not in (None, "", [], {})]


def _json_cell(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))


def export_filename(ticker: str, suffix: str) -> str:
    safe_ticker = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_" for char in ticker.upper()
    )
    return f"{safe_ticker.lower()}-{suffix}"


def _markdown_escape(value: Any) -> str:
    text = _display(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _display(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value
