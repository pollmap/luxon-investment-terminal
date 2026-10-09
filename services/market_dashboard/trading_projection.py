"""Trading read models rebuilt from existing date-indexed provider observations.

Legacy JSON remains readable. References are internal storage contracts, never
new provider evidence, and never authorize removing an unmatched legacy value.
"""
import json
from calendar import monthrange
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text

from .source_trace_store import SourceTraceIntegrityError

FORMAT = 'toss-trading-reference-v1'
REFERENCE = '_trading_projection'
PERIODS = {'month': 1, 'quarter': 3, 'year': 12, 'three_years': 36, 'five_years': 60}
FINANCING = {'credit': ('credit-trades', 'marginLoan'),
             'stock_loan': ('credit-trades', 'stockLoan'),
             'lending': ('securities-lending', None)}


class TradingProjectionUnavailable(RuntimeError):
    """A stored reference cannot be reconstructed without losing information."""


def dependencies(code, kind):
    sources = {
        'flows': (f'toss:investor-trading:{code}', f'toss-normalized:flows:{code}',
                  f'detail:{code}'),
        'financing': (f'toss:credit-trades:{code}', f'toss:securities-lending:{code}',
                      f'toss-normalized:financing:{code}', f'financing:{code}'),
        'short-selling': (f'toss:short-selling:{code}',
                          f'toss-normalized:short-selling:{code}', f'short-selling:{code}'),
    }
    return sources[kind]


def _cutoff(last, period):
    if period == 'all':
        return None
    if period not in PERIODS:
        raise ValueError('Invalid trading period')
    day = date.fromisoformat(last)
    year, month = divmod(day.year * 12 + day.month - 1 - PERIODS[period], 12)
    return day.replace(year=year, month=month + 1,
                       day=min(day.day, monthrange(year, month + 1)[1])).isoformat()


def normalize_rows(endpoint, rows, source, status):
    """Same values, ordering and evidence as the legacy Toss projection."""
    from .toss import numeric
    old_traces = source.get('record_traces', {})

    def evidence(row):
        trace = row.get('source_trace') or old_traces.get(
            row['date'], source.get('source_trace', {}))
        return {**trace, 'period': row['date']}

    if endpoint == 'investor-trading':
        result = []
        for row in rows:
            ratio = (row.get('foreignerHolding') or {}).get('holdingRate')
            result.append({
                'date': row['date'],
                **{target: numeric((row.get(key) or {}).get('netBuyVolume'))
                   for target, key in [('individual', 'individual'), ('foreign', 'foreigner'),
                                       ('institution', 'institution')]},
                'holding': float(Decimal(str(ratio)) * 100) if ratio is not None else None,
                'source_trace': {**evidence(row), 'scope': '통합'},
            })
        return {'rows': sorted(result, key=lambda row: row['date'], reverse=True),
                'status': status}
    if endpoint in ('credit-trades', 'securities-lending'):
        result = {}
        for kind, (owner, field) in FINANCING.items():
            if owner != endpoint:
                continue
            normalized = []
            for row in rows:
                item = row.get(field) if field else row
                if not item:
                    continue
                ratio = numeric(item.get('balanceRate'))
                normalized.append({
                    'date': row['date'], 'balance': numeric(item.get('balanceQuantity')),
                    'date_basis': 'trade', 'trade_date': row['date'],
                    'new': numeric(item.get('newQuantity' if field else 'executionQuantity')),
                    'repaid': numeric(item.get('returnQuantity' if field else 'repaymentQuantity')),
                    'ratio': ratio * 100 if ratio is not None else None,
                    'source_trace': evidence(row),
                })
            result[kind] = {'rows': sorted(normalized, key=lambda row: row['date']),
                            'status': status if normalized else 'no_data'}
        return result
    if endpoint == 'short-selling':
        normalized = []
        for row in rows:
            ratio = numeric(row.get('shortSellingVolumeRate'))
            value_ratio = numeric(row.get('shortSellingAmountRate'))
            normalized.append({
                'date': row['date'], 'volume': numeric(row.get('shortSellingVolume')),
                'value': numeric(row.get('shortSellingAmount')),
                'ratio': ratio * 100 if ratio is not None else None,
                'value_ratio': value_ratio * 100 if value_ratio is not None else None,
                'total_volume': None, 'total_value': None,
                'source_trace': {**evidence(row), 'unit': '주/원'},
            })
        return {'trades': {'rows': sorted(normalized, key=lambda row: row['date']),
                           'status': status if normalized else 'no_data'}}
    raise ValueError('Unsupported trading projection')


