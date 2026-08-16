from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pandas as pd

from backend.normalize.parsers.html_tables import extract_tables, row_hash
from backend.normalize.parsers.numeric import is_eps_like, is_share_count_like, parse_decimal
from backend.normalize.taxonomy import match_category, normalize_label

RECON_KEYWORDS = (
    "reconciliation",
    "non-gaap",
    "adjusted",
    "diluted eps",
    "adjusted eps",
    "adjusted earnings",
    "adjusted net income",
)


@dataclass
class ExtractedRow:
    row_type: str
    label: str
    normalized_label: str
    value: Decimal | None
    raw_value: str
    canonical_category: str | None = None
    row_hash: str | None = None
    confidence: Decimal = Decimal("0.5")


@dataclass
class ReconciliationTable:
    table_index: int
    table_hash: str
    title: str | None
    raw_dataframe: pd.DataFrame
    candidate_score: int
    rows: list[ExtractedRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def find_reconciliation_tables(html: str) -> list[ReconciliationTable]:
    candidates: list[ReconciliationTable] = []
    for index, (hash_value, frame, title) in enumerate(extract_tables(html)):
        score = _score_table(frame, title)
        if score <= 0:
            continue
        table = ReconciliationTable(
            table_index=index,
            table_hash=hash_value,
            title=title,
            raw_dataframe=frame,
            candidate_score=score,
        )
        table.rows = _extract_rows(frame)
        if not any(row.row_type in {"adjusted_eps", "adjusted_ni"} for row in table.rows):
            table.warnings.append("adjusted_value_not_found")
        candidates.append(table)
    return sorted(candidates, key=lambda item: item.candidate_score, reverse=True)


def _score_table(frame: pd.DataFrame, title: str | None) -> int:
    score = 0
    haystack = " ".join([title or "", " ".join(map(str, frame.head(6).to_numpy().flatten()))]).lower()
    for keyword in RECON_KEYWORDS:
        if keyword in haystack:
            score += 2
    row_labels = " ".join(str(row[0]) for row in frame.to_numpy() if len(row)).lower()
    for keyword in ("gaap", "non-gaap", "adjusted", "tax", "shares", "diluted"):
        if keyword in row_labels:
            score += 1
    return score


def _extract_rows(frame: pd.DataFrame) -> list[ExtractedRow]:
    rows: list[ExtractedRow] = []
    for raw_row in frame.to_numpy().tolist():
        if not raw_row:
            continue
        label = str(raw_row[0]).strip()
        if not label or label.lower() in {"nan", ""}:
            continue
        value = _first_numeric(raw_row[1:])
        normalized = normalize_label(label)
        row_type = classify_row(label)
        category = match_category(label).canonical_category if row_type == "adjustment_line" else None
        rows.append(
            ExtractedRow(
                row_type=row_type,
                label=label,
                normalized_label=normalized,
                value=value,
                raw_value="" if value is None else str(value),
                canonical_category=category,
                row_hash=row_hash(raw_row),
                confidence=Decimal("0.75") if row_type != "unknown" else Decimal("0.35"),
            )
        )
    return rows


def _first_numeric(values: list[Any]) -> Decimal | None:
    for value in values:
        parsed = parse_decimal(value)
        if parsed is not None:
            return parsed
    return None


def classify_row(label: str) -> str:
    normalized = normalize_label(label)
    if "non-gaap" in normalized or "adjusted" in normalized:
        if is_eps_like(normalized):
            return "adjusted_eps"
        if "net income" in normalized or "earnings" in normalized:
            return "adjusted_ni"
    if "gaap" in normalized:
        if is_eps_like(normalized):
            return "gaap_eps_diluted" if "diluted" in normalized else "gaap_eps"
        if "net income" in normalized or "earnings" in normalized:
            return "gaap_ni"
    if is_share_count_like(normalized):
        return "diluted_shares"
    if "tax" in normalized and "effect" in normalized:
        return "tax_effect"
    if "discontinued operations" in normalized:
        return "discontinued_ops"
    if any(token in normalized for token in ("restructuring", "impairment", "amortization", "acquisition", "stock-based", "share-based", "settlement", "extinguishment", "gain", "loss")):
        return "adjustment_line"
    return "unknown"

