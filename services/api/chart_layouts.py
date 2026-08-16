from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from services.api.database import get_engine, postgres_enabled

OWNER_KEY_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,63})\Z")


def list_chart_layouts(owner_key: str = "default") -> dict[str, Any]:
    owner_key = _validated_owner_key(owner_key)
    if postgres_enabled():
        return _list_chart_layouts_postgres(owner_key)
    return _list_chart_layouts_manifest(owner_key)


def save_chart_layout(
    name: str,
    config: dict[str, Any],
    owner_key: str = "default",
) -> dict[str, Any]:
    owner_key = _validated_owner_key(owner_key)
    normalized = _normalized_config(config)
    trace = _source_trace(owner_key, name)
    record = {
        "id": _layout_id(owner_key, name),
        "owner_key": owner_key,
        "name": name.strip() or "Default layout",
        "ticker": str(normalized["company_id"]).upper(),
        "metric": str(normalized["metric"]),
        "config": normalized,
        "source_trace": trace,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if postgres_enabled():
        return _save_chart_layout_postgres(record)
    _save_chart_layout_manifest(record)
    return record


def delete_chart_layout(layout_id: str, owner_key: str = "default") -> bool:
    owner_key = _validated_owner_key(owner_key)
    layout_id = _canonical_layout_id(layout_id)
    if postgres_enabled():
        with get_engine().begin() as connection:
            result = connection.execute(
                text("DELETE FROM chart_layouts WHERE id = :id AND owner_key = :owner_key"),
                {"id": uuid.UUID(layout_id), "owner_key": owner_key},
            )
        return bool(result.rowcount)
    path = _manifest_path(owner_key, layout_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def _list_chart_layouts_postgres(owner_key: str) -> dict[str, Any]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, owner_key, name, ticker, metric, config, source_trace,
                       created_at, updated_at
                FROM chart_layouts
                WHERE owner_key = :owner_key
                ORDER BY updated_at DESC, name
                """
            ),
            {"owner_key": owner_key},
        ).mappings().all()
    return {
        "owner_key": owner_key,
        "items": [_row_to_record(row) for row in rows],
        "source_trace": _list_source_trace(owner_key, "source_backed"),
    }


def _save_chart_layout_postgres(record: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    with get_engine().begin() as connection:
        existing = connection.execute(
            text(
                """
                SELECT id, created_at
                FROM chart_layouts
                WHERE owner_key = :owner_key AND name = :name
                """
            ),
            {"owner_key": record["owner_key"], "name": record["name"]},
        ).mappings().first()
        record_id = existing["id"] if existing else uuid.UUID(record["id"])
        created_at = existing["created_at"] if existing else now
        connection.execute(
            text(
                """
                INSERT INTO chart_layouts (
                  id, owner_key, name, ticker, metric, config, source_trace,
                  created_at, updated_at
                )
                VALUES (
                  :id, :owner_key, :name, :ticker, :metric, CAST(:config AS jsonb),
                  CAST(:source_trace AS jsonb), :created_at, :updated_at
                )
                ON CONFLICT ON CONSTRAINT uq_chart_layouts_owner_name DO UPDATE SET
                  ticker = EXCLUDED.ticker,
                  metric = EXCLUDED.metric,
                  config = EXCLUDED.config,
                  source_trace = EXCLUDED.source_trace,
                  updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "id": record_id,
                "owner_key": record["owner_key"],
                "name": record["name"],
                "ticker": record["ticker"],
                "metric": record["metric"],
                "config": json.dumps(record["config"]),
                "source_trace": json.dumps(record["source_trace"]),
                "created_at": created_at,
                "updated_at": now,
            },
        )
    return _row_to_record(
        {
            **record,
            "id": record_id,
            "created_at": created_at,
            "updated_at": now,
        }
    )


def _list_chart_layouts_manifest(owner_key: str) -> dict[str, Any]:
    directory = _layout_dir(owner_key)
    items = []
    if directory.exists():
        for path in directory.glob("*.json"):
            items.append(json.loads(path.read_text(encoding="utf-8")))
    items.sort(
        key=lambda item: (item.get("updated_at") or "", item.get("name") or ""),
        reverse=True,
    )
    return {
        "owner_key": owner_key,
        "items": items,
        "source_trace": _list_source_trace(owner_key, "local_manifest"),
    }