def _source(store, key):
    value = store.metadata(key, {})
    reference = value.get('_series_ref')
    if reference is not None and reference != key:
        raise TradingProjectionUnavailable('Trading source identity mismatch')
    if reference and (not isinstance(value.get('row_count'), int)
                      or value['row_count'] < 0):
        raise TradingProjectionUnavailable('Trading source range unavailable')
    if reference:
        # Two PK-edge lookups catch removed/truncated archives without COUNT(*)
        # over the history. Interior deletion is not certified by this check.
        table = store.time_series.table
        with store.read_connection() as db:
            query = select(table.c.time).where(table.c.series == key)
            first = db.execute(query.order_by(table.c.time).limit(1)).scalar_one_or_none()
            last = db.execute(query.order_by(table.c.time.desc()).limit(1)).scalar_one_or_none()
        if ((first, last) != (value.get('first_time'), value.get('last_time'))
                or bool(value['row_count']) != bool(first)):
            raise TradingProjectionUnavailable('Trading source bounds mismatch')
    return value


def _last_eligible(store, key, source, field=None):
    if not source.get('_series_ref'):
        rows = [row for row in source.get('rows', []) if not field or row.get(field)]
        return max((row['date'] for row in rows), default=None)
    if not field:
        return source.get('last_time')
    # One indexed descending query returns the section's actual last observation,
    # not the other credit section's date. Decode only the selected time value.
    if field not in ('marginLoan', 'stockLoan'):
        raise ValueError('Unknown financing section')
    table = store.time_series.table
    if store.engine.dialect.name == 'sqlite':
        body = ("CASE WHEN json_extract(payload,'$._market_storage')="
                "'source-trace-sha256-v1' THEN json_extract(payload,'$.row') ELSE payload END")
        present = (f"json_type(({body}),'$.{field}')='object' "
                   f"AND json_extract(({body}),'$.{field}')!='{{}}'")
    else:
        body = ("CASE WHEN payload::jsonb->>'_market_storage'='source-trace-sha256-v1' "
                "THEN payload::jsonb->'row' ELSE payload::jsonb END")
        present = (f"jsonb_typeof(({body})->'{field}')='object' "
                   f"AND ({body})->'{field}'!='{{}}'::jsonb")
    query = (select(table.c.time).where(table.c.series == key, text(present))
             .order_by(table.c.time.desc()).limit(1))
    with store.read_connection() as db:
        return db.execute(query).scalar_one_or_none()


def _rows(store, key, source, start=None, end=None, limit=None):
    if source.get('_series_ref'):
        return store.time_series.rows(key, start=start, end=end, limit=limit,
                                      reverse=limit is not None)
    rows = sorted((row for row in source.get('rows', [])
                   if (not start or row['date'] >= start) and (not end or row['date'] <= end)),
                  key=lambda row: row['date'], reverse=limit is not None)
    return rows[:limit] if limit is not None else rows


def _stable_read(store, keys, build):
    # Store/time-series reads may use separate pool connections. A revision
    # sandwich rejects a mixed observation without a long-lived DB transaction.
    for _ in range(2):
        before = store.revisions(keys)
        try:
            result = build()
        except SourceTraceIntegrityError as exc:
            raise TradingProjectionUnavailable('Trading evidence unavailable') from exc
        except TradingProjectionUnavailable:
            if store.revisions(keys) != before:
                continue
            raise
        if store.revisions(keys) == before:
            return result
    raise TradingProjectionUnavailable('Trading observations changed during read')


def _legacy(store, keys):
    values = [store.metadata(key, {}) for key in keys]
    return values


