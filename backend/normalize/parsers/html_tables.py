from __future__ import annotations

import hashlib
from io import StringIO

import pandas as pd
from bs4 import BeautifulSoup


def table_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8", errors="ignore")).hexdigest()


def row_hash(values: list[object]) -> str:
    joined = "|".join("" if value is None else str(value) for value in values)
    return hashlib.sha256(joined.encode("utf-8", errors="ignore")).hexdigest()


def extract_tables(html: str) -> list[tuple[str, pd.DataFrame, str | None]]:
    soup = BeautifulSoup(html, "lxml")
    tables: list[tuple[str, pd.DataFrame, str | None]] = []
    for table in soup.find_all("table"):
        table_html = str(table)
        title = _nearest_heading(table)
        try:
            dataframes = pd.read_html(StringIO(table_html))
        except ValueError:
            dataframes = []
        for frame in dataframes:
            if frame.empty:
                continue
            frame = frame.fillna("")
            tables.append((table_hash(table_html), frame, title))
    return tables


def _nearest_heading(table) -> str | None:
    current = table
    for _ in range(5):
        current = current.find_previous(["h1", "h2", "h3", "h4", "p", "strong"])
        if current is None:
            return None
        text = current.get_text(" ", strip=True)
        if text:
            return text
    return None

