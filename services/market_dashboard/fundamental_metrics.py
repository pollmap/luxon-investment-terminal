"""Source-traced per-share cash flow and earnings. Totals and EPS are not aliases."""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .analysis import numeric
from .analysis_metrics import METRICS
from .eps_calculation import days, local, quarters_at
from .financial_dates import publication_date
from .taxonomy import full_ifrs_account

VERSION = "fundamental-metrics-v4"
ACCOUNTS = {
    "WeightedAverageShares": "shares",
    "CashFlowsFromUsedInOperatingActivities": "ocf",
    "Revenue": "sales",
    "ProfitLoss": "profit",
    "ProfitLossBeforeTax": "pretax",
    "ProfitLossFromContinuingOperations": "continuing_profit",
    "ProfitLossFromDiscontinuedOperations": "discontinued_profit",
    "IncomeTaxExpenseContinuingOperations": "tax",
    "InterestExpense": "interest",
    "InterestExpenseFinanceExpense": "interest",
    "DepreciationDepletionAndAmortisationExpense": "da",
    "DepreciationAndAmortisationExpense": "da",
    "DepreciationExpense": "depreciation",
    "AmortisationExpense": "amortisation",
    "PurchaseOfPropertyPlantAndEquipment": "ppe",
    "PurchaseOfIntangibleAssets": "intangibles",
    "PurchasesOfPropertyPlantAndEquipment": "ppe",
    "PurchasesOfIntangibleAssets": "intangibles",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "ppe",
    "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities": "intangibles",
    "CashFlowsFromUsedInIncreaseDecreaseInCurrentBorrowings": "short_debt_net",
    "ProceedsFromNoncurrentBorrowings": "long_debt_proceeds",
    "ProceedsFromLongTermBorrowings": "long_debt_proceeds",
    "RepaymentsOfNoncurrentBorrowings": "debt_repaid",
    "InterestPaidClassifiedAsOperatingActivities": "interest_paid_operating",
}
ALLOWED_AXES = {"ConsolidatedAndSeparateFinancialStatementsAxis", "ClassesOfShareCapitalAxis"}
AMOUNT_AXIS = (
    "CarryingAmountAccumulatedDepreciationAmortisationAndImpairmentAndGrossCarryingAmountAxis"
)