def _check_references(store, code, keys, available):
    """Fetch only small reference objects, not full legacy output arrays."""
    from .toss import TRADING_PROJECTION_FIELDS
    allowed = {}
    for endpoint, mapping in TRADING_PROJECTION_FIELDS.items():
        for prefix, fields in mapping.items():
            allowed.setdefault(prefix + code, {}).update({
                field: f'toss:{endpoint}:{code}' for field in fields})
    expression = ("json_extract(payload,'$._trading_projection')"
                  if store.engine.dialect.name == 'sqlite'
                  else "payload::jsonb->'_trading_projection'")
    with store.read_connection() as db:
        references = [(key, db.execute(text(
            f'SELECT {expression} FROM market_snapshots WHERE key=:key'),
            {'key': key}).scalar_one_or_none()) for key in keys]
    for key, reference in references:
        if isinstance(reference, str):
            try:
                reference = json.loads(reference)
            except ValueError as exc:
                raise TradingProjectionUnavailable('Invalid trading reference encoding') from exc
        if reference is None:
            continue
        if not isinstance(reference, dict) or reference.get('format') != FORMAT:
            raise TradingProjectionUnavailable('Unknown trading reference format')
        fields = reference.get('fields')
        if not isinstance(fields, dict) or not fields:
            raise TradingProjectionUnavailable('Trading reference fields missing')
        for field, item in fields.items():
            expected_source = allowed.get(key, {}).get(field)
            if (not isinstance(item, dict) or item.get('source_key') != expected_source
                    or item.get('normalizer') != FORMAT
                    or not isinstance(item.get('series_rows'), int)
                    or item['series_rows'] < 0 or not item.get('source_revision')):
                raise TradingProjectionUnavailable('Trading reference identity mismatch')
            if item['source_key'] not in available:
                raise TradingProjectionUnavailable('Stored trading source is missing')
            source = store.metadata(item['source_key'], {})
            if not _range_retained(source, item):
                raise TradingProjectionUnavailable('Stored trading range regressed')


def _range_retained(source, reference):
    return not (
        source.get('row_count', len(source.get('rows', []))) < reference.get('series_rows', 0)
        or (reference.get('source_first_time') and source.get('first_time')
            and source['first_time'] > reference['source_first_time'])
        or (reference.get('source_last_time') and source.get('last_time')
            and source['last_time'] < reference['source_last_time']))


def _field_source(payload, field):
    return payload.get(REFERENCE, {}).get('fields', {}).get(field, {}).get('source_key')


def _merge_rows(primary, fallback, *, reverse=False):
    # Keep established normalized precedence on the same date, but never drop
    # dates retained by only one mirror after a partial or independent write.
    indexed = {row['date']: row for row in fallback}
    indexed.update({row['date']: row for row in primary})
    return sorted(indexed.values(), key=lambda row: row['date'], reverse=reverse)


def _section(primary, fallback, field):
    """Choose per section; a partial mirror must not hide the other history."""
    if _field_source(primary, field):
        return primary
    if _field_source(fallback, field):
        return fallback
    first, second = primary.get(field, {}), fallback.get(field, {})
    section = first if first.get('rows') else second or first
    return {field: {**section, 'rows': _merge_rows(first.get('rows', []), second.get('rows', []))}}


def _retained_rows(payload, field):
    value = payload.get(field, [])
    rows = value if isinstance(value, list) else value.get('rows', [])
    if not _field_source(payload, field):
        return rows
    # A later independent provider writer may add rows to a compact snapshot.
    # Toss summary rows are reconstructible; those other rows are not.
    return [row for row in rows if row.get('source_trace', {}).get('source') != 'toss']


def read_flows(store, code):
    keys = dependencies(code, 'flows')
    def build():
        source = store.metadata(keys[0], {})
        available = {keys[0]} if source.get('_series_ref') or source.get('rows') else set()
        _check_references(store, code, keys[1:], available)
        normalized, detail = _legacy(store, keys[1:])
        chosen, field = (normalized, 'rows') if (
            _field_source(normalized, 'rows') or normalized.get('rows')) else (detail, 'flows')
        if _field_source(chosen, field):
            source = _source(store, keys[0])
            rows = normalize_rows('investor-trading', _rows(store, keys[0], source),
                                  source, source.get('status', 'pending'))['rows']
            retained = _merge_rows(_retained_rows(normalized, 'rows'),
                                   _retained_rows(detail, 'flows'), reverse=True)
            return _merge_rows(rows, retained, reverse=True)
        return _merge_rows(normalized.get('rows', []), detail.get('flows', []), reverse=True)
    return _stable_read(store, keys, build)