def _save_chart_layout_manifest(record: dict[str, Any]) -> None:
    path = _manifest_path(record["owner_key"], record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing_manifest_by_name(record["owner_key"], record["name"])
    if existing:
        existing_path, existing_record = existing
        record["id"] = existing_record["id"]
        record["created_at"] = existing_record.get("created_at") or record["created_at"]
        path = existing_path
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def _existing_manifest_by_name(owner_key: str, name: str) -> tuple[Path, dict[str, Any]] | None:
    directory = _layout_dir(owner_key)
    if not directory.exists():
        return None
    for path in directory.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("name") == name:
            return path, record
    return None


def _row_to_record(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "owner_key": row["owner_key"],
        "name": row["name"],
        "ticker": row["ticker"],
        "metric": row["metric"],
        "config": row["config"] or {},
        "source_trace": row["source_trace"] or {},
        "created_at": (
            row["created_at"].isoformat()
            if hasattr(row["created_at"], "isoformat")
            else row["created_at"]
        ),
        "updated_at": (
            row["updated_at"].isoformat()
            if hasattr(row["updated_at"], "isoformat")
            else row["updated_at"]
        ),
    }


def _normalized_config(config: dict[str, Any]) -> dict[str, Any]:
    visibility = config.get("visibility") or {}
    return {
        "company_id": str(config.get("company_id") or config.get("ticker") or "").upper(),
        "metric": str(config.get("metric") or "adjusted_operating"),
        "forecast_mode": str(config.get("forecast_mode") or "custom"),
        "forecast_case": str(config.get("forecast_case") or "median"),
        "forecast_years": int(config.get("forecast_years") or 5),
        "start_year": _optional_year(config.get("start_year")),
        "end_year": _optional_year(config.get("end_year")),
        "normal_multiple_years": _optional_window(config.get("normal_multiple_years")),
        "user_growth_rate": _optional_str(config.get("user_growth_rate")),
        "target_multiple": _optional_str(config.get("target_multiple")),
        "manual_eps_values": str(config.get("manual_eps_values") or ""),
        "visibility": {
            "price": bool(visibility.get("price", config.get("show_price", True))),
            "metric_area": bool(
                visibility.get("metric_area", config.get("show_metric_area", True))
            ),
            "fair_value": bool(visibility.get("fair_value", config.get("show_fair_value", True))),
            "normal_multiple": bool(
                visibility.get("normal_multiple", config.get("show_normal_multiple", True))
            ),
            "current_valuation": bool(
                visibility.get("current_valuation", config.get("show_current_valuation", True))
            ),
            "custom_valuation": bool(
                visibility.get("custom_valuation", config.get("show_custom_valuation", False))
            ),
            "dividend_floor": bool(
                visibility.get("dividend_floor", config.get("show_dividend_floor", True))
            ),
            "payout_ratio": bool(
                visibility.get("payout_ratio", config.get("show_payout_ratio", True))
            ),
            "dividend_yield": bool(
                visibility.get("dividend_yield", config.get("show_dividend_yield", False))
            ),
            "recession_bands": bool(
                visibility.get("recession_bands", config.get("show_recession_bands", True))
            ),
            "forecast": bool(visibility.get("forecast", config.get("show_forecast", True))),
            "scenario_lines": bool(
                visibility.get("scenario_lines", config.get("show_scenario_lines", True))
            ),
        },
        "hidden_scenario_lines": list(config.get("hidden_scenario_lines") or []),
    }


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_window(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return max(1, min(20, int(value)))


def _optional_year(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _source_trace(owner_key: str, name: str) -> dict[str, Any]:
    return {
        "source_type": "user_chart_layout",
        "source_document_id": f"chart-layout-{owner_key}-{name}",
        "filing_id": f"chart-layout-{owner_key}-{name}",
        "period": "current",
        "unit": "chart_layout",
        "currency": "mixed",
        "formula": "explicit user-saved chart layout configuration",
        "quality_status": "user_provided",
    }


def _list_source_trace(owner_key: str, quality_status: str) -> dict[str, Any]:
    return {
        "source_type": "chart_layouts",
        "source_document_id": f"chart-layouts-{owner_key}",
        "filing_id": f"chart-layouts-{owner_key}",
        "period": "current",
        "unit": "chart_layouts",
        "currency": "mixed",
        "formula": "saved owner chart layout configurations",
        "quality_status": quality_status,
    }


def _layout_id(owner_key: str, name: str) -> str:
    value = f"personal-fastgraphs:chart-layout:{owner_key}:{name}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


def _manifest_path(owner_key: str, layout_id: str) -> Path:
    canonical_id = _canonical_layout_id(layout_id)
    directory = _layout_dir(owner_key)
    path = (directory / f"{canonical_id}.json").resolve()
    if directory not in path.parents:
        raise ValueError("layout_id resolves outside the chart layout directory")
    return path


def _layout_dir(owner_key: str) -> Path:
    owner_key = _validated_owner_key(owner_key)
    root = Path(os.getenv("CHART_LAYOUT_DIR") or "storage/chart_layouts").resolve()
    directory = (root / owner_key).resolve()
    if root not in directory.parents:
        raise ValueError("owner_key resolves outside the chart layout directory")
    return directory


def _validated_owner_key(owner_key: str) -> str:
    if not isinstance(owner_key, str) or not OWNER_KEY_PATTERN.fullmatch(owner_key):
        raise ValueError(
            "owner_key must be a lowercase slug containing only letters, numbers, '_' or '-'"
        )
    return owner_key


def _canonical_layout_id(layout_id: str) -> str:
    try:
        parsed = uuid.UUID(layout_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("layout_id must be a canonical UUID") from exc
    canonical = str(parsed)
    if layout_id != canonical:
        raise ValueError("layout_id must be a canonical UUID")
    return canonical
