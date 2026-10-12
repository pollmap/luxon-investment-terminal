"""Pure company-total cash conversion view; never infer EPS or collect data."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .analysis import numeric, trailing_twelve_months
from .financial_dates import publication_date
from .taxonomy import full_ifrs_account

VERSION = "financial-questions-v2"
LABELS = {
    "revenue": "매출액",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
    "operating_cash": "영업현금흐름",
    "capex": "유형·무형자산 취득 지출",
    "free_cash": "잉여현금흐름",
}
CAPEX = {
    "PurchaseOfPropertyPlantAndEquipment": "ppe",
    "PurchasesOfPropertyPlantAndEquipment": "ppe",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "ppe",
    "PurchaseOfIntangibleAssets": "intangibles",
    "PurchasesOfIntangibleAssets": "intangibles",
    "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities": "intangibles",
}
REASONS = {
    "input_missing": "확인된 입력이 없어요",
    "period_unverified": "실제 회계기간 확인이 필요해요",
    "period_mismatch": "같은 기간의 입력이 아니에요",
    "basis_mismatch": "연결·별도 기준이 달라요",
    "currency_or_unit_mismatch": "통화·단위 확인이 필요해요",
    "dimension_unverified": "회사 전체 금액인지 확인이 필요해요",
    "company_identity_required": "같은 회사의 입력인지 확인이 필요해요",
    "source_evidence_required": "원문 근거 확인이 필요해요",
    "publication_unverified": "공개일 확인이 필요해요",
    "conflicting_inputs": "입력 값이 서로 달라요",
    "capex_sign_unverified": "취득 지출의 부호 확인이 필요해요",
    "non_positive_profit": "순이익이 0 이하라 비율을 계산하지 않아요",
    "non_positive_revenue": "매출액이 0 이하라 이익률을 계산하지 않아요",
    "entity_scope_unverified": "같은 회사 전체 금액인지 확인이 필요해요",
}


def _local(value):
    return str(value).rsplit("}", 1)[-1]


def _monetary(unit):
    return unit in ("원", "KRW") or (
        isinstance(unit, str) and unit.endswith("}KRW") and "/" not in unit and "*" not in unit
    )


def _dimensions(row, basis):
    dims = {_local(k): _local(v) for k, v in (row.get("dimensions") or {}).items()}
    axis = "ConsolidatedAndSeparateFinancialStatementsAxis"
    return not (set(dims) - {axis}) and (
        not dims
        or dims[axis]
        == {
            "CFS": "ConsolidatedMember",
            "OFS": "SeparateMember",
        }[basis]
    )


def _sources(row):
    trace = row.get("source_trace") or {}
    inputs = trace.get("inputs")
    return inputs if isinstance(inputs, list) and inputs else [trace]


def _public_source_url(trace):
    receipt = str(trace.get("filing_id") or "")
    if re.fullmatch(r"\d{14}", receipt):
        return "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt
    try:
        parsed = urlsplit(str(trace.get("source_url") or ""))
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"dart.fss.or.kr", "opendart.fss.or.kr", "kind.krx.co.kr"}
            or parsed.username
            or parsed.password
        ):
            return None
        if any(
            re.search(r"key|token|secret|password|signature|auth", key, re.I)
            for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
        ):
            return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    except (ValueError, TypeError):
        return None


def _evidence(row, company):
    """Retain real fact IDs or explicit source-observation locators, never DB IDs invented."""
    inputs, references, clocks, ids = _sources(row), [], [], []
    for trace in inputs:
        if not isinstance(trace, dict):
            return None, "source_evidence_required"
        digest = trace.get("sha256") or trace.get("source_document_id")
        if not trace.get("source") or not re.fullmatch(r"[0-9a-f]{64}", str(digest or "")):
            return None, "source_evidence_required"
        if trace.get("unit") and not _monetary(trace["unit"]):
            return None, "currency_or_unit_mismatch"
        if trace.get("currency") not in (None, "KRW"):
            return None, "currency_or_unit_mismatch"
        context_start, context_end = trace.get("input_period_start"), trace.get("input_period_end")
        if not context_start or not context_end:
            match = re.fullmatch(
                r"(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})", str(trace.get("period") or "")
            )
            if match:
                context_start, context_end = match.groups()
        if context_start or context_end:
            try:
                duration = (
                    date.fromisoformat(context_end) - date.fromisoformat(context_start)
                ).days + 1
            except (TypeError, ValueError):
                return None, "period_unverified"
            if not 70 <= duration <= 380:
                return None, "period_unverified"
        if trace.get("input_company_id") not in (None, company):
            return None, "company_identity_required"
        declared = trace.get("input_published_at")
        if declared and (not context_end or trace.get("input_company_id") != company):
            return None, "company_identity_required" if trace.get(
                "input_company_id"
            ) != company else "period_unverified"
        if not declared and "publication_time_unverified" in (trace.get("quality_flags") or []):
            return None, "publication_unverified"
        published, _ = (
            publication_date({"published_at": declared}) if declared else publication_date(trace)
        )
        if not published or published[:10] < (context_end or row["period_start"]):
            return None, "publication_unverified"
        clocks.append(published)
        explicit = trace.get("input_fact_ids") or (
            [trace["fact_id"]] if trace.get("fact_id") else []
        )
        if explicit:
            if not isinstance(explicit, (list, tuple)) or any(
                not isinstance(item, str) or not item for item in explicit
            ):
                return None, "source_evidence_required"
            ids.extend(str(item) for item in explicit)
            kind = "financial_fact"
        else:
            # A source hash + context/period + account denotes a real observation.
            # Its explicit prefix keeps it distinct from an operational DB row ID.
            identity = [
                company,
                row.get("account_id"),
                digest,
                trace.get("context_id"),
                trace.get("period"),
                trace.get("filing_id"),
            ]
            ids.append(
                "source-observation:"
                + hashlib.sha256(
                    json.dumps(identity, sort_keys=True, default=str).encode()
                ).hexdigest()
            )
            kind = "source_observation"
        reference = {
            k: trace[k]
            for k in (
                "source",
                "source_document_id",
                "sha256",
                "filing_id",
                "context_id",
                "period",
                "unit",
                "available_at",
                "input_period_start",
                "input_period_end",
                "input_published_at",
                "input_company_id",
                "period_kind",
                "calculation_fact_id",
                "calculation_version",
            )
            if k in trace
        } | {"input_kind": kind}
        source_url = _public_source_url(trace)
        if source_url:
            reference["source_url"] = source_url
        references.append(reference)
    if not clocks:
        return None, "publication_unverified"
    if max(clocks)[:10] < row["period_end"]:
        return None, "publication_unverified"
    return {
        "input_fact_ids": sorted(set(ids)),
        "available_at": max(clocks),
        "source_trace": {"source": "derived_financial_facts", "inputs": references},
    }, None


def _validate(row, basis, company):
    if row.get("basis") != basis:
        return "basis_mismatch"
    if not company or row.get("company_id") != company:
        return "company_identity_required"
    if row.get("currency") != "KRW" or not _monetary(row.get("unit")):
        return "currency_or_unit_mismatch"
    if not _dimensions(row, basis) or row.get("scope") not in (
        None,
        "",
        "company",
        "company_total",
    ):
        return "dimension_unverified"
    try:
        length = (
            date.fromisoformat(row["period_end"]) - date.fromisoformat(row["period_start"])
        ).days + 1
    except (KeyError, TypeError, ValueError):
        return "period_unverified"
    if row.get("frequency") != "ttm" or not 350 <= length <= 380:
        return "period_unverified"
    if numeric(row.get("exact_value", row.get("value"))) is None:
        return "input_missing"
    return None


def _empty(metric, reason="input_missing"):
    return {
        "metric": metric,
        "name": LABELS.get(metric, metric),
        "value": None,
        "exact_value": None,
        "unit": "원",
        "status": "unavailable",
        "reason": reason,
        "reason_label": REASONS[reason],
        "input_fact_ids": [],
        "available_at": None,
        "source_trace": None,
        "formula": None,
        "formula_version": VERSION,
    }


def _observed(metric, row, evidence):
    amount = numeric(row.get("exact_value", row.get("value")))
    formula = row.get("formula") or row.get("source_trace", {}).get("formula")
    expected = {
        "net_income": {"ProfitLoss"},
        "operating_cash": {"CashFlowsFromUsedInOperatingActivities"},
        "revenue": {"Revenue"},
        "operating_income": {"OperatingIncomeLoss"},
        "ppe": {name for name, role in CAPEX.items() if role == "ppe"},
        "intangibles": {name for name, role in CAPEX.items() if role == "intangibles"},
    }
    account = row.get("account_id", "")
    total_scope = (
        full_ifrs_account(account) and _local(account) in expected.get(metric, set())
    ) or (row.get("entity_scope") == "company_total" and row.get("entity_scope_verified") is True)
    result = {
        "metric": metric,
        "name": row.get("name") or LABELS.get(metric, metric),
        "value": float(amount),
        "exact_value": str(amount),
        "unit": "원",
        "status": "ready",
        "reason": None,
        "reason_label": None,
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "source_account_id": account,
        "entity_scope": "company_total" if total_scope else "unverified",
        **evidence,
        "formula": formula,
        "formula_version": VERSION,
    }
    if row.get("input_model_version"):
        result["input_model_version"] = row["input_model_version"]
        result["input_model_metric"] = row["input_model_metric"]
        result["input_model_formula"] = row.get("input_model_formula")
        result["input_model_fact_id"] = row.get("input_model_fact_id")
    return result


def _derived(metric, name, amount, inputs, formula, unit="원"):
    ids = sorted({identity for item in inputs for identity in item["input_fact_ids"]})
    return {
        "metric": metric,
        "name": name,
        "value": float(amount),
        "exact_value": str(amount),
        "unit": unit,
        "status": "ready",
        "reason": None,
        "reason_label": None,
        "period_start": inputs[0]["period_start"],
        "period_end": inputs[0]["period_end"],
        "entity_scope": "company_total"
        if all(item.get("entity_scope") == "company_total" for item in inputs)
        else "unverified",
        "input_fact_ids": ids,
        "available_at": max(item["available_at"] for item in inputs),
        "source_trace": {
            "source": "derived_financial_facts",
            "formula": formula,
            "input_fact_ids": ids,
            "inputs": [item["source_trace"] for item in inputs],
        },
        "formula": formula,
        "formula_version": VERSION,
    }


def _capex_rows(dated_facts, basis, company):
    """Reuse the established quarter calculator, with no parsing or store access."""
    inputs = []
    for fact in dated_facts:
        name = _local(fact.get("account_id"))
        if name not in CAPEX or not full_ifrs_account(fact.get("account_id", "")):
            continue
        if fact.get("company_id", fact.get("entity_id")) != company:
            continue
        if (
            fact.get("basis") != basis
            or fact.get("currency") != "KRW"
            or not _monetary(fact.get("unit"))
            or not _dimensions(fact, basis)
        ):
            continue
        evidence = dict(fact.get("source_trace") or {})
        if fact.get("fact_id"):
            evidence["input_fact_ids"] = [fact["fact_id"]]
        published, _ = publication_date(fact)
        if not published or published[:10] < str(fact.get("period_end") or "9999"):
            continue
        evidence["available_at"] = published
        evidence["input_period_start"] = fact["period_start"]
        evidence["input_period_end"] = fact["period_end"]
        evidence["input_published_at"] = published
        evidence["input_company_id"] = company
        inputs.append({**fact, "company_id": company, "source_trace": evidence})
    return [
        dict(row, metric=CAPEX[_local(row["account_id"])])
        for row in trailing_twelve_months(inputs)["rows"]
    ]


def _prepared_total_rows(model, basis, company):
    """Reuse retained amounts, never invert a reported per-share value.

    The existing v4 calculator already validates every monetary input, company,
    dimension and quarter before retaining its exact company-total numerator.
    EPS numerators represent ordinary equity attribution and are not accepted.
    """
    from .fundamental_metrics import VERSION as INPUT_VERSION

    metrics = {
        "OCF": ("operating_cash", "영업활동 현금흐름의 검증된 단독 분기 금액 합계"),
        "SALES": ("revenue", "매출액의 검증된 단독 분기 금액 합계"),
        "FCF": ("free_cash", "검증된 단독 분기 영업현금흐름 - 유형·무형자산 취득 지출의 금액 합계"),
    }
    results = []
    for row in model.get("rows") or []:
        metric = row.get("metric")
        if metric not in metrics or row.get("basis") != basis or row.get("frequency") != "ttm":
            continue
        ids = row.get("input_fact_ids")
        numerator = numeric(row.get("numerator"))
        published, _ = publication_date({"published_at": row.get("published_at")})
        if (
            row.get("company_id") != company
            or not company
            or row.get("formula_version") != INPUT_VERSION
            or row.get("period_verified") is not True
            or row.get("currency") != "KRW"
            or row.get("unit") != "원/주"
            or row.get("attribution") != "company_total_per_weighted_ordinary_share"
            or not isinstance(ids, list)
            or any(not isinstance(identity, str) or not identity for identity in ids)
            or len(set(ids)) < 4
            or not _dimensions(row, basis)
            or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("fact_id") or ""))
            or numerator is None
            or not published
            or published[:10] < str(row.get("period_end") or "9999")
        ):
            continue
        name, formula = metrics[metric]
        evidence = dict(row.get("source_trace") or {})
        evidence.update(
            input_fact_ids=list(ids),
            input_company_id=company,
            input_period_start=row.get("period_start"),
            input_period_end=row.get("period_end"),
            input_published_at=published,
            formula=formula,
            period_kind="prepared_calculation_window",
            calculation_fact_id=row["fact_id"],
            calculation_version=INPUT_VERSION,
        )
        results.append(
            {
                "metric": name,
                "name": LABELS[name],
                "company_id": company,
                "basis": basis,
                "currency": "KRW",
                "unit": "원",
                "frequency": "ttm",
                "period_start": row.get("period_start"),
                "period_end": row.get("period_end"),
                "period_verified": True,
                "value": float(numerator),
                "exact_value": str(numerator),
                "account_id": "prepared-company-total:" + metric,
                "entity_scope": "company_total",
                "entity_scope_verified": True,
                "source_trace": evidence,
                "formula": formula,
                "input_model_version": INPUT_VERSION,
                "input_model_metric": metric,
                "input_model_formula": row.get("formula"),
                "input_model_fact_id": row["fact_id"],
            }
        )
    return results


def build_financial_questions(analysis_view, basis="CFS", *, company_id=None, dated_facts=()):
    """Consume a prepared analysis projection and optional narrow CAPEX facts.

    No Store construction, cache writes, provider calls, document parsing or EPS
    inference. Company totals do not require weighted shares or split units.
    """
    if basis not in ("CFS", "OFS"):
        raise ValueError("Invalid accounting basis")
    summary = (analysis_view.get("ttm") or {}).get(basis) or {}
    source_rows = list(summary.get("rows") or [])
    prepared = analysis_view.get("fundamental_metrics") or {}
    companies = {
        row.get("company_id")
        for row in [*source_rows, *(prepared.get("rows") or [])]
        if row.get("basis") == basis and row.get("company_id")
    }
    company = company_id or (next(iter(companies)) if len(companies) == 1 else None)
    source_rows += _prepared_total_rows(prepared, basis, company)
    candidates = source_rows + _capex_rows(dated_facts, basis, company)
    # Keep an explicit reporting period, even when some inputs fail validation.
    eligible = [row for row in source_rows if not _validate(row, basis, company)]
    end = max((row["period_end"] for row in eligible), default=None)
    priorities = {
        metric: index
        for index, metric in enumerate(
            ("net_income", "operating_cash", "revenue", "operating_income")
        )
    }
    anchor = min(
        (row for row in eligible if row["period_end"] == end),
        key=lambda row: priorities.get(row.get("metric"), 99),
        default=None,
    )
    start = anchor["period_start"] if anchor else None
    values = {metric: _empty(metric) for metric in (*LABELS, "ppe", "intangibles")}
    grouped = {}
    for row in candidates:
        metric = row.get("metric")
        if metric not in values:
            continue
        reason = _validate(row, basis, company)
        if not reason and (row["period_start"], row["period_end"]) != (start, end):
            reason = "period_mismatch"
        evidence, evidence_reason = (None, None) if reason else _evidence(row, company)
        reason = reason or evidence_reason
        if reason:
            if values[metric]["status"] != "ready":
                values[metric] = _empty(metric, reason)
            continue
        grouped.setdefault(metric, []).append((row, evidence))
    for metric, observations in grouped.items():
        # Prefer the most recent disclosed version; conflicts at that version
        # remain errors rather than arbitrary first/last rows.
        latest = max(item[1]["available_at"] for item in observations)
        observations = [item for item in observations if item[1]["available_at"] == latest]
        amounts = {
            numeric(item[0].get("exact_value", item[0].get("value"))) for item in observations
        }
        if len(amounts) != 1:
            values[metric] = _empty(metric, "conflicting_inputs")
        else:
            values[metric] = _observed(metric, *observations[0])
    ppe, intangible, ocf = (values[key] for key in ("ppe", "intangibles", "operating_cash"))
    values["capex"]["required_inputs"] = ["ppe", "intangibles"]
    values["capex"]["missing_inputs"] = [
        key for key in ("ppe", "intangibles") if values[key]["status"] != "ready"
    ]
    if ppe["status"] == intangible["status"] == "ready":
        if numeric(ppe["exact_value"]) < 0 or numeric(intangible["exact_value"]) < 0:
            values["capex"] = _empty("capex", "capex_sign_unverified")
        else:
            values["capex"] = _derived(
                "capex",
                LABELS["capex"],
                numeric(ppe["exact_value"]) + numeric(intangible["exact_value"]),
                [ppe, intangible],
                "유형자산 취득 현금지출 + 무형자산 취득 현금지출",
            )
    elif ppe["reason"] != "input_missing" or intangible["reason"] != "input_missing":
        missing = values["capex"]["missing_inputs"]
        reason = next(
            (values[key]["reason"] for key in missing if values[key]["reason"] != "input_missing"),
            "input_missing",
        )
        values["capex"] = {
            **_empty("capex", reason),
            "required_inputs": ["ppe", "intangibles"],
            "missing_inputs": missing,
        }
    if (
        values["capex"]["status"] == ocf["status"] == "ready"
        and ocf.get("entity_scope") == values["capex"].get("entity_scope") == "company_total"
    ):
        values["free_cash"] = _derived(
            "free_cash",
            LABELS["free_cash"],
            numeric(ocf["exact_value"]) - numeric(values["capex"]["exact_value"]),
            [ocf, values["capex"]],
            "영업현금흐름 - 유형·무형자산 취득 현금지출",
        )
    elif values["capex"]["status"] == ocf["status"] == "ready":
        values["free_cash"] = _empty("free_cash", "entity_scope_unverified")
    if values["free_cash"].get("input_model_metric") == "FCF":
        values["free_cash"]["required_inputs"] = ["verified_fcf_numerator"]
        values["free_cash"]["missing_inputs"] = []
    else:
        values["free_cash"]["required_inputs"] = ["operating_cash", "capex"]
        values["free_cash"]["missing_inputs"] = [
            key for key in ("operating_cash", "capex") if values[key]["status"] != "ready"
        ]
    comparisons = []
    for metric, label, numerator, denominator, invalid in (
        (
            "cash_conversion",
            "순이익 대비 영업현금흐름",
            "operating_cash",
            "net_income",
            "non_positive_profit",
        ),
        ("net_margin", "순이익률", "net_income", "revenue", "non_positive_revenue"),
        ("operating_margin", "영업이익률", "operating_income", "revenue", "non_positive_revenue"),
    ):
        left, right = values[numerator], values[denominator]
        result = _empty(metric, left["reason"] or right["reason"] or "input_missing")
        result.update(name=label, unit="%")
        if left["status"] == right["status"] == "ready":
            if (
                left.get("entity_scope") != "company_total"
                or right.get("entity_scope") != "company_total"
            ):
                result = {**_empty(metric, "entity_scope_unverified"), "name": label, "unit": "%"}
            elif numeric(right["exact_value"]) <= 0:
                result = {**_empty(metric, invalid), "name": label, "unit": "%"}
            else:
                result = _derived(
                    metric,
                    label,
                    numeric(left["exact_value"]) / numeric(right["exact_value"]) * Decimal(100),
                    [left, right],
                    f"{left['name']} / {right['name']} × 100",
                    "%",
                )
        result["required_inputs"] = [numerator, denominator]
        result["missing_inputs"] = [
            key for key in (numerator, denominator) if values[key]["status"] != "ready"
        ]
        comparisons.append(result)
    rows = [values[key] for key in LABELS]
    ready = [row for row in rows if row["status"] == "ready"]
    return {
        "basis": basis,
        "company_id": company,
        "scope": "company_financial_totals",
        "frequency": "ttm",
        "period_start": start,
        "period_end": end,
        "currency": "KRW",
        "unit": "원",
        "rows": rows,
        "comparisons": comparisons,
        "status": "ready"
        if all(values[key]["status"] == "ready" for key in ("net_income", "operating_cash"))
        else "partial"
        if ready
        else "not_available",
        "per_share_status": "not_calculated",
        "formula_version": VERSION,
        "availability": {
            row["metric"]: {"status": row["status"], "reason": row["reason"]}
            for row in [*rows, *comparisons]
        },
    }
