from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

NULL_TOKENS = {"", "-", "--", "—", "–", "n/a", "na", "nm"}


def parse_decimal(raw: object, *, dash_as_zero: bool = False) -> Decimal | None:
    if raw is None:
        return None
    text = str(raw).strip().lower()
    text = re.sub(r"\[[^\]]+\]|\([a-z]\)", "", text).strip()
    if text in NULL_TOKENS:
        return Decimal("0") if dash_as_zero else None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = text.replace("−", "-").replace("$", "").replace(",", "").replace("%", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", "."}:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return -abs(value) if negative else value


def infer_scale(text: str) -> Decimal:
    lowered = text.lower()
    if "billion" in lowered:
        return Decimal("1000000000")
    if "million" in lowered:
        return Decimal("1000000")
    if "thousand" in lowered:
        return Decimal("1000")
    return Decimal("1")


def is_eps_like(label: str) -> bool:
    lowered = label.lower()
    return "eps" in lowered or "per share" in lowered or "earnings per share" in lowered


def is_share_count_like(label: str) -> bool:
    lowered = label.lower()
    return "weighted average" in lowered or "shares used" in lowered or "diluted shares" in lowered

