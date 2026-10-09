"""Read-only source -> consistent SQLite backup -> isolated restore -> full comparison.

Automatically includes new tables, indexes, triggers, views and binary values.
Fixed-size row hashes are sorted in bounded workspace chunks, without system temp files.
Run: python scripts/verify_market_backup.py --source <db> --out <new-directory>
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import sqlite3
import time
from contextlib import ExitStack, closing
from pathlib import Path
from uuid import uuid4


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute("PRAGMA cache_size=-8192")
    return connection


def fingerprint(connection, scratch):
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=False)
    schema = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' "
        "ORDER BY type,name,tbl_name"
    ).fetchall()
    tables = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND "
        "(name NOT LIKE 'sqlite_%' OR name='sqlite_sequence') ORDER BY name"
    ).fetchall()
    result = {
        "schema_sha256": hashlib.sha256(
            json.dumps(schema, ensure_ascii=False).encode()
        ).hexdigest(),
        "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
        "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
        "tables": {},
    }
    for table_index, (name,) in enumerate(tables):
        columns = [
            row[1]
            for row in connection.execute(f"PRAGMA table_xinfo({identifier(name)})")
            if row[6] != 1
        ]
        projection = ",".join(identifier(c) for c in columns)
        digest, count, chunk, parts = hashlib.sha256(), 0, [], []

        def flush(table_index=table_index, parts=parts, chunk=chunk):
            path = scratch / f"{table_index}-{len(parts)}.sha256"
            path.write_bytes(b"".join(sorted(chunk)))
            parts.append(path)
            chunk.clear()

        # Sort fixed-size row hashes in workspace chunks, never multi-GB payloads
        # in SQLite's system temp directory. Preserve duplicate rows in the digest.
        for row in connection.execute(f"SELECT {projection} FROM {identifier(name)}"):
            row_hash = hashlib.sha256()
            for value in row:
                tag = type(value).__name__.encode()
                raw = value if isinstance(value, bytes) else repr(value).encode("utf-8")
                row_hash.update(tag + b":" + str(len(raw)).encode() + b":" + raw)
            chunk.append(row_hash.digest())
            if len(chunk) == 4096:
                flush()
            count += 1
        if chunk:
            flush()
        with ExitStack() as stack:
            streams = [stack.enter_context(path.open("rb")) for path in parts]

            def hashes(stream):
                while block := stream.read(32):
                    yield block

            for row_hash in heapq.merge(*(hashes(stream) for stream in streams)):
                digest.update(row_hash)
        result["tables"][name] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def inspect(path, scratch=None):
    with closing(readonly(path)) as connection:
        result = fingerprint(connection, scratch or Path(path).parent / ("hashes-" + uuid4().hex))
        result["integrity"] = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        result["foreign_key_errors"] = [
            list(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        return result


def verify_backup(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError("Source database does not exist")
    if output == source or source.is_relative_to(output):
        raise ValueError("Output must be isolated from the source")
    output.mkdir(parents=True, exist_ok=False)
    backup, restored = output / "backup.sqlite", output / "restored.sqlite"
    started = time.monotonic()
    origin = readonly(source)
    try:
        # A read transaction pins one WAL snapshot during hashing and backup.
        # Background collectors may continue; newer writes belong to the next backup.
        origin.execute("BEGIN")
        before = fingerprint(origin, output / "source-hashes")
        with closing(sqlite3.connect(backup)) as target:
            origin.backup(target, pages=256)
    finally:
        origin.close()
    with closing(readonly(backup)) as saved, closing(sqlite3.connect(restored)) as destination:
        saved.backup(destination, pages=256)
    restored_result = inspect(restored, output / "restored-hashes")
    restored_content = {
        k: v for k, v in restored_result.items() if k not in ("integrity", "foreign_key_errors")
    }
    report = {
        "source": str(source),
        "backup": str(backup),
        "restored": str(restored),
        "source_read_only": True,
        "live_database_replaced": False,
        "hash_method": "sha256(sorted(sha256(type-length-value framed rows))); chunks=4096 rows",
        "content_matches": before == restored_content,
        "integrity_ok": restored_result["integrity"] == ["ok"],
        "foreign_keys_ok": not restored_result["foreign_key_errors"],
        "source_snapshot": before,
        "restore": restored_result,
        "seconds": round(time.monotonic() - started, 3),
        "backup_bytes": backup.stat().st_size,
    }
    report["passed"] = all(
        report[key] for key in ("content_matches", "integrity_ok", "foreign_keys_ok")
    )
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = verify_backup(args.source, args.out)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "passed",
                    "content_matches",
                    "integrity_ok",
                    "foreign_keys_ok",
                    "seconds",
                    "backup_bytes",
                )
            },
            ensure_ascii=False,
        )
    )
    print(f"Report: {Path(args.out).resolve() / 'verification.json'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
