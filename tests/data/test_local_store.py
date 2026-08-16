from __future__ import annotations

from data.local_store import AppendOnlyRawStore, DuckDBWarehouse, LocalSQLiteStore


def test_append_only_raw_store_writes_manifest(tmp_path):
    store = AppendOnlyRawStore(tmp_path / "raw")
    result = store.write_bytes(
        market="US",
        source="sec",
        identifier="AAPL",
        payload=b'{"ok": true}',
        suffix="json",
        metadata={"accession": "fixture"},
    )
    assert result.path.exists()
    assert result.manifest_path.exists()
    assert len(result.sha256) == 64


def test_sqlite_store_initializes_tables(tmp_path):
    store = LocalSQLiteStore(tmp_path / "app.sqlite")
    store.initialize()
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {row[0] for row in rows}
    assert {"watchlists", "portfolio_transactions", "data_quality_checks"} <= names


def test_duckdb_warehouse_roundtrips_parquet(tmp_path):
    warehouse = DuckDBWarehouse(tmp_path / "warehouse.duckdb")
    parquet_path = warehouse.write_parquet(
        [{"ticker": "AAPL", "fiscal_year": 2024, "eps": 6.08}],
        tmp_path / "warehouse" / "facts.parquet",
    )
    rows = warehouse.query_parquet(parquet_path)
    assert rows == [("AAPL", 2024, 6.08)]

