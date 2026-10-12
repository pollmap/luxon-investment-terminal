"""Small, source-backed TTM read model; never aggregate business-segment totals."""

from .analysis import trailing_twelve_months
from .financial_dates import publication_date

ACCOUNTS = {
    "매출액": ("revenue", "매출액"),
    "수익(매출액)": ("revenue", "매출액"),
    "Revenue": ("revenue", "매출액"),
    "영업이익": ("operating_income", "영업이익"),
    "영업이익(손실)": ("operating_income", "영업이익"),
    "OperatingIncomeLoss": ("operating_income", "영업이익"),
    "당기순이익": ("net_income", "당기순이익"),
    "당기순이익(손실)": ("net_income", "당기순이익"),
    "ProfitLoss": ("net_income", "당기순이익"),
    "CashFlowsFromUsedInOperatingActivities": ("operating_cash", "영업현금흐름"),
    "CashFlowsFromUsedInInvestingActivities": ("investing_cash", "투자현금흐름"),
    "CashFlowsFromUsedInFinancingActivities": ("financing_cash", "재무현금흐름"),
}


def _monetary(unit):
    return unit in ("원", "KRW") or (
        isinstance(unit, str) and unit.endswith("}KRW") and "/" not in unit and "*" not in unit
    )


def _traced_input(fact):
    """Keep actual context/identity in a read projection, never alter raw facts."""
    evidence = dict(fact.get("source_trace") or {})
    for key in ("period_start", "period_end"):
        if fact.get(key):
            evidence["input_" + key] = fact[key]
    company = fact.get("company_id") or fact.get("entity_id")
    if company:
        evidence["input_company_id"] = company
    if fact.get("fact_id"):
        evidence["input_fact_ids"] = sorted(
            set([*(evidence.get("input_fact_ids") or []), fact["fact_id"]])
        )
    # A received-at clock must not take precedence over a disclosed filing date.
    declared = {
        key: fact[key] for key in ("published_at", "publication_date", "rcept_dt") if fact.get(key)
    }
    published, _ = publication_date(declared)
    if published:
        evidence["input_published_at"] = published
    return {**fact, "source_trace": evidence}


def _evidence_priority(row):
    inputs = row.get("source_trace", {}).get("inputs") or []
    company = row.get("company_id")
    company_valid = bool(
        company
        and inputs
        and all(evidence.get("input_company_id") == company for evidence in inputs)
    )
    publication_valid = bool(
        inputs
        and all(
            evidence.get("input_published_at")
            and evidence.get("input_period_end")
            and evidence["input_published_at"][:10] >= evidence["input_period_end"]
            for evidence in inputs
        )
    )
    monetary = row.get("currency") == "KRW" and _monetary(row.get("unit"))
    dims = {
        key.rsplit("}", 1)[-1]: value.rsplit("}", 1)[-1]
        for key, value in row.get("dimensions", {}).items()
    }
    axis = "ConsolidatedAndSeparateFinancialStatementsAxis"
    dimensions_valid = not dims or (
        set(dims) == {axis}
        and dims[axis] == {"CFS": "ConsolidatedMember", "OFS": "SeparateMember"}[row["basis"]]
    )
    return (
        row["period_end"],
        monetary and dimensions_valid,
        company_valid and publication_valid,
        company_valid,
        publication_valid,
        row["account_id"].startswith("IS:"),
    )


def ttm_summary(official, basis="CFS"):
    if basis not in ("CFS", "OFS"):
        raise ValueError("Invalid accounting basis")
    facts = official.get("period_facts", []) + official.get("xbrl_facts", [])
    facts = [
        f
        for f in facts
        if f.get("basis") == basis
        and f.get("currency") == "KRW"
        and all("ConsolidatedAndSeparate" in key for key in (f.get("dimensions") or {}))
    ]
    calculated = trailing_twelve_months([_traced_input(fact) for fact in facts])
    selected = {}
    for row in calculated["rows"]:
        name = row["account_id"].rsplit("}", 1)[-1].removeprefix("IS:")
        identity = ACCOUNTS.get(name)
        if not identity:
            continue
        key, label = identity
        candidate = {**row, "metric": key, "name": label, "unit": "원"}
        prior = selected.get(key)
        # A complete period with retained company/context/publication evidence
        # wins over an undated legacy summary. IS remains the fallback only when
        # neither source can demonstrate a stronger contract.
        if prior is None or _evidence_priority(row) > _evidence_priority(prior):
            selected[key] = candidate
    order = [
        "revenue",
        "operating_income",
        "net_income",
        "operating_cash",
        "investing_cash",
        "financing_cash",
    ]
    latest = max((row["period_end"] for row in selected.values()), default=None)
    current = [
        selected[key] for key in order if key in selected and selected[key]["period_end"] == latest
    ]
    return {
        "basis": basis,
        "status": "ready" if selected else "period_evidence_required",
        "rows": current,
        "period_end": latest,
        "older_periods": {
            key: row["period_end"] for key, row in selected.items() if row["period_end"] != latest
        },
        "per_share_status": "not_calculated",
        "formula": "sum(verified standalone quarters)",
    }