def read_financing(store, code, period):
    from .financing import public_financing
    if period != 'all' and period not in PERIODS:
        raise ValueError('Invalid financing period')
    keys = dependencies(code, 'financing')
    def build():
        sources = {key: store.metadata(key, {}) for key in keys[:2]}
        available = {key for key, value in sources.items()
                     if value.get('_series_ref') or value.get('rows')}
        _check_references(store, code, keys[2:], available)
        normalized, fallback = _legacy(store, keys[2:])
        result = {'code': normalized.get('code') or fallback.get('code', code)}
        for kind, (endpoint, field) in FINANCING.items():
            key = f'toss:{endpoint}:{code}'
            chosen = _section(normalized, fallback, kind)
            if not _field_source(chosen, kind):
                result[kind] = chosen.get(kind, {})
                continue
            source = _source(store, key)
            last = _last_eligible(store, key, source, field)
            rows = _rows(store, key, source, _cutoff(last, period), last) if last else []
            result[kind] = normalize_rows(endpoint, rows, source,
                                          source.get('status', 'pending'))[kind]
            retained = _merge_rows(_retained_rows(normalized, kind), _retained_rows(fallback, kind))
            result[kind]['rows'] = _merge_rows(result[kind]['rows'], retained)
        return public_financing(result, period)
    return _stable_read(store, keys, build)


def read_short_selling(store, code, period):
    from .short_selling import public_short_selling
    if period != 'all' and period not in PERIODS:
        raise ValueError('Invalid short selling period')
    keys = dependencies(code, 'short-selling')
    def build():
        source = store.metadata(keys[0], {})
        normalized, legacy = _legacy(store, keys[1:])
        available = {keys[0]} if source.get('_series_ref') or source.get('rows') else set()
        _check_references(store, code, keys[1:], available)
        result = {**legacy, **normalized, 'code': legacy.get('code', code)}
        chosen = _section(normalized, legacy, 'trades')
        result['trades'] = chosen.get('trades', {})
        if _field_source(chosen, 'trades'):
            source = _source(store, keys[0])
            last = _last_eligible(store, keys[0], source)
            rows = _rows(store, keys[0], source, _cutoff(last, period), last) if last else []
            result.update(normalize_rows('short-selling', rows, source,
                                         source.get('status', 'pending')))
            retained = _merge_rows(_retained_rows(normalized, 'trades'),
                                   _retained_rows(legacy, 'trades'))
            result['trades']['rows'] = _merge_rows(result['trades']['rows'], retained)
        return public_short_selling(result, period)
    return _stable_read(store, keys, build)


def _owned(endpoint, code):
    from .toss import TRADING_PROJECTION_FIELDS
    return {prefix + code: fields
            for prefix, fields in TRADING_PROJECTION_FIELDS[endpoint].items()}


def _reference_valid(payload, source_key, owned):
    reference = payload.get(REFERENCE, {})
    return reference.get('format') == FORMAT and all(
        reference.get('fields', {}).get(field, {}).get('source_key') == source_key
        and reference.get('fields', {}).get(field, {}).get('normalizer') == FORMAT
        for field in owned)


def _has_other_source(payload, owned):
    """Another provider's observations are not disposable Toss projections."""
    for field in owned:
        value = payload.get(field)
        rows = value if isinstance(value, list) else (
            value.get('rows', []) if isinstance(value, dict) else [])
        if any(row.get('source_trace', {}).get('source') not in (None, 'toss')
               for row in rows):
            return True
    return False


