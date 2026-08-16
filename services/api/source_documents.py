from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text

from services.api.database import get_engine, postgres_enabled


MAX_PREVIEW_BYTES = 64 * 1024
LOCAL_RAW_PREFIX = "raw:"
TEXT_SUFFIXES = {".csv", ".htm", ".html", ".json", ".md", ".txt", ".xml"}


def resolve_source_document(source_document_id: str, *, include_preview: bool = True) -> dict[str, Any]:
    """Resolve an audit source_document_id to stored evidence without exposing arbitrary paths."""
    normalized_id = source_document_id.strip()
    if not normalized_id:
        return _missing_response(source_document_id, "empty_source_document_id")

    db_record = _resolve_from_postgres(normalized_id)
    if db_record:
        return _with_local_preview(db_record, include_preview=include_preview)

    if normalized_id.startswith(LOCAL_RAW_PREFIX):
        local_record = _resolve_raw_content_hash(normalized_id)
        if local_record:
            return _with_local_preview(local_record, include_preview=include_preview)

    logical_record = _resolve_logical_id(normalized_id)
    if logical_record:
        return logical_record

    return _missing_response(normalized_id, "source_document_not_found")


def _resolve_from_postgres(source_document_id: str) -> dict[str, Any] | None:
    if not postgres_enabled():
        return None
    try:
        UUID(source_document_id)
        uuid_like = True
    except ValueError:
        uuid_like = False
    try:
        with get_engine().connect() as connection:
            if uuid_like:
                document = connection.execute(
                    text(
                        """
                        SELECT id::text AS source_document_id, source_type AS source,
                               accession_number, form_type, filing_url, source_url,
                               content_hash, local_path, metadata
                        FROM source_documents
                        WHERE id = :source_document_id
                        LIMIT 1
                        """
                    ),
                    {"source_document_id": source_document_id},
                ).mappings().first()
                if document:
                    return _record_from_db_document(document)

            content_hash = _raw_content_hash(source_document_id)
            raw_object = connection.execute(
                text(
                    """
                    SELECT id::text AS raw_object_id, source_document_id::text AS source_document_id,
                           source, ticker, identifier, source_url, blob_url, local_path,
                           content_hash, content_type, metadata
                    FROM raw_objects
                    WHERE source_document_id::text = :source_document_id
                       OR content_hash = :content_hash
                       OR ('raw:' || source || ':' || content_hash) = :source_document_id
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {
                    "source_document_id": source_document_id,
                    "content_hash": content_hash,
                },
            ).mappings().first()
            if raw_object:
                return _record_from_db_raw_object(source_document_id, raw_object)
    except Exception:  # pragma: no cover - DB availability is environment-specific.
        return None
    return None


def _resolve_raw_content_hash(source_document_id: str) -> dict[str, Any] | None:
    parts = source_document_id.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    source = parts[1]
    content_hash = parts[2]
    storage_root = _storage_root()
    search_roots = [
        storage_root / "storage" / "raw" / source,
        storage_root / "storage" / "raw",
        storage_root / "storage" / "cache" / "kr-valuation-inputs",
    ]
    for root in search_roots:
        path = _find_file_by_hash(root, content_hash)
        if path:
            return {
                "source_document_id": source_document_id,
                "status": "found",
                "source": source,
                "content_hash": _file_hash(path) or content_hash,
                "local_path": _display_path(path),
                "source_url": None,
                "filing_url": None,
                "content_type": _content_type(path),
                "preview_available": False,
                "preview_text": None,
                "resolver": "local_raw_content_hash",
                "metadata": {"matched_prefix": content_hash[:12]},
            }
    return None


def _resolve_logical_id(source_document_id: str) -> dict[str, Any] | None:
    local_record = (
        _resolve_kr_derived_valuation_input_id(source_document_id)
        or _resolve_kr_cache_logical_id(source_document_id)
        or _resolve_opendart_logical_id(source_document_id)
    )
    if local_record:
        return _with_local_preview(local_record, include_preview=True)

    if source_document_id.startswith("derived:"):
        source = "derived_metric"
    elif source_document_id.startswith("kr-cache:"):
        source = "kr_cache_diagnostic"
    elif source_document_id.startswith("opendart:"):
        source = "opendart"
    else:
        return None
    return {
        "source_document_id": source_document_id,
        "status": "logical_only",
        "source": source,
        "content_hash": None,
        "local_path": None,
        "source_url": None,
        "filing_url": None,
        "content_type": None,
        "preview_available": False,
        "preview_text": None,
        "resolver": "logical_source_document_id",
        "metadata": {
            "note": "This source_document_id is a deterministic audit identifier. It explains provenance but does not point to a standalone raw file.",
        },
    }


def _resolve_kr_derived_valuation_input_id(source_document_id: str) -> dict[str, Any] | None:
    parts = source_document_id.split(":")
    if len(parts) < 5 or parts[0] != "derived" or parts[1] != "kr" or parts[4] != "valuation-input":
        return None
    ticker = parts[2].upper()
    fiscal_year = _int_or_none(parts[3])
    if fiscal_year is None:
        return None
    cache_path = _find_kr_valuation_cache_file(ticker, fiscal_year)
    if not cache_path:
        return None
    return {
        "source_document_id": source_document_id,
        "status": "found",
        "source": "kr_valuation_warehouse",
        "content_hash": _file_hash(cache_path),
        "local_path": _display_path(cache_path),
        "source_url": None,
        "filing_url": None,
        "content_type": _content_type(cache_path),
        "preview_available": False,
        "preview_text": None,
        "resolver": "local_kr_warehouse_derived_valuation_input",
        "metadata": {
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "logical_source_document_id": source_document_id,
            "derived_metric": parts[4],
            "backing_source": "kr_valuation_input_cache",
            "note": "Derived KR valuation warehouse rows resolve to the source-backed valuation input cache used to build the warehouse.",
        },
    }


def _resolve_kr_cache_logical_id(source_document_id: str) -> dict[str, Any] | None:
    parts = source_document_id.split(":")
    if len(parts) < 3 or parts[0] != "kr-cache":
        return None
    ticker = parts[1].upper()
    fiscal_year = _int_or_none(parts[2])
    cache_path = _find_kr_valuation_cache_file(ticker, fiscal_year)
    if not cache_path:
        return None
    return {
        "source_document_id": source_document_id,
        "status": "found",
        "source": "kr_valuation_input_cache",
        "content_hash": _file_hash(cache_path),
        "local_path": _display_path(cache_path),
        "source_url": None,
        "filing_url": None,
        "content_type": _content_type(cache_path),
        "preview_available": False,
        "preview_text": None,
        "resolver": "local_kr_valuation_cache_logical_id",
        "metadata": {
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "logical_source_document_id": source_document_id,
            "diagnostic": parts[3:] if len(parts) > 3 else [],
        },
    }


def _resolve_opendart_logical_id(source_document_id: str) -> dict[str, Any] | None:
    parts = source_document_id.split(":")
    if len(parts) < 3 or parts[0] != "opendart":
        return None
    ticker = parts[1].upper()
    fiscal_year = _int_or_none(parts[2])
    if fiscal_year is None:
        return None
    raw_root = _safe_local_path(str(_storage_root() / "storage" / "raw" / "opendart" / ticker))
    if not raw_root or not raw_root.exists():
        return None
    candidates = sorted(raw_root.glob(f"*-{fiscal_year}-*.json"))
    if not candidates:
        return None
    path = candidates[0]
    return {
        "source_document_id": source_document_id,
        "status": "found",
        "source": "opendart",
        "content_hash": _file_hash(path),
        "local_path": _display_path(path),
        "source_url": None,
        "filing_url": None,
        "content_type": _content_type(path),
        "preview_available": False,
        "preview_text": None,
        "resolver": "local_opendart_logical_id",
        "metadata": {
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "logical_source_document_id": source_document_id,
            "diagnostic": parts[3:] if len(parts) > 3 else [],
        },
    }


def _with_local_preview(record: dict[str, Any], *, include_preview: bool) -> dict[str, Any]:
    path = _safe_local_path(record.get("local_path"))
    if not path:
        return record | {"preview_available": False, "preview_text": None}
    record = record | {
        "local_path": _display_path(path),
        "content_type": record.get("content_type") or _content_type(path),
    }
    if not include_preview or not _preview_supported(path, record.get("content_type")):
        return record | {"preview_available": False, "preview_text": None}
    try:
        payload = path.read_bytes()[:MAX_PREVIEW_BYTES]
        preview = payload.decode("utf-8", errors="replace")
        if path.stat().st_size > MAX_PREVIEW_BYTES:
            preview = f"{preview}\n\n[preview truncated at {MAX_PREVIEW_BYTES} bytes]"
        return record | {"preview_available": True, "preview_text": preview}
    except OSError as exc:
        metadata = dict(record.get("metadata") or {})
        metadata["preview_error"] = str(exc)
        return record | {"preview_available": False, "preview_text": None, "metadata": metadata}


def _record_from_db_document(row: Any) -> dict[str, Any]:
    return {
        "source_document_id": row["source_document_id"],
        "status": "found",
        "source": row["source"],
        "content_hash": row["content_hash"],
        "local_path": row["local_path"],
        "source_url": row["source_url"],
        "filing_url": row["filing_url"],
        "content_type": None,
        "preview_available": False,
        "preview_text": None,
        "resolver": "postgres_source_documents",
        "metadata": _dict_or_empty(row["metadata"])
        | {
            "accession_number": row["accession_number"],
            "form_type": row["form_type"],
        },
    }


def _record_from_db_raw_object(requested_id: str, row: Any) -> dict[str, Any]:
    return {
        "source_document_id": row["source_document_id"] or requested_id,
        "status": "found",
        "source": row["source"],
        "content_hash": row["content_hash"],
        "local_path": row["local_path"],
        "source_url": row["source_url"] or row["blob_url"],
        "filing_url": None,
        "content_type": row["content_type"],
        "preview_available": False,
        "preview_text": None,
        "resolver": "postgres_raw_objects",
        "metadata": _dict_or_empty(row["metadata"])
        | {
            "raw_object_id": row["raw_object_id"],
            "ticker": row["ticker"],
            "identifier": row["identifier"],
        },
    }


def _missing_response(source_document_id: str, reason: str) -> dict[str, Any]:
    return {
        "source_document_id": source_document_id,
        "status": "missing",
        "source": None,
        "content_hash": _raw_content_hash(source_document_id),
        "local_path": None,
        "source_url": None,
        "filing_url": None,
        "content_type": None,
        "preview_available": False,
        "preview_text": None,
        "resolver": "not_found",
        "metadata": {"reason": reason},
    }


def _find_file_by_hash(root: Path, content_hash: str) -> Path | None:
    safe_root = _safe_local_path(str(root))
    if not safe_root or not safe_root.exists():
        return None
    short_hash = content_hash[:12]
    for candidate in safe_root.rglob(f"*{short_hash}*"):
        if candidate.is_file():
            return candidate
    checked = 0
    for candidate in safe_root.rglob("*"):
        if not candidate.is_file():
            continue
        checked += 1
        if checked > 5000:
            return None
        digest = _file_hash(candidate)
        if digest == content_hash:
            return candidate
    return None


def _find_kr_valuation_cache_file(ticker: str, fiscal_year: int | None) -> Path | None:
    cache_root = _safe_local_path(str(_storage_root() / "storage" / "cache" / "kr-valuation-inputs"))
    if not cache_root or not cache_root.exists():
        return None
    slug = ticker.replace(".", "_")
    candidates = sorted(cache_root.glob(f"{slug}-*-valuation-inputs.json"))
    if fiscal_year is None:
        return candidates[0] if candidates else None
    ranged_candidates: list[tuple[int, Path]] = []
    for candidate in candidates:
        years = _cache_file_year_range(candidate)
        if not years:
            continue
        start_year, end_year = years
        if start_year <= fiscal_year <= end_year:
            ranged_candidates.append((end_year - start_year, candidate))
    if ranged_candidates:
        return sorted(ranged_candidates, key=lambda item: item[0])[0][1]
    return candidates[0] if candidates else None


def _cache_file_year_range(path: Path) -> tuple[int, int] | None:
    stem = path.stem
    parts = stem.split("-")
    if len(parts) < 4:
        return None
    start_year = _int_or_none(parts[-4])
    end_year = _int_or_none(parts[-3])
    if start_year is None or end_year is None:
        return None
    return start_year, end_year


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _safe_local_path(value: Any) -> Path | None:
    if not value:
        return None
    raw_path = Path(str(value))
    storage_root = _storage_root()
    path = raw_path if raw_path.is_absolute() else storage_root / raw_path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    allowed_roots = [
        storage_root / "storage" / "raw",
        storage_root / "storage" / "cache" / "kr-valuation-inputs",
    ]
    try:
        if any(resolved == root.resolve() or root.resolve() in resolved.parents for root in allowed_roots):
            return resolved
    except OSError:
        return None
    return None


def _storage_root() -> Path:
    return Path(os.getenv("SOURCE_DOCUMENT_STORAGE_ROOT", ".")).resolve()


def _file_hash(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _display_path(path: Path) -> str:
    storage_root = _storage_root()
    try:
        return str(path.resolve().relative_to(storage_root))
    except ValueError:
        return str(path)


def _preview_supported(path: Path, content_type: Any) -> bool:
    content_type_text = str(content_type or "")
    return content_type_text.startswith("text/") or path.suffix.lower() in TEXT_SUFFIXES


def _raw_content_hash(source_document_id: str) -> str | None:
    if source_document_id.startswith(LOCAL_RAW_PREFIX):
        parts = source_document_id.split(":", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
    if len(source_document_id) >= 32 and all(char in "0123456789abcdefABCDEF" for char in source_document_id):
        return source_document_id.lower()
    return None


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
