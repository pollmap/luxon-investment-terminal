from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class RawWriteResult:
    path: Path
    sha256: str
    manifest_path: Path


class AppendOnlyRawStore:
    def __init__(self, root: str | Path = "data/raw") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_bytes(
        self,
        *,
        market: str,
        source: str,
        identifier: str,
        payload: bytes,
        suffix: str,
        metadata: dict[str, Any] | None = None,
    ) -> RawWriteResult:
        digest = hashlib.sha256(payload).hexdigest()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        directory = self.root / market.lower() / source.lower() / identifier
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stamp}-{digest[:12]}.{suffix.lstrip('.')}"
        if path.exists():
            raise FileExistsError(f"append-only raw path already exists: {path}")
        path.write_bytes(payload)
        manifest_path = path.with_suffix(path.suffix + ".manifest.json")
        manifest = {
            "market": market,
            "source": source,
            "identifier": identifier,
            "sha256": digest,
            "path": str(path),
            "written_at": stamp,
            "metadata": metadata or {},
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return RawWriteResult(path=path, sha256=digest, manifest_path=manifest_path)


class LocalSQLiteStore:
    def __init__(self, path: str | Path = "data/app.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS watchlists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_transactions (
                    id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    currency TEXT NOT NULL,
                    source_trace TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS data_quality_checks (
                    id TEXT PRIMARY KEY,
                    fact_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    flags TEXT NOT NULL,
                    checked_at TEXT NOT NULL
                );
                """
            )


class DuckDBWarehouse:
    def __init__(self, db_path: str | Path = "data/warehouse/warehouse.duckdb") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.db_path))

    def write_parquet(self, rows: list[dict[str, Any]], path: str | Path) -> Path:
        parquet_path = Path(path)
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, parquet_path)
        return parquet_path

    def query_parquet(self, path: str | Path, sql: str = "SELECT * FROM parquet_scan(?)") -> list[tuple]:
        with self.connect() as connection:
            return connection.execute(sql, [str(path)]).fetchall()