def _previous_projection_can_advance(marker, snapshots, owned, full, source):
    """Prove a prior v2 Toss output is retained by the newer source revision.

    No absent dates may disappear. Changed values require a different observed
    raw document; an arbitrary hand-written legacy value is never approved.
    """
    from .toss import projection_field_hash
    prior = marker.get('input', {})
    if (prior.get('version') != 2 or not prior.get('revision')
            or not isinstance(prior.get('rows'), int) or prior['rows'] <= 0
            or prior['rows'] > source['row_count']):
        return False
    for key, fields in owned.items():
        existing = snapshots[key]
        previous_hash = marker.get('output_field_hashes', {}).get(key)
        if projection_field_hash(existing, fields) != previous_hash or not previous_hash:
            return False
        expected = {'flows': full['rows']} if key.startswith('detail:') else full
        for field in fields:
            old, new = existing[field], expected[field]
            if field == 'status':
                continue
            old_rows = old if isinstance(old, list) else old.get('rows', [])
            new_rows = new if isinstance(new, list) else new.get('rows', [])
            indexed = {row['date']: row for row in new_rows}
            for row in old_rows:
                replacement = indexed.get(row['date'])
                if replacement is None:
                    return False
                old_trace = row.get('source_trace', {})
                new_trace = replacement.get('source_trace', {})
                old_doc = old_trace.get('source_document_id')
                new_doc = new_trace.get('source_document_id')
                if any(trace.get('source') != 'toss' for trace in (old_trace, new_trace)):
                    return False
                if any(old_trace.get(field) != new_trace.get(field)
                       for field in ('scope', 'unit', 'currency')):
                    return False
                if any(not isinstance(doc, str) or len(doc) != 64
                       or any(c not in '0123456789abcdef' for c in doc)
                       for doc in (old_doc, new_doc)):
                    return False
                old_values = {k: v for k, v in row.items() if k != 'source_trace'}
                new_values = {k: v for k, v in replacement.items() if k != 'source_trace'}
                if old_values != new_values and old_doc == new_doc:
                    return False
    return True


def _summary(store, code, endpoint, source):
    key = f'toss:{endpoint}:{code}'
    if endpoint in ('credit-trades', 'securities-lending'):
        result = {}
        for kind, (owner, field) in FINANCING.items():
            if owner != endpoint:
                continue
            last = _last_eligible(store, key, source, field)
            rows = _rows(store, key, source, start=last, end=last) if last else []
            result[kind] = normalize_rows(endpoint, rows, source, source['status'])[kind]
        return result
    rows = _rows(store, key, source, limit=1)
    return normalize_rows(endpoint, rows, source, source['status'])


class _CompactionChanged(RuntimeError):
    pass


def _reserve_absent(store, db, keys, before):
    """Reserve new output keys; a concurrent insertion must abort, not overwrite."""
    from .store import now
    if store.engine.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    for key in sorted(key for key in keys if key not in before):
        statement = insert(store.snapshots).values(key=key, payload='{}', updated_at=now())
        inserted = db.execute(statement.on_conflict_do_nothing(
            index_elements=[store.snapshots.c.key]).returning(store.snapshots.c.key)).first()
        if not inserted:
            raise _CompactionChanged()


