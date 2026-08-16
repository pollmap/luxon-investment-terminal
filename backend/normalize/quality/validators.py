from __future__ import annotations

from decimal import Decimal

from backend.normalize.schemas import AdjustedEarningsRecord, AdjustmentRecord


def eps_bridge_flags(record: AdjustedEarningsRecord) -> list[str]:
    if record.adjusted_ni is None or record.diluted_shares in {None, Decimal("0")}:
        return []
    computed = record.adjusted_ni / record.diluted_shares
    if record.adjusted_eps is None:
        return ["missing_adjusted_eps"]
    tolerance = max(Decimal("0.01"), abs(record.adjusted_eps) * Decimal("0.005"))
    if abs(computed - record.adjusted_eps) > tolerance:
        return ["eps_bridge_outside_tolerance"]
    return []


def sign_flags(adjustment: AdjustmentRecord) -> list[str]:
    flags: list[str] = []
    category = adjustment.canonical_category
    if "gain" in adjustment.item_label.lower() and adjustment.sign > 0:
        flags.append("gain_added_back")
    if ("loss" in adjustment.item_label.lower() or "charge" in adjustment.item_label.lower()) and adjustment.sign < 0:
        flags.append("loss_removed")
    if category == "gain_loss_on_sale" and adjustment.sign == 0:
        flags.append("gain_loss_sign_unknown")
    return flags


def asymmetric_adjustment_flags(adjustments: list[AdjustmentRecord]) -> list[str]:
    categories = {item.canonical_category for item in adjustments}
    gain_loss = [item for item in adjustments if item.canonical_category == "gain_loss_on_sale"]
    if gain_loss and all(item.sign >= 0 for item in gain_loss):
        return ["asymmetric_adjustment"]
    if "gain_loss_on_sale" not in categories:
        return []
    return []


def recurring_flags(history: list[AdjustmentRecord]) -> dict[str, bool]:
    by_category: dict[str, set[tuple[int, str]]] = {}
    for adjustment in history:
        by_category.setdefault(adjustment.canonical_category, set()).add(
            (adjustment.fiscal_year, adjustment.fiscal_period)
        )
    return {category: len(periods) >= 2 for category, periods in by_category.items()}

