"""fixture_non_production: discover future tables and detect content/schema damage."""

import sqlite3

import pytest

from scripts.verify_market_backup import inspect, verify_backup


def database(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
            PRAGMA user_version=7;
            CREATE TABLE source(id INTEGER PRIMARY KEY AUTOINCREMENT, value);
            INSERT INTO source(value) VALUES (1),(1.0),('1'),(NULL),(x'000102ff');
            CREATE TABLE "future table"(id TEXT PRIMARY KEY, payload BLOB);
            INSERT INTO "future table" VALUES ('new',x'ffff0041');
            CREATE INDEX new_index ON source(value);
            CREATE VIEW new_view AS SELECT value FROM source;
            CREATE TRIGGER preserve_audit AFTER INSERT ON source BEGIN
                INSERT OR IGNORE INTO "future table" VALUES ('trigger',NULL);
            END;
        """)


def test_restore_discovers_new_tables_and_preserves_binary_schema_and_source(tmp_path):
    source = tmp_path / "market.db"
    database(source)
    before = inspect(source)
    report = verify_backup(source, tmp_path / "isolated")
    assert report["passed"]
    assert report["source_snapshot"]["tables"]["future table"]["rows"] == 1
    assert report["source_snapshot"]["tables"]["sqlite_sequence"]["rows"] == 1
    assert before == inspect(source)
    with pytest.raises(FileExistsError):
        verify_backup(source, tmp_path / "isolated")
    with pytest.raises(ValueError):
        verify_backup(source, tmp_path)


def test_row_content_and_schema_changes_are_detected_independently(tmp_path):
    source = tmp_path / "market.db"
    database(source)
    report = verify_backup(source, tmp_path / "isolated")
    restored = report["restored"]
    original = inspect(restored)
    with sqlite3.connect(restored) as db:
        db.execute('UPDATE "future table" SET payload=?', (b"changed",))
    changed = inspect(restored)
    assert original["schema_sha256"] == changed["schema_sha256"]
    assert original["tables"] != changed["tables"]
    with sqlite3.connect(restored) as db:
        db.execute("DROP INDEX new_index")
    assert inspect(restored)["schema_sha256"] != changed["schema_sha256"]
