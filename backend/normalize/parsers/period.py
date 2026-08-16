from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FiscalPeriod:
    fiscal_year: int
    fiscal_period: str


def parse_period(text: str, default_year: int | None = None) -> FiscalPeriod | None:
    lowered = re.sub(r"\s+", " ", text.lower())
    year_match = re.search(r"(20\d{2}|19\d{2})", lowered)
    year = int(year_match.group(1)) if year_match else default_year
    if year is None:
        return None

    if any(token in lowered for token in ("year ended", "fiscal year", "twelve months", "full year", "fy")):
        return FiscalPeriod(year, "FY")
    if any(token in lowered for token in ("three months", "quarter", "q1", "q2", "q3", "q4")):
        quarter_match = re.search(r"\bq([1-4])\b", lowered)
        if quarter_match:
            return FiscalPeriod(year, f"Q{quarter_match.group(1)}")
        month_match = re.search(r"(march|june|september|december|mar\.?|jun\.?|sep\.?|dec\.?)", lowered)
        if month_match:
            month = month_match.group(1)[:3]
            quarter = {"mar": "Q1", "jun": "Q2", "sep": "Q3", "dec": "Q4"}[month]
            return FiscalPeriod(year, quarter)
        return FiscalPeriod(year, "Q")
    return FiscalPeriod(year, "FY")

