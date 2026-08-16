from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class ScreenerConfig:
    max_per: Decimal = Decimal("25")
    min_roe: Decimal | None = None
    min_eps_cagr: Decimal | None = None
    max_debt_to_equity: Decimal | None = None
    min_market_cap: Decimal | None = None
    min_market_cap_usd: Decimal | None = None
    relative_discount_pct: Decimal = Decimal("0")
    require_roe_gt_roic: bool = True

    def as_meta(self) -> dict[str, str | bool | None]:
        return {
            "max_per": str(self.max_per),
            "min_roe": str(self.min_roe) if self.min_roe is not None else None,
            "min_eps_cagr": str(self.min_eps_cagr) if self.min_eps_cagr is not None else None,
            "max_debt_to_equity": (
                str(self.max_debt_to_equity) if self.max_debt_to_equity is not None else None
            ),
            "min_market_cap": str(self.min_market_cap) if self.min_market_cap is not None else None,
            "min_market_cap_usd": (
                str(self.min_market_cap_usd) if self.min_market_cap_usd is not None else None
            ),
            "relative_discount_pct": str(self.relative_discount_pct),
            "require_roe_gt_roic": self.require_roe_gt_roic,
        }


def apply_screener_filters(
    rows: list[dict[str, Any]],
    config: ScreenerConfig,
) -> list[dict[str, Any]]:
    return [_apply_row(row, config) for row in rows]


def screener_filter_descriptions(config: ScreenerConfig) -> list[str]:
    descriptions = [f"metric_to_value: per <= {config.max_per}"]
    if config.min_roe is not None:
        descriptions.append(f"metric_to_value: roe >= {config.min_roe}")
    if config.min_eps_cagr is not None:
        descriptions.append(f"metric_to_value: eps_cagr >= {config.min_eps_cagr}")
    if config.max_debt_to_equity is not None:
        descriptions.append(f"metric_to_value: debt_to_equity <= {config.max_debt_to_equity}")
    if config.min_market_cap is not None:
        descriptions.append(f"metric_to_value: market_cap >= {config.min_market_cap}")
    if config.min_market_cap_usd is not None:
        descriptions.append(f"metric_to_value: market_cap_usd >= {config.min_market_cap_usd}")
    if config.require_roe_gt_roic:
        descriptions.append("metric_to_metric: roe > roic")
    else:
        descriptions.append("metric_to_metric: disabled")
    descriptions.append(
        "company_relative: per <= normal_pe"
        if config.relative_discount_pct == 0
        else f"company_relative: per <= normal_pe * (1 - {config.relative_discount_pct}% / 100)"
    )
    return descriptions


def _apply_row(row: dict[str, Any], config: ScreenerConfig) -> dict[str, Any]:
    per = _decimal_or_none(row.get("per"))
    normal_pe = _decimal_or_none(row.get("normal_pe"))
    roe = _decimal_or_none(row.get("roe"))
    roic = _decimal_or_none(row.get("roic"))
    eps_cagr = _decimal_or_none(row.get("eps_cagr"))
    debt_to_equity = _decimal_or_none(row.get("debt_to_equity"))
    market_cap = _decimal_or_none(row.get("market_cap"))
    market_cap_usd = _decimal_or_none(row.get("market_cap_usd"))

    metric_to_value_checks = [
        _lte(per, config.max_per),
        _optional_gte(roe, config.min_roe),
        _optional_gte(eps_cagr, config.min_eps_cagr),
        _optional_lte(debt_to_equity, config.max_debt_to_equity),
        _optional_gte(market_cap, config.min_market_cap),
        _optional_gte(market_cap_usd, config.min_market_cap_usd),
    ]
    metric_to_value = all(metric_to_value_checks)
    metric_to_metric = True if not config.require_roe_gt_roic else (
        roe is not None and roic is not None and roe > roic
    )
    relative_threshold = _relative_threshold(normal_pe, config.relative_discount_pct)
    company_relative = (
        per is not None and relative_threshold is not None and per <= relative_threshold
    )
    filters = {
        "metric_to_value": metric_to_value,
        "metric_to_metric": metric_to_metric,
        "company_relative": company_relative,
        "passes_all": metric_to_value and metric_to_metric and company_relative,
    }
    return {
        **row,
        "filters": filters,
        "filter_reasons": _filter_reasons(
            per,
            normal_pe,
            roe,
            roic,
            eps_cagr,
            debt_to_equity,
            market_cap,
            market_cap_usd,
            relative_threshold,
            config,
            filters,
        ),
    }


def _filter_reasons(
    per: Decimal | None,
    normal_pe: Decimal | None,
    roe: Decimal | None,
    roic: Decimal | None,
    eps_cagr: Decimal | None,
    debt_to_equity: Decimal | None,
    market_cap: Decimal | None,
    market_cap_usd: Decimal | None,
    relative_threshold: Decimal | None,
    config: ScreenerConfig,
    filters: dict[str, bool],
) -> list[str]:
    reasons = [
        _reason("P/E", per, "<=", config.max_per),
        _reason("P/E vs relative threshold", per, "<=", relative_threshold),
        _reason("ROE", roe, ">", roic) if config.require_roe_gt_roic else "ROE > ROIC disabled",
    ]
    if config.min_roe is not None:
        reasons.append(_reason("ROE", roe, ">=", config.min_roe))
    if config.min_eps_cagr is not None:
        reasons.append(_reason("EPS CAGR", eps_cagr, ">=", config.min_eps_cagr))
    if config.max_debt_to_equity is not None:
        reasons.append(_reason("Debt/Equity", debt_to_equity, "<=", config.max_debt_to_equity))
    if config.min_market_cap is not None:
        reasons.append(_reason("Market cap", market_cap, ">=", config.min_market_cap))
    if config.min_market_cap_usd is not None:
        reasons.append(_reason("Market cap USD", market_cap_usd, ">=", config.min_market_cap_usd))
    reasons.append(f"All filters: {'pass' if filters['passes_all'] else 'watch'}")
    if normal_pe is None:
        reasons.append("Normal P/E unavailable")
    return reasons


def _relative_threshold(normal_pe: Decimal | None, discount_pct: Decimal) -> Decimal | None:
    if normal_pe is None:
        return None
    return (normal_pe * (Decimal("1") - (discount_pct / Decimal("100")))).quantize(Decimal("0.01"))


def _reason(label: str, left: Decimal | None, operator: str, right: Decimal | None) -> str:
    left_text = str(left) if left is not None else "missing"
    right_text = str(right) if right is not None else "missing"
    return f"{label} {left_text} {operator} {right_text}"


def _lte(left: Decimal | None, right: Decimal) -> bool:
    return left is not None and left <= right


def _optional_lte(left: Decimal | None, right: Decimal | None) -> bool:
    return True if right is None else _lte(left, right)


def _optional_gte(left: Decimal | None, right: Decimal | None) -> bool:
    return True if right is None else left is not None and left >= right


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
