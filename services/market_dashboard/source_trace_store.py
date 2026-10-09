"""Lossless, content-addressed evidence for new time-series and financial facts.

The row envelope is a storage detail. Readers restore the original source_trace
objects before returning data. Old inline JSON stays valid and is never migrated
by a read. Enable reference writes only after every reader supports this format.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, select

FORMAT = 'source-trace-sha256-v1'
FINANCIAL_FORMAT = 'financial-source-trace-sha256-v1'
FORMAT_KEY = '_market_storage'
REF_KEY = '$market_source_trace'
INLINE_KEY = '$market_source_trace_fields'
COLLECTION_CLOCKS = frozenset(('received_at', 'collected_at', 'ingested_at'))
READ_BATCH = 400  # Below SQLite's historical 999-variable limit.
WRITE_BATCH = 100


def storage_capabilities():
    """Pure deployment probe; importing/calling it never opens a database."""
    return {'inline_time_series': True, 'read_formats': [FORMAT],
            'reference_write_env': 'MARKET_SHARED_SOURCE_TRACES',
            'reference_writes_default': False}


class SourceTraceIntegrityError(ValueError):
    """A missing/corrupt reference must never become fabricated evidence."""


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class TraceRecord:
    digest: str
    byte_count: int
    payload: str

    @classmethod
    def from_value(cls, value):
        payload = encode_json(value)
        data = payload.encode('utf-8')
        return cls(hashlib.sha256(data).hexdigest(), len(data), payload)

    def verified_value(self):
        data = self.payload.encode('utf-8')
        if (not re.fullmatch(r'[0-9a-f]{64}', self.digest)
                or self.byte_count != len(data)
                or hashlib.sha256(data).hexdigest() != self.digest):
            raise SourceTraceIntegrityError('Stored source-trace bytes or hash do not match')
        try:
            value = json.loads(self.payload)
        except (TypeError, ValueError) as exc:
            raise SourceTraceIntegrityError('Stored source-trace JSON is invalid') from exc
        try:
            canonical = encode_json(value)
        except (TypeError, ValueError) as exc:
            raise SourceTraceIntegrityError('Stored source-trace encoding is invalid') from exc
        if not isinstance(value, dict) or canonical != self.payload:
            raise SourceTraceIntegrityError('Stored source-trace encoding is invalid')
        return value


def compact_payloads(payloads, *, format=FORMAT):
    """Return storage JSON and exact evidence records, without touching a DB.

    Evidence is referenced only when the whole row becomes smaller. Nulls and
    every evidence field are preserved; rows with only small traces stay inline.
    Financial envelopes retain collection clocks inline and share the remaining
    evidence. This format requires its own financial-reader rollout capability.
    The hash covers canonical UTF-8 JSON, not an upstream document's raw bytes;
    the upstream document hash remains an unchanged field inside the evidence.
    """
    if format not in (FORMAT, FINANCIAL_FORMAT):
        raise SourceTraceIntegrityError('Unsupported source-trace write format')
    payloads = list(payloads)
    bodies, records = [], {}
    for body in payloads:
        row = json.loads(body)
        if not isinstance(row, dict) or FORMAT_KEY in row:
            raise SourceTraceIntegrityError('Unexpected time-series storage envelope')
        pending = {}

        def visit(value, pending):
            if isinstance(value, list):
                return [visit(item, pending) for item in value]
            if not isinstance(value, dict):
                return value
            result = {}
            for key, child in value.items():
                if key == 'source_trace' and isinstance(child, dict):
                    # Financial collection clocks differ for each account. Keep
                    # their exact values on the fact; never discard or normalize
                    # them into another observation's evidence.
                    fields = ({k: v for k, v in child.items() if k in COLLECTION_CLOCKS}
                              if format == FINANCIAL_FORMAT else {})
                    record = TraceRecord.from_value(
                        {k: v for k, v in child.items() if k not in fields})
                    if record.digest in pending and pending[record.digest] != record:
                        raise SourceTraceIntegrityError('Conflicting source-trace identity')
                    pending[record.digest] = record
                    result[key] = {REF_KEY: record.digest,
                                   **({INLINE_KEY: fields} if fields else {})}
                else:
                    result[key] = visit(child, pending)
            return result

        compact = encode_json({FORMAT_KEY: format, 'row': visit(row, pending)})
        if pending and len(compact.encode('utf-8')) < len(body.encode('utf-8')):
            bodies.append(compact)
            for digest, record in pending.items():
                if digest in records and records[digest] != record:
                    raise SourceTraceIntegrityError('Conflicting source-trace identity')
                records[digest] = record
        else:
            bodies.append(body)
    if format == FINANCIAL_FORMAT:
        # Avoid creating a separate trace row when all contexts are unique.
        # This is a conservative serialized-byte budget, not a prediction of
        # PostgreSQL heap/TOAST/index size. Actual physical savings are separate.
        stored_bytes = sum(len(body.encode('utf-8')) for body in bodies)
        stored_bytes += sum(record.byte_count + len(record.digest) + 4 + 128
                            for record in records.values())
        if stored_bytes >= sum(len(body.encode('utf-8')) for body in payloads):
            return payloads, {}
    return bodies, records


class SourceTraceStore:
    def __init__(self, store, *, create_schema=True):
        self.store = store
        metadata = MetaData()
        self.table = Table('market_source_traces', metadata,
            Column('digest', String(64), primary_key=True),
            Column('byte_count', Integer, nullable=False),
            Column('payload', Text, nullable=False))
        # Additive only: no existing table/row is rewritten. New releases first
        # ship this reader with reference writes disabled to establish rollback.
        if create_schema:
            metadata.create_all(store.engine)

    def persist(self, records, db):
        """Insert immutable evidence in the SAME transaction as its row refs."""
        if not records:
            return
        if self.store.engine.dialect.name == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        elif self.store.engine.dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
        else:
            raise ValueError('Shared source traces require SQLite or PostgreSQL')
        for digest, record in records.items():
            if digest != record.digest:
                raise SourceTraceIntegrityError('Source-trace reference does not match its record')
            record.verified_value()
        # Concurrent collectors can encounter the same traces in opposite row
        # order. Acquire content-key conflicts in one order to avoid deadlocks.
        values = [{'digest': records[key].digest, 'byte_count': records[key].byte_count,
                   'payload': records[key].payload} for key in sorted(records)]
        inserted = set()
        for offset in range(0, len(values), WRITE_BATCH):
            statement = insert(self.table).values(values[offset:offset + WRITE_BATCH])
            inserted.update(db.execute(statement.on_conflict_do_nothing(
                index_elements=[self.table.c.digest]).returning(self.table.c.digest)).scalars())
        # A conflict never overwrites evidence. Verify existing bytes rather than
        # assuming that a matching primary key guarantees an intact record.
        conflicts = set(records) - inserted
        existing = self._verify_records(self._fetch_records(conflicts, db), conflicts)
        for digest, value in existing.items():
            if encode_json(value) != records[digest].payload:
                raise SourceTraceIntegrityError('Conflicting stored source-trace bytes')

    def _fetch_records(self, digests, db):
        result = {}
        ordered = sorted(digests)
        for offset in range(0, len(ordered), READ_BATCH):
            batch = ordered[offset:offset + READ_BATCH]
            rows = db.execute(select(self.table.c.digest, self.table.c.byte_count,
                                     self.table.c.payload)
                              .where(self.table.c.digest.in_(batch))).all()
            for digest, byte_count, body in rows:
                result[digest] = TraceRecord(digest, byte_count, body)
        return result

    @staticmethod
    def _verify_records(records, digests):
        result = {digest: record.verified_value() for digest, record in records.items()}
        if set(result) != set(digests):
            raise SourceTraceIntegrityError('A referenced source-trace record is missing')
        return result

    def restore_payloads(self, payloads, *, connection=None):
        """Bulk dual read; never expose storage refs or share mutable dicts."""
        decoded, references = [], set()

        def collect(value, format):
            if isinstance(value, list):
                for item in value:
                    collect(item, format)
            elif isinstance(value, dict):
                for key, child in value.items():
                    if key == 'source_trace' and isinstance(child, dict) and REF_KEY in child:
                        digest = child.get(REF_KEY)
                        fields = child.get(INLINE_KEY, {})
                        allowed = {REF_KEY, INLINE_KEY} if format == FINANCIAL_FORMAT else {REF_KEY}
                        if (not set(child) <= allowed or not isinstance(digest, str)
                                or not isinstance(fields, dict)
                                or not set(fields) <= COLLECTION_CLOCKS
                                or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                            raise SourceTraceIntegrityError('Invalid source-trace reference')
                        references.add(digest)
                    else:
                        collect(child, format)

        for body in payloads:
            value = json.loads(body)
            compact = isinstance(value, dict) and FORMAT_KEY in value
            if compact:
                if (value.get(FORMAT_KEY) not in (FORMAT, FINANCIAL_FORMAT)
                        or set(value) != {FORMAT_KEY, 'row'}
                        or not isinstance(value['row'], dict) or FORMAT_KEY in value['row']):
                    raise SourceTraceIntegrityError('Unsupported time-series storage envelope')
                format = value[FORMAT_KEY]
                value = value['row']
                collect(value, format)
            decoded.append((compact, value))
        # Source records are immutable and commit with row references. Returning
        # the pooled row-query connection before JSON/hash work is safe; another
        # short checkout is needed only when these rows actually contain refs.
        records = {}
        if references:
            # Append's legacy-identity check already owns a transaction. Reuse
            # it to see its writes and avoid a second checkout from a size-1 pool.
            context = (self.store.read_connection() if connection is None
                       else nullcontext(connection))
            with context as db:
                records = self._fetch_records(references, db)
        traces = self._verify_records(records, references)

        def restore(value):
            if isinstance(value, list):
                return [restore(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {}
            for key, child in value.items():
                if key == 'source_trace' and isinstance(child, dict) and REF_KEY in child:
                    evidence = deepcopy(traces[child[REF_KEY]])
                    fields = child.get(INLINE_KEY, {})
                    if set(fields) & set(evidence):
                        raise SourceTraceIntegrityError(
                            'Inline evidence conflicts with shared evidence')
                    result[key] = {**evidence, **deepcopy(fields)}
                else:
                    result[key] = restore(child)
            return result

        return [restore(value) if compact else value for compact, value in decoded]
