from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import threading
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, case, cast, create_engine, event, func, literal, select, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[2]


def now():
    return datetime.now(UTC).isoformat()


def next_snapshot_revision(previous, proposed):
    """Internal version clock; never alter the source's data/collection dates."""
    candidate = datetime.fromisoformat(proposed).astimezone(UTC)
    prior = datetime.fromisoformat(previous).astimezone(UTC)
    if candidate <= prior:
        candidate = prior + timedelta(microseconds=1)
    return candidate.isoformat(timespec='microseconds')


def _raw_source_directory(root: Path, source: str) -> Path:
    """Keep writer namespaces below the operator-configured storage root."""
    if (
        not isinstance(source, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", source) is None
        or source.upper() in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"(?:COM|LPT)[1-9]", source.upper()) is not None
    ):
        raise ValueError("Invalid raw source directory name")

    # The storage root is operator configuration; source is never a path.
    raw_root = root.resolve() / "raw"
    directory = raw_root / source

    def check_boundary():
        for path in (raw_root, directory):
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("Raw source directory links are not supported")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("Raw source directory must be a directory")
        if raw_root.resolve() != raw_root or directory.resolve().parent != raw_root:
            raise ValueError("Raw source directory escapes raw root")

    check_boundary()
    directory.mkdir(parents=True, exist_ok=True)
    # Recheck before any archive file is opened, including compressed writers.
    check_boundary()
    return directory


