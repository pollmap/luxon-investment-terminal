from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from packages.quality import validate_source_trace

AXIS_WEIGHTS: dict[str, Decimal] = {
    "profitability": Decimal("0.25"),
    "cash_generation": Decimal("0.20"),
    "financial_strength": Decimal("0.20"),
    "growth": Decimal("0.20"),
    "predictability": Decimal("0.15"),
}

AXIS_LABELS = {
    "profitability": "Profitability",
    "cash_generation": "Cash generation",
    "financial_strength": "Financial strength",
    "growth": "Growth",
    "predictability": "Predictability",
}

METRIC_RULES: dict[str, dict[str, Any]] = {
    "gross_margin_pct": {"axis": "profitability", "poor": "20", "excellent": "60"},
    "operating_margin_pct": {"axis": "profitability", "poor": "5", "excellent": "35"},
    "net_margin_pct": {"axis": "profitability", "poor": "5", "excellent": "25"},
    "roe_pct": {"axis": "profitability", "poor": "5", "excellent": "25"},
    "roic_pct": {"axis": "profitability", "poor": "5", "excellent": "20"},
    "fcf_margin_pct": {"axis": "cash_generation", "poor": "0", "excellent": "25"},
    "debt_to_equity": {
        "axis": "financial_strength",
        "poor": "3",
        "excellent": "0",
        "lower_is_better": True,
    },
    "current_ratio": {"axis": "financial_strength", "poor": "0.8", "excellent": "2.0"},
    "quick_ratio": {"axis": "financial_strength", "poor": "0.6", "excellent": "1.5"},
    "interest_coverage": {"axis": "financial_strength", "poor": "1.5", "excellent": "10"},
    "revenue_growth_pct": {"axis": "growth", "poor": "-5", "excellent": "20"},
    "eps_growth_pct": {"axis": "growth", "poor": "-10", "excellent": "25"},
}


