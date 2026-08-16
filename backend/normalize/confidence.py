from __future__ import annotations

from decimal import Decimal

from backend.normalize.enums import NormalizationMethod

PENALTIES: dict[str, Decimal] = {
    "missing_explicit_tax_effect": Decimal("0.05"),
    "inferred_tax_effect": Decimal("0.05"),
    "inferred_shares": Decimal("0.05"),
    "period_ambiguity": Decimal("0.10"),
    "eps_reconciliation_outside_tolerance": Decimal("0.10"),
    "recurring_adjustment": Decimal("0.10"),
    "asymmetric_adjustment": Decimal("0.10"),
    "table_parsing_ambiguity": Decimal("0.15"),
    "source_not_direct": Decimal("0.20"),
}


def base_confidence(
    method: NormalizationMethod,
    direct_eps: bool = False,
    detailed_bridge: bool = False,
    explicit_tax: bool = False,
    clear_period: bool = True,
) -> Decimal:
    if method == NormalizationMethod.S1_SEC_RECONCILIATION:
        if direct_eps and detailed_bridge and explicit_tax and clear_period:
            return Decimal("0.95")
        if direct_eps and detailed_bridge:
            return Decimal("0.90")
        if direct_eps:
            return Decimal("0.85")
        return Decimal("0.75")
    if method == NormalizationMethod.S2_XBRL_SPECIAL_ITEMS:
        return Decimal("0.65") if explicit_tax else Decimal("0.55")
    if method == NormalizationMethod.S4_GAAP_FALLBACK:
        return Decimal("0.35")
    return Decimal("0.50")


def apply_penalties(base: Decimal, flags: list[str]) -> tuple[Decimal, dict[str, str]]:
    applied: dict[str, str] = {}
    score = base
    for flag in flags:
        if flag in PENALTIES:
            penalty = PENALTIES[flag]
            score -= penalty
            applied[flag] = str(penalty)
    if score < Decimal("0"):
        score = Decimal("0")
    return score.quantize(Decimal("0.01")), applied

