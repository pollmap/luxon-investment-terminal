from __future__ import annotations

from typing import Literal
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .metrics import METRICS

_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


@lru_cache(maxsize=16384)
def search_tokens(name):
    compact = "".join(name.casefold().split())
    initials = "".join(_INITIALS[(ord(c) - 0xAC00) // 588] if 0xAC00 <= ord(c) <= 0xD7A3 else c for c in compact)
    return compact, initials


def matches_security(row, query):
    """Search names, codes, explicit aliases and Hangul initials identically."""
    needle = "".join(query.casefold().split())
    if not needle:
        return True
    aliases = row.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    names = [str(row.get("name", "")), str(row.get("code", "")), *[str(a) for a in aliases]]
    for name in names:
        compact, initials = search_tokens(name)
        if needle in compact:
            return True
        if needle in initials:
            return True
    return False


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    operator: Literal["gte", "lte", "eq"] = "gte"
    value: float = Field(allow_inf_nan=False)

    @field_validator("metric")
    @classmethod
    def valid_metric(cls, value):
        if value not in METRICS:
            raise ValueError("Unknown metric")
        return value


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(default="", max_length=100)
    sector: str = Field(default="", max_length=100)
    market: Literal["ALL", "KOSPI", "KOSDAQ", "KONEX"] = "ALL"
    rules: list[Rule] = Field(default_factory=list, max_length=20)
    any_rules: list[Rule] = Field(default_factory=list, max_length=20)
    sort: str = "value"
    direction: Literal["asc", "desc"] = "desc"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    codes: list[str] | None = Field(default=None, max_length=1000)

    @field_validator("sort")
    @classmethod
    def valid_sort(cls, value):
        if value not in METRICS and value != "name":
            raise ValueError("Unknown sort")
        return value


def apply_query(rows, query):
    def match(row, rule):
        value = row.get(rule.metric)
        if value is None:
            return False
        return {"gte": value >= rule.value, "lte": value <= rule.value, "eq": value == rule.value}[
            rule.operator
        ]

    result = [
        row
        for row in rows
        if (query.market == "ALL" or row["market"] == query.market)
        and matches_security(row, query.q)
        and (not query.sector or row.get("sector") == query.sector)
        and (query.codes is None or row["code"] in query.codes)
        and all(match(row, r) for r in query.rules)
        and (not query.any_rules or any(match(row, r) for r in query.any_rules))
    ]
    known = [r for r in result if r.get(query.sort) is not None]
    missing = [r for r in result if r.get(query.sort) is None]
    known.sort(key=lambda r: (r[query.sort], r["code"]), reverse=query.direction == "desc")
    result = known + missing
    return {"rows": result[query.offset : query.offset + query.limit], "total": len(result)}