def build_health_check_score(
    ticker: str,
    fiscal_fitness_rows: list[dict[str, Any]],
    *,
    currency: str,
    data_mode: str,
    forecast_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest_year = max((int(row["fiscal_year"]) for row in fiscal_fitness_rows), default=None)
    latest_rows = [
        row
        for row in fiscal_fitness_rows
        if latest_year is not None and int(row["fiscal_year"]) == latest_year
    ]
    axes = [
        _metric_axis(axis_key, latest_rows, data_mode, currency, ticker, latest_year)
        for axis_key in ("profitability", "cash_generation", "financial_strength", "growth")
    ]
    axes.append(_predictability_axis(ticker, forecast_evidence, data_mode, currency, latest_year))
    overall_score = _weighted_overall(axes)
    flags = sorted({flag for axis in axes for flag in axis["flags"]})
    quality_status = _quality_status(data_mode, flags)
    source_trace = {
        "source_document_id": f"{ticker.lower()}-{latest_year or 'latest'}-health-check",
        "source_type": "health_check_derived",
        "filing_id": f"{ticker}-{latest_year or 'latest'}-health-check",
        "period": f"FY{latest_year}" if latest_year is not None else "latest",
        "available_at": _first_available_at(
            [axis["source_trace"] for axis in axes if axis.get("source_trace")]
        ),
        "unit": "score_0_100",
        "currency": currency,
        "method": "health_check_derived",
        "formula": (
            "weighted average of profitability, cash generation, financial strength, "
            "growth, and predictability axis scores"
        ),
        "quality_status": quality_status,
        "input_traces": [
            axis["source_trace"] for axis in axes if axis.get("source_trace")
        ],
    }
    return {
        "ticker": ticker,
        "fiscal_year": latest_year,
        "overall_score": overall_score,
        "rating": _rating(overall_score),
        "quality_status": quality_status,
        "flags": flags,
        "axes": axes,
        "source_trace": source_trace,
    }


def _metric_axis(
    axis_key: str,
    rows: list[dict[str, Any]],
    data_mode: str,
    currency: str,
    ticker: str,
    fiscal_year: int | None,
) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    axis_flags: list[str] = []
    for row in rows:
        rule = METRIC_RULES.get(str(row.get("metric_key")))
        if not rule or rule["axis"] != axis_key:
            continue
        value = _decimal_or_none(row.get("value"))
        row_flags = list(row.get("flags") or [])
        if value is None:
            row_flags.append(f"missing_health_score_input:{row.get('metric_key')}")
            metric_score = None
        else:
            metric_score = _score_value(
                value,
                Decimal(rule["poor"]),
                Decimal(rule["excellent"]),
                bool(rule.get("lower_is_better")),
            )
        axis_flags.extend(row_flags)
        axis_flags.extend(validate_source_trace(row.get("source_trace")).flags)
        inputs.append(
            {
                "metric_key": row.get("metric_key"),
                "label": row.get("label"),
                "value": value,
                "score": metric_score,
                "quality_status": row.get("quality_status"),
                "flags": sorted(set(row_flags)),
                "source_trace": row.get("source_trace"),
            }
        )
    scored = [item["score"] for item in inputs if item["score"] is not None]
    if not scored:
        axis_flags.append(f"axis_not_scored:{axis_key}")
        score = Decimal("50.00")
    else:
        score = _average(scored)
    quality_status = _quality_status(data_mode, axis_flags)
    return {
        "axis_key": axis_key,
        "label": AXIS_LABELS[axis_key],
        "score": score,
        "weight": AXIS_WEIGHTS[axis_key],
        "quality_status": quality_status,
        "flags": sorted(set(axis_flags)),
        "inputs": inputs,
        "source_trace": _axis_trace(
            ticker,
            fiscal_year,
            axis_key,
            currency,
            quality_status,
            f"{axis_key} score = average(normalized metric scores)",
            inputs,
        ),
    }


def _predictability_axis(
    ticker: str,
    forecast_evidence: dict[str, Any] | None,
    data_mode: str,
    currency: str,
    fiscal_year: int | None,
) -> dict[str, Any]:
    flags: list[str] = []
    source_trace = (forecast_evidence or {}).get("source_trace") or {}
    hit_rate_1y = _decimal_or_none(
        ((forecast_evidence or {}).get("scorecard") or {}).get("summary", {}).get(
            "hit_rate_1y_pct"
        )
    )
    hit_rate_2y = _decimal_or_none(
        ((forecast_evidence or {}).get("scorecard") or {}).get("summary", {}).get(
            "hit_rate_2y_pct"
        )
    )
    sentiment_score = _sentiment_score((forecast_evidence or {}).get("sentiment"))
    quality = str(((forecast_evidence or {}).get("scorecard") or {}).get("status") or "")
    if not forecast_evidence:
        flags.append("predictability_requires_point_in_time_consensus_snapshots")
    if "fixture" in quality:
        flags.append("fixture_non_production_scorecard_proxy")
    if hit_rate_1y is None or hit_rate_2y is None:
        flags.append("missing_scorecard_hit_rate")
    if not source_trace:
        flags.append("missing_predictability_source_trace")
    else:
        flags.extend(validate_source_trace(source_trace).flags)

    scores = [score for score in (hit_rate_1y, hit_rate_2y, sentiment_score) if score is not None]
    score = _average(scores) if scores else Decimal("50.00")
    quality_status = _quality_status(data_mode, flags)
    inputs = [
        {
            "metric_key": "hit_rate_1y_pct",
            "label": "1Y estimate hit rate",
            "value": hit_rate_1y,
            "score": hit_rate_1y,
            "quality_status": quality or None,
            "flags": [],
            "source_trace": source_trace,
        },
        {
            "metric_key": "hit_rate_2y_pct",
            "label": "2Y estimate hit rate",
            "value": hit_rate_2y,
            "score": hit_rate_2y,
            "quality_status": quality or None,
            "flags": [],
            "source_trace": source_trace,
        },
        {
            "metric_key": "revision_sentiment_score",
            "label": "Revision sentiment",
            "value": sentiment_score,
            "score": sentiment_score,
            "quality_status": ((forecast_evidence or {}).get("sentiment") or {}).get(
                "quality_status"
            ),
            "flags": [],
            "source_trace": source_trace,
        },
    ]
    return {
        "axis_key": "predictability",
        "label": AXIS_LABELS["predictability"],
        "score": score,
        "weight": AXIS_WEIGHTS["predictability"],
        "quality_status": quality_status,
        "flags": sorted(set(flags)),
        "inputs": inputs,
        "source_trace": _axis_trace(
            ticker,
            fiscal_year,
            "predictability",
            currency,
            quality_status,
            "predictability score = average(1Y hit rate, 2Y hit rate, revision sentiment score)",
            inputs,
        ),
    }


def _sentiment_score(sentiment: dict[str, Any] | None) -> Decimal | None:
    if not sentiment:
        return None
    net_revision = _decimal_or_none(sentiment.get("net_revision_score_pct"))
    if net_revision is None:
        return None
    return _clamp(Decimal("50") + net_revision, Decimal("0"), Decimal("100")).quantize(
        Decimal("0.01")
    )


def _score_value(
    value: Decimal,
    poor: Decimal,
    excellent: Decimal,
    lower_is_better: bool,
) -> Decimal:
    if excellent == poor:
        return Decimal("50.00")
    if lower_is_better:
        raw = ((poor - value) / (poor - excellent)) * Decimal("100")
    else:
        raw = ((value - poor) / (excellent - poor)) * Decimal("100")
    return _clamp(raw, Decimal("0"), Decimal("100")).quantize(Decimal("0.01"))


def _weighted_overall(axes: list[dict[str, Any]]) -> Decimal:
    total = sum(
        Decimal(str(axis["score"])) * Decimal(str(axis["weight"]))
        for axis in axes
    )
    return total.quantize(Decimal("0.01"))


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("50.00")
    return (sum(values) / Decimal(len(values))).quantize(Decimal("0.01"))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(max(value, low), high)


def _rating(score: Decimal) -> str:
    if score >= Decimal("80"):
        return "strong"
    if score >= Decimal("65"):
        return "healthy"
    if score >= Decimal("50"):
        return "mixed"
    return "watch"


def _quality_status(data_mode: str, flags: list[str]) -> str:
    if data_mode != "source_backed":
        return "fixture_non_production_health_check"
    if any(flag.startswith(("missing_source_trace", "invalid_decimal")) for flag in flags):
        return "source_backed_warning"
    if flags:
        return "source_backed_partial"
    return "source_backed_derived"


def _axis_trace(
    ticker: str,
    fiscal_year: int | None,
    axis_key: str,
    currency: str,
    quality_status: str,
    formula: str,
    inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "source_document_id": f"{ticker.lower()}-{fiscal_year or 'latest'}-{axis_key}-health-score",
        "source_type": "health_check_axis_derived",
        "filing_id": f"{ticker}-{fiscal_year or 'latest'}-{axis_key}-health-score",
        "period": f"FY{fiscal_year}" if fiscal_year is not None else "latest",
        "available_at": _first_available_at(
            [item["source_trace"] for item in inputs if item.get("source_trace")]
        ),
        "unit": "score_0_100",
        "currency": currency,
        "method": "health_check_axis_derived",
        "formula": formula,
        "quality_status": quality_status,
        "input_traces": [
            item["source_trace"] for item in inputs if item.get("source_trace")
        ],
    }


def _first_available_at(source_traces: list[dict[str, Any]]) -> Any:
    for trace in source_traces:
        if trace.get("available_at"):
            return trace["available_at"]
        for nested in trace.get("input_traces") or []:
            if isinstance(nested, dict) and nested.get("available_at"):
                return nested["available_at"]
    return None