def _identity(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _valid(fact):
    account = fact.get("account_id", "")
    role = ACCOUNTS.get(local(account))
    standard = full_ifrs_account(account) or (
        "dart.fss.or.kr/taxonomy/" in account
        and "/ifrs/dart}" in account
        and local(account) in ("InterestExpenseFinanceExpense", "ProceedsFromLongTermBorrowings")
    )
    from .reviewed_notes import verified_note

    standard = standard or verified_note(fact)
    if not role or not standard:
        return None
    dims = {local(k): local(v) for k, v in (fact.get("dimensions") or {}).items()}
    # DART's reported amount is the total in the note, not a business segment
    # nor accumulated depreciation. Other members of this axis stay excluded.
    if dims.get(AMOUNT_AXIS) == "ReportedAmountMember":
        dims.pop(AMOUNT_AXIS)
    if set(dims) - ALLOWED_AXES:
        return None
    scope = dims.get("ConsolidatedAndSeparateFinancialStatementsAxis")
    if scope and {"ConsolidatedMember": "CFS", "SeparateMember": "OFS"}.get(scope) != fact.get(
        "basis"
    ):
        return None
    share_class = dims.get("ClassesOfShareCapitalAxis")
    if share_class not in (None, "OrdinarySharesMember"):
        return None
    # Company-wide operating totals are not ordinary-share EPS numerators.
    if role != "shares" and share_class is not None:
        return None
    unit = fact.get("unit", "")
    if role == "shares":
        if unit != "{http://www.xbrl.org/2003/instance}shares":
            return None
    elif fact.get("currency") != "KRW" or not unit.endswith("}KRW"):
        return None
    published, _ = publication_date(fact)
    try:
        length = days(fact["period_start"], fact["period_end"])
    except (KeyError, ValueError, TypeError):
        return None
    if (
        not 70 <= length <= 380
        or not fact.get("period_verified")
        or fact.get("basis") not in ("CFS", "OFS")
        or not fact.get("filing_id")
        or not fact.get("fact_id")
        or not fact.get("source_trace")
        or not published
        or published[:10] < fact["period_end"]
        or numeric(fact.get("value")) is None
    ):
        return None
    return role, published


def calculate_fundamental_metrics(facts, cashflow_reviews=()):
    grouped = defaultdict(lambda: defaultdict(list))
    rejected = Counter()
    for fact in facts:
        valid = _valid(fact)
        if not valid:
            continue
        role, published = valid
        company = fact.get("company_id") or fact.get("entity_id")
        if not company:
            continue
        key = (company, fact["basis"], fact["period_start"], fact["period_end"], fact["filing_id"])
        grouped[key][role].append({**fact, "published_at": published})
    inputs = []
    for (company, basis, start, end, filing), group in grouped.items():
        values, sources = {}, {}
        for role, candidates in group.items():
            amounts = {numeric(f["value"]) for f in candidates}
            if len(amounts) != 1:
                rejected[role + "_conflict"] += 1
                continue
            values[role] = next(iter(amounts))
            sources[role] = candidates
        if values.get("shares", Decimal(0)) <= 0:
            rejected["weighted_shares_missing"] += 1
            continue
        # The IFRS tax concept covers continuing operations. Whole-company net
        # income can only be paired with it when discontinued profit is explicitly
        # zero. Missing discontinued profit does not mean zero.
        earnings_role = (
            "continuing_profit"
            if "continuing_profit" in values
            else "profit"
            if values.get("discontinued_profit") == 0
            else None
        )
        formulas = {"OCF": ("ocf",), "SALES": ("sales",), "FCF": ("ocf", "ppe", "intangibles")}
        policies = [
            r
            for r in cashflow_reviews
            if r.get("verified") is True
            and r.get("company_id") == company
            and r.get("filing_id") == filing
            and r.get("basis") == basis
            and [start, end] in r.get("periods", [])
            and r.get("all_interest_in_ocf") is True
            and r.get("lease_principal_in_debt_repaid") is True
            and r.get("complete_debt_cashflows") is True
            and r.get("source_trace", {}).get("sha256")
            and r.get("source_trace", {}).get("source_url")
        ]
        if len(policies) == 1:
            formulas["FCFE"] = (
                "ocf",
                "ppe",
                "intangibles",
                "short_debt_net",
                "long_debt_proceeds",
                "debt_repaid",
                "interest_paid_operating",
            )
        if "pretax" in values or earnings_role:
            formulas["EBIT"] = (
                ("pretax", "interest") if "pretax" in values else (earnings_role, "tax", "interest")
            )
            formulas["EBITDA"] = (
                *formulas["EBIT"],
                *(("da",) if "da" in values else ("depreciation", "amortisation")),
            )
        else:
            rejected["earnings_scope_unverified"] += 1
        for metric, roles in formulas.items():
            if any(role not in values for role in roles):
                rejected[metric + "_inputs_missing"] += 1
                continue
            if metric in ("FCF", "FCFE") and (values["ppe"] < 0 or values["intangibles"] < 0):
                rejected["capex_sign_unverified"] += 1
                continue
            total = (
                values["ocf"] - values["ppe"] - values["intangibles"]
                if metric == "FCF"
                else sum(values[r] for r in roles)
            )
            if metric == "FCFE":
                if any(
                    values[r] < 0
                    for r in ("long_debt_proceeds", "debt_repaid", "interest_paid_operating")
                ):
                    rejected["FCFE_cashflow_sign_unverified"] += 1
                    continue
                # Operating interest is already deducted in OCF. Lease principal
                # is already inside the reviewed repayment line; neither is subtracted twice.
                total = (
                    values["ocf"]
                    - values["ppe"]
                    - values["intangibles"]
                    + values["short_debt_net"]
                    + values["long_debt_proceeds"]
                    - values["debt_repaid"]
                )
            evidence_roles = (
                (*roles, "discontinued_profit")
                if metric in ("EBIT", "EBITDA") and "profit" in roles
                else roles
            )
            used = [f for r in (*evidence_roles, "shares") for f in sources[r]]
            fact_ids = sorted({f["fact_id"] for f in used})
            if metric == "FCFE":
                fact_ids.append("cashflow-policy:" + policies[0]["source_trace"]["sha256"])
            share_bases = {f.get("per_share_basis") for f in sources["shares"]}
            share_basis = next(iter(share_bases)) if len(share_bases) == 1 else None
            published = max(f["published_at"] for f in used)
            length = days(start, end)
            frequency = (
                "annual" if length >= 350 else "quarterly" if length <= 100 else "cumulative"
            )
            amount = total / values["shares"]
            formula = METRICS[metric]["formula"]
            if metric == "FCFE":
                formula = (
                    "(영업현금흐름 - 유형·무형자산 취득 + 단기차입금 순증감"
                    " + 장기차입 - 사채·장기차입 상환)"
                    " / 기간 가중평균 주식 수; 지급이자는 영업현금흐름, 리스 원금은 상환액에 포함"
                )
            if metric in ("EBIT", "EBITDA") and "pretax" in roles:
                formula = (
                    "(법인세차감전순이익 + 이자비용"
                    + (" + 감가상각·상각비" if metric == "EBITDA" else "")
                    + ") / 기간 가중평균 주식 수"
                )
            inputs.append(
                {
                    "company_id": company,
                    "metric": metric,
                    "basis": basis,
                    "period_start": start,
                    "period_end": end,
                    "period_verified": True,
                    "frequency": frequency,
                    "days": length,
                    "filing_id": filing,
                    "profit": str(total),
                    "numerator": str(total),
                    "weighted_shares": str(values["shares"]),
                    "value": float(amount),
                    "exact_value": str(amount),
                    "currency": "KRW",
                    "unit": "원/주",
                    "share_class": "ordinary",
                    "share_class_verified": True,
                    "attribution": "company_total_per_weighted_ordinary_share",
                    "published_at": published,
                    "per_share_basis": share_basis,
                    "formula": formula,
                    "formula_version": VERSION,
                    "input_fact_ids": fact_ids,
                    "fact_id": _identity([VERSION, metric, fact_ids]),
                    "source_trace": {
                        **used[0]["source_trace"],
                        "available_at": published,
                        "formula": formula,
                        "input_fact_ids": fact_ids,
                    },
                    "comparison_status": "requires_price_alignment"
                    if share_basis
                    else "share_basis_required",
                }
            )
            if metric == "FCFE":
                inputs[-1]["cashflow_policy"] = policies[0]
    rows = [r for r in inputs if r["frequency"] == "annual"]
    groups = defaultdict(list)
    for row in inputs:
        groups[(row["company_id"], row["basis"], row["metric"])].append(row)
    readiness = []
    for (company, basis, metric), group in groups.items():
        seen = set()
        share_basis_blocked = False
        for publication in sorted({r["published_at"] for r in group}):
            quarters = quarters_at(group, publication)
            for index in range(3, len(quarters)):
                window = quarters[index - 3 : index + 1]
                if not all(
                    date.fromisoformat(a["period_end"]) + timedelta(days=1)
                    == date.fromisoformat(b["period_start"])
                    for a, b in zip(window, window[1:], strict=False)
                ):
                    continue
                length = sum(r["days"] for r in window)
                if not 350 <= length <= 380:
                    continue
                bases = {r.get("per_share_basis") for r in window}
                # Unknown bases may be displayed separately as reported annual
                # facts; combining different share units cannot produce TTM.
                if len(bases) != 1 or None in bases:
                    rejected[group[0]["metric"] + "_ttm_share_basis_required"] += 1
                    share_basis_blocked = True
                    continue
                ids = sorted({i for r in window for i in r["input_fact_ids"]})
                identity = _identity([VERSION, group[0]["metric"], "ttm", ids])
                if identity in seen:
                    continue
                seen.add(identity)
                shares = sum(numeric(r["weighted_shares"]) * r["days"] for r in window) / length
                total = sum(numeric(r["profit"]) for r in window)
                rows.append(
                    {
                        **window[-1],
                        "period_start": window[0]["period_start"],
                        "days": length,
                        "frequency": "ttm",
                        "numerator": str(total),
                        "profit": str(total),
                        "weighted_shares": str(shares),
                        "value": float(total / shares),
                        "exact_value": str(total / shares),
                        "published_at": publication,
                        "fact_id": identity,
                        "input_fact_ids": ids,
                        "source_trace": {
                            **window[-1]["source_trace"],
                            "available_at": publication,
                            "input_fact_ids": ids,
                        },
                    }
                )
        # These are input coverage diagnostics, never additional comparison facts.
        # Retain partial periods even when no annual/TTM result can be returned.
        has_result = any(
            r["company_id"] == company and r["basis"] == basis and r["metric"] == metric
            for r in rows
        )
        readiness.append(
            {
                "company_id": company,
                "basis": basis,
                "metric": metric,
                "status": "annual_or_ttm_inputs"
                if has_result
                else "split_basis_required"
                if share_basis_blocked
                else "partial_period_inputs",
                "input_start": min(r["period_start"] for r in group),
                "input_end": max(r["period_end"] for r in group),
                "input_periods": len({(r["period_start"], r["period_end"]) for r in group}),
                "input_frequencies": sorted({r["frequency"] for r in group}),
            }
        )
    return {
        "rows": sorted(rows, key=lambda r: (r["metric"], r["period_end"], r["published_at"])),
        "rejected": dict(rejected),
        "formula_version": VERSION,
        "readiness": sorted(readiness, key=lambda r: (r["company_id"], r["basis"], r["metric"])),
    }


def security_fundamentals(model, code, corporation_id):
    if not corporation_id:
        return []
    return [
        {**r, "security_code": code}
        for r in model.get("rows", [])
        if r.get("company_id") == corporation_id and r.get("share_class") == "ordinary"
    ]
