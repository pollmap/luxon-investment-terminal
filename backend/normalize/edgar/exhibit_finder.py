from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FilingDocument:
    sequence: str
    document: str
    document_type: str
    description: str
    url: str


def looks_like_earnings_exhibit(document: FilingDocument) -> bool:
    doc_type = document.document_type.lower()
    description = document.description.lower()
    filename = document.document.lower()
    if "ex-99" not in doc_type:
        return False
    cues = ("earnings", "press release", "results", "financial results", "quarterly", "fiscal")
    return any(cue in description or cue in filename for cue in cues) or "ex99" in filename

