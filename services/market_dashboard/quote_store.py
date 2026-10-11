"""Incremental quote read model; the original universe snapshot remains intact."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy import cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB


def quote_key(code):
    if not isinstance(code, str) or not re.fullmatch(r'[0-9A-Z]{6}', code):
        raise ValueError('Invalid quote security')
    return 'quote:' + code


def book_revision(legacy, incremental):
    return (hashlib.sha256(f'{legacy or ""}|{incremental}'.encode()).hexdigest()
            if incremental else legacy)


def legacy_quote(store, db, code):
    column = store.snapshots.c.payload
    value = (func.json_extract(column, '$."' + code + '"')
             if store.engine.dialect.name == 'sqlite'
             else cast(column, JSONB)[code])
    raw = db.execute(select(value).where(store.snapshots.c.key == 'quotes')).scalar()
    return json.loads(raw) if isinstance(raw, str) else raw or {}


def read_quote(store, db, code):
    raw = db.execute(select(store.snapshots.c.payload).where(
        store.snapshots.c.key == quote_key(code))).scalar()
    return json.loads(raw) if raw is not None else legacy_quote(store, db, code)


def _older(incoming, existing):
    if not existing:
        return False
    if not incoming:
        return True
    try:
        a, b = datetime.fromisoformat(incoming), datetime.fromisoformat(existing)
        if a.tzinfo and b.tzinfo:
            return a < b
    except (TypeError, ValueError):
        pass
    # Legacy fallback timestamps may omit a timezone. Only accept an explicitly
    # later calendar day; an ambiguous same-day clock cannot reverse a good price.
    return str(incoming)[:10] <= str(existing)[:10]


def merge_quote(previous, patch):
    patch = dict(patch)
    daily = patch.pop('daily_facts', {})
    if 'price' in patch:
        price = patch['price']
        valid = (isinstance(price, (float, int)) and not isinstance(price, bool)
                 and math.isfinite(price) and price > 0)
        primary = previous.get('quote_trace', {}).get('source') in ('toss', 'toss_websocket')
        incoming_primary = patch.get('quote_trace', {}).get('source') in ('toss', 'toss_websocket')
        if (not valid or _older(patch.get('as_of'), previous.get('as_of'))
                or (primary and not incoming_primary)):
            patch = {}
        elif all(previous.get(k) == v for k, v in patch.items()
                 if k not in ('received_at', 'quote_trace')):
            # A repeated last trade is not a new quote merely because fetched again.
            patch = {}
    result = {**previous, **patch}
    if daily:
        merged = dict(previous.get('daily_facts', {}))
        for name, fact in daily.items():
            if not isinstance(fact, dict) or fact.get('value') is None:
                continue
            prior = merged.get(name, {})
            if prior.get('period') and str(fact.get('period', '')) < str(prior['period']):
                continue
            merged[name] = fact
        result['daily_facts'] = merged
    return result


def update_quote(store, code, patch):
    return update_quotes(store, {code: patch})[code]


def update_quotes(store, patches):
    keys = [(code, quote_key(code)) for code in sorted(patches)]
    if not keys:
        return {}
    changed = []
    results = {}
    with store.lock, store.engine.begin() as db:
        if store.engine.dialect.name == 'sqlite':
            db.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            # Unlike SELECT FOR UPDATE, this also serializes first insertion.
            # Sorted acquisition also prevents overlapping batches deadlocking.
            for _, key in keys:
                lock_id = int.from_bytes(
                    hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
                db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_id})
        for code, key in keys:
            raw = db.execute(select(store.snapshots.c.payload).where(
                store.snapshots.c.key == key)).scalar()
            previous = json.loads(raw) if raw is not None else legacy_quote(store, db, code)
            result = merge_quote(previous, patches[code])
            results[code] = result
            # First normal collector touch seeds an unchanged legacy quote too.
            # Preserve its original source clocks/evidence; only storage changes.
            seed = raw is None and bool(previous)
            if (result != previous or seed) and store.put(key, result, _connection=db):
                changed.append(key)
        if changed:
            store.put('quote-book', {'change': uuid4().hex}, _connection=db)
    if changed:
        for key in changed:
            store.notify_changed(key)
        store.notify_changed('quote-book')
        store.notify_changed('quotes')
    return results


def read_all(store, default=None):
    revision = store.revisions(('quotes',)).get('quotes')
    if revision is None:
        return default
    cached = store._cached('__quote_book__')
    if cached and cached[0] == revision:
        return cached[1]
    result, incremental, heads = {}, {}, {}
    with store.read_connection() as db:
        # A single SELECT is one snapshot even at PostgreSQL READ COMMITTED.
        # Reading the legacy blob and incremental rows separately could mix
        # commits; attach precisely the heads observed in this same statement.
        rows = db.execute(select(store.snapshots.c.key, store.snapshots.c.payload,
                                 store.snapshots.c.updated_at).where(or_(
            store.snapshots.c.key.in_(('quotes', 'quote-book')),
            store.snapshots.c.key.like('quote:%'))))
        for key, payload, updated_at in rows:
            if key == 'quotes':
                result = json.loads(payload)
                heads[key] = updated_at
            elif key == 'quote-book':
                heads[key] = updated_at
            else:
                incremental[key[6:]] = json.loads(payload)
    result.update(incremental)
    revision = book_revision(heads.get('quotes'), heads.get('quote-book'))
    store._remember('__quote_book__', revision, result,
                    len(json.dumps(result, ensure_ascii=False).encode()))
    return result