def compact_projection(store, code, endpoint, state):
    """Opt-in writer: compact exact legacy projections, never unmatched data.

    Return True when handled/deferred; False only for unsupported endpoints.
    Parent enables the flag only after every reader/rollback target supports it.
    """
    from .store import now
    from .toss import projection_field_hash
    if endpoint == 'program-trades':
        return False  # Already stores one current summary, not full history.
    source_key = f'toss:{endpoint}:{code}'
    owned = _owned(endpoint, code)
    keys = (source_key, *owned)
    before = store.revisions(keys)
    source = _source(store, source_key)
    marker_key = f'toss-projection:{endpoint}:{code}'
    marker = store.metadata(marker_key, {})
    audit_key = f'toss-compaction:{endpoint}:{code}'
    attempt = store.metadata(audit_key, {})
    signature = {'version': 3, 'normalizer': FORMAT,
                 'revision': source.get('series_revision'), 'rows': source.get('row_count'),
                 'status': state.get('status')}
    if not source.get('_series_ref') or not source.get('series_revision'):
        # Do not convert unverified legacy or clear retained last-good values.
        store.put(audit_key, {'status': 'source_unavailable', 'input': signature,
                             'checked_at': now()})
        return True
    output_revisions = {key: before.get(key) for key in owned}
    if (attempt.get('status') == 'legacy_preserved' and attempt.get('input') == signature
            and attempt.get('output_revisions') == output_revisions):
        return True
    snapshots = {key: store.metadata(key, {}) for key in owned}
    for key, fields in owned.items():
        for field in fields:
            reference = snapshots[key].get(REFERENCE, {}).get('fields', {}).get(field, {})
            if reference.get('source_key') == source_key and not _range_retained(source, reference):
                raise TradingProjectionUnavailable('Trading source cannot replace retained range')
    valid = all(_reference_valid(snapshots[key], source_key, fields)
                and not _has_other_source(snapshots[key], fields)
                for key, fields in owned.items())
    current_hashes = {key: projection_field_hash(snapshots[key], fields)
                      for key, fields in owned.items()}
    if valid and marker.get('input') == signature and (
            current_hashes == marker.get('output_field_hashes')):
        if store.revisions(keys) == before:
            store.put(marker_key, {**marker, 'skipped': marker.get('skipped', 0) + 1,
                                  'checked_at': now(),
                                  'outputs': {key: before[key] for key in owned}})
        return True
    verification = attempt.get('verification')
    if not valid:
        rows = _rows(store, source_key, source)
        if len(rows) != source['row_count']:
            raise TradingProjectionUnavailable('Trading source row count mismatch')
        full = normalize_rows(endpoint, rows, source, state['status'])
        prior_verified = _previous_projection_can_advance(marker, snapshots, owned, full, source)
        verified_hashes = {}
        for key, fields in owned.items():
            expected = {'flows': full['rows']} if key.startswith('detail:') else full
            existing = snapshots[key]
            present = [field for field in fields if field in existing]
            if present and projection_field_hash(existing, present) != (
                    projection_field_hash(expected, present)) and not prior_verified:
                # Exact row/value/evidence equality is required before shortening.
                if store.revisions(keys) == before:
                    store.put(audit_key, {'status': 'legacy_preserved',
                                         'reason': 'owned_fields_not_equivalent',
                                         'input': signature, 'output_revisions': output_revisions,
                                         'checked_at': now()})
                return True
            verified_hashes[key] = projection_field_hash(existing, present) if present else None
        verification = {'method': ('previous-toss-projection-retained-v1' if prior_verified
                                   else 'full-owned-field-equality-v1'),
                        'input': signature, 'source_rows_compared': len(rows),
                        'legacy_field_hashes': verified_hashes,
                        'prior_projection': marker.get('input') if prior_verified else None}
    summary = _summary(store, code, endpoint, {**source, 'status': state['status']})
    outputs = {}
    for key, fields in owned.items():
        updates = {'flows': summary['rows']} if key.startswith('detail:') else summary
        payload = dict(snapshots[key])
        payload.update({field: updates[field] for field in fields})
        if key.startswith(('financing:', 'short-selling:')):
            payload.setdefault('code', code)
        previous_refs = payload.get(REFERENCE, {}).get('fields', {})
        ref = {'source_key': source_key, 'source_revision': source['series_revision'],
               'series_rows': source['row_count'], 'source_first_time': source.get('first_time'),
               'source_last_time': source.get('last_time'), 'normalizer': FORMAT}
        payload[REFERENCE] = {'format': FORMAT, 'fields': {
            **previous_refs, **{field: ref for field in fields}}}
        outputs[key] = payload
    changed = []
    # Build/compare large values before locking. Recheck exact revisions and lock
    # in one order before atomically shortening both mirrors and recording proof.
    try:
        with store.lock, store.engine.begin() as db:
            query = (select(store.snapshots.c.key, store.snapshots.c.updated_at)
                     .where(store.snapshots.c.key.in_(keys)).order_by(store.snapshots.c.key))
            if store.engine.dialect.name == 'postgresql':
                query = query.with_for_update()
            if dict(db.execute(query).all()) != before:
                return True
            _reserve_absent(store, db, owned, before)
            for key, payload in outputs.items():
                if store.put(key, payload, _connection=db):
                    changed.append(key)
            revision_query = select(store.snapshots.c.key, store.snapshots.c.updated_at).where(
                store.snapshots.c.key.in_(tuple(owned)))
            revisions = dict(db.execute(revision_query).all())
            marker = {'input': signature, 'compact': True, 'outputs': revisions,
                      'output_field_hashes': {key: projection_field_hash(outputs[key], fields)
                                              for key, fields in owned.items()},
                      'rebuilt': marker.get('rebuilt', 0) + 1, 'skipped': marker.get('skipped', 0),
                      'checked_at': now()}
            store.put(marker_key, marker, _connection=db)
            changed.append(marker_key)
            store.put(audit_key, {'status': 'compacted', 'input': signature,
                                 'verification': verification,
                                 'checked_at': now()}, _connection=db)
            changed.append(audit_key)
    except _CompactionChanged:
        return True
    for key in changed:
        store.notify_changed(key)
    return True