class Store:
    """Append-only source archive plus atomic public market snapshots."""

    def __init__(self, url=None, root=None):
        self.root = Path(root or os.getenv("MARKET_STORAGE_DIR", ROOT / "storage/public-market"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.url = url or os.getenv("MARKET_DATABASE_URL", f"sqlite:///{self.root / 'market.db'}")
        options = (
            {"connect_args": {"check_same_thread": False, "timeout": 30}}
            if self.url.startswith("sqlite")
            else {}
        )
        if self.url == "sqlite://":
            options["poolclass"] = StaticPool
        self.engine = create_engine(self.url, pool_pre_ping=True, **options)
        if self.url.startswith('sqlite'):
            # Executed inside the atomic upsert, including independent processes.
            # SQLite's built-in strftime only has millisecond fractional precision.
            def register_revision(dbapi, _record):
                dbapi.create_function('market_next_snapshot_revision', 2, next_snapshot_revision)
            event.listen(self.engine, 'connect', register_revision)
        if self.url.startswith("sqlite") and self.url != "sqlite://":
            with self.engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        self.meta = MetaData()
        self.snapshots = Table(
            "market_snapshots",
            self.meta,
            Column("key", String(128), primary_key=True),
            Column("payload", Text, nullable=False),
            Column("updated_at", String(40), nullable=False),
        )
        self.jobs = Table(
            "market_jobs",
            self.meta,
            Column("key", String(128), primary_key=True),
            Column("requested_at", String(40), nullable=False),
        )
        self.meta.create_all(self.engine)
        if self.url.startswith("sqlite"):
            # Cover revision-only reads: updated_at lives after a large JSON TEXT value,
            # so a primary-key lookup alone still touches the payload's overflow pages.
            with self.engine.begin() as db:
                db.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS market_snapshot_revision_idx "
                    "ON market_snapshots (key, updated_at)"
                )
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.cache_sizes = {}
        self.cache_max_bytes = max(1, int(os.getenv("MARKET_DECODE_CACHE_MB", "32"))) * 1024**2
        self.request_times = {}
        self.request_lock = threading.Lock()
        from .time_series import TimeSeries
        self.time_series = TimeSeries(self)
        self.redis = None
        if os.getenv("MARKET_REDIS_URL"):
            from redis import Redis

            self.redis = Redis.from_url(os.environ["MARKET_REDIS_URL"], socket_timeout=1)

    def archive(self, name, rows):
        """Immutable normalized Parquet snapshots for offline DuckDB audits."""
        if not rows:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq

        directory = self.root / "parquet" / name
        directory.mkdir(parents=True, exist_ok=True)
        flat = [
            {
                k: json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else v
                for k, v in row.items()
            }
            for row in rows
        ]
        path = directory / (now().replace(":", "-").replace("+", "_") + ".parquet")
        pq.write_table(pa.Table.from_pylist(flat), path, compression="zstd")

    def raw(self, source, url, content):
        digest = hashlib.sha256(content).hexdigest()
        directory = _raw_source_directory(self.root, source)
        from .raw_archive import COMPRESSED_SOURCES, save_compressed
        if source in COMPRESSED_SOURCES:
            save_compressed(directory, digest, content, url, now())
            return digest
        path = directory / f"{digest}.json"
        if source == 'dart':
            # Exact DART only: XBRL/events have their own archive contracts.
            compressed = (directory / f'{digest}.bin.gz').exists()
            metadata = None
            if path.exists():
                try:
                    metadata = json.loads(path.read_bytes())
                except (ValueError, UnicodeError):
                    if compressed:
                        raise ValueError('Compressed DART metadata is invalid') from None
                if isinstance(metadata, dict) and metadata.get('archive_version') == 2:
                    compressed = True
            if compressed:
                from .raw_archive import read_original_verified
                if not isinstance(metadata, dict):
                    raise ValueError('Compressed DART metadata is missing')
                if read_original_verified(directory, digest) != content:
                    raise ValueError('Compressed DART original differs from received bytes')
                # Keep the original receipt/URL/envelope. A duplicate provider
                # response must not recreate .bin after a verified canary.
                save_compressed(directory, digest, content, url, now())
                return digest
            if os.getenv('MARKET_COMPRESS_DART_RAW', '0') == '1':
                # A separate rollout enables new writes; default remains off.
                save_compressed(directory, digest, content, url, now())
                return digest
        # The digest always identifies the exact original bytes, including ZIP/XBRL/PDF.
        binary = directory / f"{digest}.bin"
        try:
            with binary.open("xb") as file:
                file.write(content)
        except FileExistsError:
            pass
        try:
            payload = content.decode("utf-8") if not content.startswith((b"PK", b"%PDF")) else None
        except UnicodeDecodeError:
            payload = None
        try:
            with path.open("x", encoding="utf-8") as file:
                json.dump(
                    {
                        "url": url,
                        "received_at": now(),
                        "sha256": digest,
                        "payload": payload,
                        "binary_file": binary.name,
                        "bytes": len(content),
                    },
                    file,
                    ensure_ascii=False,
                )
        except FileExistsError:
            pass
        return digest

    def _next_revision(self, proposed):
        if self.engine.dialect.name == 'sqlite':
            return func.market_next_snapshot_revision(self.snapshots.c.updated_at, proposed)
        value = func.greatest(
            cast(self.snapshots.c.updated_at, DateTime(timezone=True)) + text("INTERVAL '1 microsecond'"),
            cast(proposed, DateTime(timezone=True)),
        )
        formatted = func.to_char(func.timezone('UTC', value), 'YYYY-MM-DD"T"HH24:MI:SS.US').op('||')(literal('+00:00'))
        # ISO consumers support four-digit years. Exhaustion must fail the
        # NOT NULL write and roll back, never wrap the clock or overwrite data.
        return case((value <= cast(literal('9999-12-31T23:59:59.999999+00:00'),
                                  DateTime(timezone=True)), formatted), else_=None)

    def put(self, key, payload, *, _connection=None):
        # Stable content revisions let every API process reuse unchanged responses.
        # Atomic upsert also avoids two workers racing between SELECT and INSERT.
        value = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        if self.engine.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        elif self.engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            raise ValueError("Market snapshots require SQLite or PostgreSQL")
        written_at = now()
        with self.lock, (self.engine.begin() if _connection is None else nullcontext(_connection)) as db:
            statement = insert(self.snapshots).values(key=key, payload=value, updated_at=written_at)
            statement = statement.on_conflict_do_update(
                index_elements=[self.snapshots.c.key],
                set_={"payload": statement.excluded.payload, "updated_at": self._next_revision(statement.excluded.updated_at)},
                where=self.snapshots.c.payload != statement.excluded.payload,
            ).returning(self.snapshots.c.updated_at)
            updated = db.execute(statement).first()
            changed = updated is not None
            if changed:
                written_at = updated[0]
                self._drop_cached(key)
            if changed and key.startswith('detail:'):
                from .detail_summary import project as project_summary
                summary_key='detail-summary:'+key.removeprefix('detail:')
                summary=json.dumps(project_summary(payload),ensure_ascii=False,allow_nan=False,sort_keys=True)
                summary_statement=insert(self.snapshots).values(key=summary_key,payload=summary,updated_at=written_at)
                db.execute(summary_statement.on_conflict_do_update(index_elements=[self.snapshots.c.key],
                    set_={'payload':summary_statement.excluded.payload,'updated_at':self._next_revision(summary_statement.excluded.updated_at)},
                    where=self.snapshots.c.payload!=summary_statement.excluded.payload))
                self._drop_cached(summary_key)
            if changed and key.startswith('official:'):
                from .official_views import FIELDS, project, PROJECTION_VERSION, with_share_basis
                # Eager worker projections must use the same independent unit
                # ledger as read_view. Read payload + revision together in this
                # transaction, not via a cached/separate Store.get connection.
                basis_key = 'share-basis:' + key.removeprefix('official:')
                basis_row = db.execute(select(self.snapshots.c.payload, self.snapshots.c.updated_at)
                                       .where(self.snapshots.c.key == basis_key)).first()
                analysis_source = with_share_basis(payload, json.loads(basis_row[0])) if basis_row else payload
                for view in FIELDS:
                    derived_key = 'official-view:' + key.removeprefix('official:') + ':' + view
                    envelope = {'source_revision':written_at, 'projection_version':PROJECTION_VERSION,
                                'payload':project(analysis_source if view == 'analysis' else payload, view)}
                    if view == 'analysis':
                        # A concurrent ledger update leaves an older revision
                        # here, which read_view rejects and rebuilds safely.
                        envelope['share_basis_revision'] = basis_row[1] if basis_row else None
                    derived = json.dumps(envelope,
                                         ensure_ascii=False, allow_nan=False, sort_keys=True)
                    view_statement = insert(self.snapshots).values(key=derived_key, payload=derived, updated_at=written_at)
                    db.execute(view_statement.on_conflict_do_update(index_elements=[self.snapshots.c.key],
                        set_={'payload':view_statement.excluded.payload, 'updated_at':self._next_revision(view_statement.excluded.updated_at)}))
                    self._drop_cached(derived_key)
        if not changed:
            return False
        if _connection is None:
            self.notify_changed(key)
        return True

    def notify_changed(self, key):
        """Publish only after the snapshot transaction has committed."""
        if self.redis:
            try:
                self.redis.delete("market:snapshot:" + key)
                self.redis.publish("market:changed", key)
            except Exception:
                pass

    def compare_and_put(self, key, expected, payload, *, history_key, revision_guards=None):
        """Replace an existing snapshot and preserve its history in one DB transaction.

        Locks, comparisons and writes share a connection across independent Store
        instances/processes. A missing revision guard also participates in the
        conditional claim; no absent-row lock or process-local lock is assumed.
        """
        guards = dict(revision_guards or {})
        if history_key == key or history_key in guards:
            raise ValueError("History key must be independent of guarded snapshots")
        changed = []
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        elif dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            raise ValueError("Market snapshots require SQLite or PostgreSQL")
        with self.lock, self.engine.begin() as db:
            if dialect == "sqlite":
                # Serialize the read/compare/write, including separate file-DB
                # connections. A deferred read-to-write upgrade may fail in WAL.
                db.exec_driver_sql("BEGIN IMMEDIATE")
            statement = select(self.snapshots).where(
                self.snapshots.c.key.in_(sorted({key, *guards}))
            ).order_by(self.snapshots.c.key)
            if dialect == "postgresql":
                statement = statement.with_for_update()
            rows = {row["key"]: row for row in db.execute(statement).mappings()}
            current = rows.get(key)
            if current is None or json.loads(current["payload"]) != expected:
                return False
            if any((rows[name]["updated_at"] if name in rows else None) != revision
                   for name, revision in guards.items()):
                return False
            if expected == payload:
                return False
            # Linearize the comparison at a conditional write. This also checks
            # guards that were absent during SELECT (and thus could not be locked).
            claim = self.snapshots.update().where(
                self.snapshots.c.key == key,
                self.snapshots.c.payload == current["payload"],
                self.snapshots.c.updated_at == current["updated_at"],
            )
            guard_table = self.snapshots.alias("snapshot_guard")
            for name, revision in guards.items():
                actual = select(guard_table.c.updated_at).where(
                    guard_table.c.key == name
                ).scalar_subquery()
                claim = claim.where(actual.is_(None) if revision is None else actual == revision)
            claimed = db.execute(claim.values(payload=current["payload"])
                                 .returning(self.snapshots.c.key)).first()
            if claimed is None:
                return False
            history = insert(self.snapshots).values(
                key=history_key, payload=current["payload"], updated_at=now()
            ).on_conflict_do_nothing(index_elements=[self.snapshots.c.key])
            inserted = db.execute(history.returning(self.snapshots.c.key)).first()
            if inserted:
                self._drop_cached(history_key)
                changed.append(history_key)
            else:
                saved = db.execute(select(self.snapshots.c.payload).where(
                    self.snapshots.c.key == history_key
                )).scalar_one()
                if json.loads(saved) != expected:
                    raise ValueError("Immutable snapshot history differs")
            if self.put(key, payload, _connection=db):
                changed.append(key)
        for name in changed:
            self.notify_changed(name)
        return key in changed

    def revisions(self, keys):
        """Small cross-process revision check without fetching large JSON payloads."""
        keys = tuple(keys)
        if not keys:
            return {}
        with self.read_connection() as db:
            if self.url.startswith("sqlite"):
                # SQLite otherwise prefers the unique PK and reads the large row.
                placeholders = ",".join("?" for _ in keys)
                return dict(
                    db.exec_driver_sql(
                        "SELECT key, updated_at FROM market_snapshots "
                        "INDEXED BY market_snapshot_revision_idx "
                        f"WHERE key IN ({placeholders})",
                        keys,
                    ).all()
                )
            rows = db.execute(
                select(self.snapshots.c.key, self.snapshots.c.updated_at).where(
                    self.snapshots.c.key.in_(keys)
                )
            )
            return dict(rows.all())

    def listed_codes(self):
        """Share only small security identities between background queue loops."""
        with self.lock:
            revision = self.revisions(("universe",)).get("universe")
            cached = getattr(self, "_listed_codes", None)
            if cached and cached[0] == revision:
                return cached[1]
            from .service_scope import included
            codes = tuple(sorted({row["code"] for row in self.get("universe", {}).get("rows", []) if included(row)}))
            self._listed_codes = (revision, codes)
            return codes

    def get(self, key, default=None, *, use_redis=True):
        # Read across worker/API processes; only decoding is cached by revision.
        if self.redis and use_redis:
            try:
                cached = self.redis.get("market:snapshot:" + key)
                if cached:
                    return self._hydrate(json.loads(cached))
            except Exception:
                pass
        with self.read_connection() as db:
            if self.url.startswith("sqlite"):
                revision = db.exec_driver_sql(
                    "SELECT updated_at FROM market_snapshots "
                    "INDEXED BY market_snapshot_revision_idx WHERE key = ?",
                    (key,),
                ).scalar()
            else:
                revision = db.execute(
                    select(self.snapshots.c.updated_at).where(self.snapshots.c.key == key)
                ).scalar()
        # Return the connection before taking the cache lock. Other paths may
        # hold that lock while opening a transaction; waiting here with every
        # pool connection checked out would deadlock those writers/readers.
        if revision is None:
            return default
        cached = self._cached(key)
        if cached and cached[0] == revision:
            return cached[1]
        with self.read_connection() as db:
            row = (
                db.execute(select(self.snapshots).where(self.snapshots.c.key == key))
                .mappings()
                .first()
            )
        if not row:
            return default
        cached = self._cached(key)
        if cached and cached[0] == row["updated_at"]:
            return cached[1]
        value = json.loads(row["payload"])
        series = isinstance(value, dict) and bool(value.get('_series_ref'))
        value = self._hydrate(value)
        # Ordinary snapshots already have their serialized size. Re-encoding a
        # full-universe snapshot here used substantial CPU merely to count bytes.
        size = len((json.dumps(value, ensure_ascii=False) if series else row['payload']).encode('utf-8'))
        self._remember(key, row["updated_at"], value, size)
        if self.redis and use_redis:
            try:
                self.redis.setex("market:snapshot:" + key, 2, row["payload"])
            except Exception:
                pass
        return value

    def metadata(self, key, default=None):
        """Read the series manifest without materializing its full history."""
        with self.read_connection() as db:
            payload = db.execute(select(self.snapshots.c.payload).where(self.snapshots.c.key == key)).scalar()
        return json.loads(payload) if payload else default

    def _hydrate(self,value):
        if isinstance(value,dict) and value.get('_series_ref'):
            return {**value,'rows':self.time_series.rows(value['_series_ref'],value.get('_series_reverse',False))}
        return value

    def _drop_cached(self, key):
        with self.lock:
            self.cache.pop(key, None)
            self.cache_bytes -= self.cache_sizes.pop(key, 0)

    def _cached(self, key):
        with self.lock:
            cached = self.cache.get(key)
            if cached is not None:
                self.cache.move_to_end(key)
            return cached

    def _remember(self, key, revision, value, size):
        # Bound encoded bytes and entries. Decoded Python objects take additional memory.
        with self.lock:
            self._drop_cached(key)
            if size > self.cache_max_bytes:
                return
            while self.cache and (
                self.cache_bytes + size > self.cache_max_bytes or len(self.cache) >= 128
            ):
                self._drop_cached(next(iter(self.cache)))
            self.cache[key] = (revision, value)
            self.cache_sizes[key] = size
            self.cache_bytes += size

    def list_prefix(self, prefix):
        """Read a bounded family of current snapshots in one database query."""
        with self.read_connection() as db:
            rows = (
                db.execute(
                    select(self.snapshots.c.key, self.snapshots.c.payload).where(
                        self.snapshots.c.key.like(prefix.replace("%", "\\%") + "%", escape="\\")
                    )
                )
                .mappings()
                .all()
            )
        return {row["key"]: self._hydrate(json.loads(row["payload"])) for row in rows}

    def request(self, code):
        # Reserve a refresh before I/O so concurrent requests for one symbol
        # coalesce without making unrelated snapshot readers wait for a write.
        with self.request_lock:
            stamp = datetime.now(UTC).timestamp()
            if stamp - self.request_times.get(code, 0) < 30:
                return
            self.request_times[code] = stamp
        if self.engine.dialect.name == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        # Only the isolated in-memory SQLite store shares a single connection.
        guard = self.lock if self.url == 'sqlite://' else nullcontext()
        try:
            with guard, self.engine.begin() as db:
                statement = insert(self.jobs).values(key=code, requested_at=now())
                db.execute(statement.on_conflict_do_update(
                    index_elements=[self.jobs.c.key],
                    set_={'requested_at': statement.excluded.requested_at},
                ))
        except (OperationalError, OSError) as exc:
            # Collection intent is best effort; preserve the retry cooldown on
            # disk failures without pretending the request was persisted.
            logging.getLogger(__name__).warning('Refresh deferred: %s', type(exc).__name__)
            return False
        return True

    def active(self):
        with self.read_connection() as db:
            rows = (
                db.execute(select(self.jobs).order_by(self.jobs.c.requested_at.desc()).limit(40))
                .mappings()
                .all()
            )
        cutoff = datetime.now(UTC).timestamp() - 180
        known=set(self.listed_codes())
        return [
            r["key"] for r in rows if r['key'] in known and datetime.fromisoformat(r["requested_at"]).timestamp() > cutoff
        ]

    @contextmanager
    def read_connection(self):
        # StaticPool uses one DBAPI connection for isolated in-memory runs.
        # A concurrent reader closing its transaction can otherwise roll back
        # another thread's write. File SQLite/PostgreSQL retain parallel reads.
        guard = self.lock if self.url == "sqlite://" else nullcontext()
        with guard, self.engine.connect() as connection:
            yield connection
