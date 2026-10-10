"""Bounded existing-prefix reads; no connection/process creation or default activation.

Only series replay changes. The frozen checkpoint protocol, codec, original
trace replay and append path are inherited unchanged. No running restore is
modified by importing this module.
"""

from __future__ import annotations

from . import checkpoint as cp

codec = cp.codec
ROW_BATCH_PREFIX = (
    "SELECT series,time,digest,CASE WHEN sum(octet_length(payload)) OVER () <= %s "
    "THEN payload ELSE NULL END AS payload FROM market_compact_restore.rows "
    "WHERE (series,time) IN ("
)


class ReplayBatchSink(cp.PgBatchSink):
    def __init__(self, db, manifest, budget):
        super().__init__(db, manifest, budget)
        self.replay_series_rows = 0
        self.replay_series_batches = 0

    def replay_stats(self):
        # Attempt-local observations, never checkpoint advancement or final proof.
        return {"replay_method": "exact-pk-batch-v1",
                "replay_validated_series_rows": self.replay_series_rows,
                "replay_validated_series_batches": self.replay_series_batches}

    def consume(self, event, before, after, *, replay):
        if not (replay is True and isinstance(event, codec.CopyBatch)
                and event.table == codec.SERIES):
            return super().consume(event, before, after, replay=replay)
        if not self.locked:
            raise codec.Refused("Restore target is not exclusively owned")
        self.budget.check(event.row_count * 128 + len(event.raw) * 4)
        with self.db.transaction():
            db = self.db
            db.execute("SELECT set_config('statement_timeout',%s,true)",
                       (str(self.budget.check()),))
            stage = codec.NativeCopyStage(cp.TransactionView(db), limits=self.manifest.limits())
            stage.create()
            values = stage.decode(event)
            limits = self.manifest.limits()
            if not 1 <= len(values) <= limits.max_batch_rows or len(values) != event.row_count:
                raise codec.Refused("Native decoded row count differs")
            keys = []
            for value in values:
                codec.validate_series(value)
                keys.append(tuple(value[:2]))
            if len(set(keys)) != len(keys):
                raise codec.Refused("Duplicate time-series keys within a batch")
            self.budget.check()
            # Only placeholder syntax is constructed. Never interpolate keys,
            # accept table names, or use independent IN lists (a cross-product).
            statement = ROW_BATCH_PREFIX + ",".join(["(%s,%s)"] * len(keys)) + ") LIMIT %s"
            # Check the total stored payload size inside PG before transfer. A
            # corrupted prefix must not allocate 400 arbitrarily large payloads.
            parameters = ((limits.max_batch_bytes,)
                          + tuple(part for key in keys for part in key) + (len(keys) + 1,))
            found = db.execute(statement, parameters).fetchmany(len(keys) + 1)
            if len(found) != len(keys):
                raise codec.Refused("Previously committed logical row differs")
            stored = {}
            for value in found:
                codec.validate_series(value)
                key = tuple(value[:2])
                if key in stored:
                    raise codec.Refused("Previously committed logical row differs")
                stored[key] = value
            if stored.keys() != set(keys):
                raise codec.Refused("Previously committed logical row differs")
            lookup = cp.TraceLookup(db)
            for index, value in enumerate(values):
                if index % 64 == 0:
                    self.budget.check()
                if codec.row_identity(value, lookup) != codec.row_identity(
                    stored[keys[index]], lookup,
                ):
                    raise codec.Refused("Previously committed logical row differs")
            self.budget.check()
        # COMMIT completing after the budget is not reported as success. Replay
        # writes only temporary native staging, never rows/traces/checkpoint.
        self.budget.check()
        self.replay_series_rows += len(values)
        self.replay_series_batches += 1
