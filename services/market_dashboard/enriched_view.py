"""Incremental read projection of the compatibility-wide enrichment snapshot."""
import json
import threading

from sqlalchemy import case, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB

PREFIX = 'enriched-item:'
STATE = 'enriched-items-state'
_lock_setup = threading.Lock()


def _projection_lock(store):
    # This lock protects only the incremental projection, not other snapshots.
    # Initialize outside Store.lock so a slow projection cannot queue its readers.
    with _lock_setup:
        lock = getattr(store, '_enriched_view_lock', None)
        if lock is None:
            lock = threading.Lock()
            store._enriched_view_lock = lock
        return lock


def read_item(store, code):
    """Read one validated projection in one statement, with legacy fallback.

    The source revision, projection state and item are observed together. A
    direct/older writer invalidates this projection rather than serving an old
    company's values under the new compatibility snapshot revision.
    """
    source = store.snapshots.alias('enrichment_source')
    state = store.snapshots.alias('enrichment_state')
    item = store.snapshots.alias('enrichment_item')
    if store.engine.dialect.name == 'sqlite':
        state_revision = func.json_extract(state.c.payload, '$.source_revision')
        fallback = func.json_extract(source.c.payload, '$."' + code + '"')
        projected = item.c.payload
    else:
        state_revision = cast(state.c.payload, JSONB)['source_revision'].astext
        fallback = cast(source.c.payload, JSONB)[code]
        projected = cast(item.c.payload, JSONB)
    value = case((state_revision == source.c.updated_at, projected), else_=fallback)
    query = select(value).select_from(
        source.outerjoin(state, state.c.key == STATE).outerjoin(item, item.c.key == PREFIX + code)
    ).where(source.c.key == 'enriched')
    with store.read_connection() as db:
        raw = db.execute(query).scalar()
    return json.loads(raw) if isinstance(raw, str) else raw or {}


def item_revisions(store, keys, heads):
    """Resolve atomically sampled heads without reading financial payloads.

    The tiny projection-generation document is read once per changed revision.
    Warm summary requests need only Store's single covering-index heads query.
    """
    revision, state_revision = heads.get('enriched'), heads.get(STATE)
    if revision is None:
        return {}
    cached = getattr(store, '_enriched_revision_state', None)
    if state_revision and (not cached or cached[0] != state_revision):
        table = store.snapshots
        with store.read_connection() as db:
            row = db.execute(select(table.c.updated_at, table.c.payload)
                             .where(table.c.key == STATE)).first()
        # An intervening writer must not label a new generation with an old head.
        if row and row[0] == state_revision:
            cached = (state_revision, json.loads(row[1]).get('source_revision'))
            store._enriched_revision_state = cached
        else:
            cached = None
    if state_revision and cached and cached == (state_revision, revision):
        # A missing item stays missing when another company's enrichment changes.
        return {key: heads.get(key, 'enriched-item:missing') for key in keys}
    return {key: revision for key in keys}


def publish(store, detail):
    table = store.snapshots
    # Lock the same source row as the backfill, then write source and projection
    # in one transaction. A failed projection must not publish a newer source.
    with store.engine.begin() as db:
        source = db.execute(select(table.c.payload, table.c.updated_at)
                            .where(table.c.key == 'enriched').with_for_update()).first()
        values = json.loads(source[0]) if source else {}
        state_raw = db.execute(select(table.c.payload).where(table.c.key == STATE)).scalar()
        state = json.loads(state_raw) if state_raw else {}
        ready = bool(source and state.get('source_revision') == source[1])
        code = detail['code']
        facts = detail.get('facts', {})
        values[code] = {**values.get(code, {}), 'facts': facts,
                        **{key: fact['value'] for key, fact in facts.items()}}
        changed = store.put('enriched', values, _connection=db)
        if changed and ready:
            store.put(PREFIX + code, values[code], _connection=db)
            revision = db.execute(select(table.c.updated_at)
                                  .where(table.c.key == 'enriched')).scalar_one()
            store.put(STATE, {'source_revision': revision}, _connection=db)
    if changed:
        store.notify_changed('enriched')


def rebuild(store):
    """Idempotent backfill; the source is read-locked against enrichment writes."""
    table = store.snapshots
    with store.engine.begin() as db:
        source = db.execute(select(table.c.payload, table.c.updated_at)
                            .where(table.c.key == 'enriched').with_for_update()).first()
        if source is None:
            return 0
        values = json.loads(source[0])
        for code, value in values.items():
            store.put(PREFIX + code, value, _connection=db)
        existing = db.execute(select(table.c.key)
                              .where(table.c.key.like(PREFIX + '%'))).scalars().all()
        removed = set(existing) - {PREFIX + code for code in values}
        if removed:
            db.execute(table.delete().where(table.c.key.in_(removed)))
        store.put(STATE, {'source_revision': source[1]}, _connection=db)
    return len(values)


def read(store, default=None):
    table = store.snapshots
    with _projection_lock(store):
        # read_connection retains the shared-connection guard for in-memory
        # SQLite, while file SQLite/PostgreSQL can read unrelated data in parallel.
        with store.read_connection() as db:
            heads = dict(db.execute(select(table.c.key, table.c.updated_at)
                                    .where(table.c.key.in_(['enriched', STATE]))).all())
            state_raw = db.execute(select(table.c.payload).where(table.c.key == STATE)).scalar()
            state = json.loads(state_raw) if state_raw else {}
            valid = bool(heads.get('enriched')
                         and state.get('source_revision') == heads['enriched'])
            if valid:
                previous = getattr(store, '_enriched_view', None)
                if previous and previous[0] == heads:
                    return previous[1]
                revisions = dict(db.execute(select(table.c.key, table.c.updated_at)
                                            .where(table.c.key.like(PREFIX + '%'))).all())
                old_revisions = previous[2] if previous else {}
                changed = [key for key, rev in revisions.items() if old_revisions.get(key) != rev]
                payloads = db.execute(select(table.c.key, table.c.payload)
                                      .where(table.c.key.in_(changed))).all() if changed else []
                after = dict(db.execute(select(table.c.key, table.c.updated_at)
                                        .where(table.c.key.in_(['enriched', STATE]))).all())
                valid = heads == after
        if not valid:
            # Release the connection BEFORE a fallback acquires its own. This
            # also handles an interleaved writer without returning mixed versions.
            return store.get('enriched', default or {}, use_redis=False)
        # Decoding does not occupy a pool connection or the general cache lock.
        values = {code: value for code, value in (previous[1].items() if previous else [])
                  if PREFIX + code in revisions}
        for key, payload in payloads:
            values[key.removeprefix(PREFIX)] = json.loads(payload)
        store._enriched_view = (heads, values, revisions)
        return values
