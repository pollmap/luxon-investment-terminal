"""Search identities and ranking facts, without decoding every company's details."""

import json
import threading
from collections import OrderedDict

from sqlalchemy import Text, String, bindparam, case, cast, column, func, literal, select, text, values
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from . import request_timings as timings
from .models import Query, apply_query, matches_security
from .quote_metrics import select_daily_measures

# Keep the same merge, matching and same-trading-day turnover rules as the list.
# Financial statements and provenance are hydrated only for the selected results.
PATHS = (('code',), ('name',), ('aliases',), ('market',), ('sector',), ('value',),
         ('as_of',), ('facts', 'value', 'value'), ('facts', 'value', 'period'),
         ('daily_facts', 'value', 'value'), ('daily_facts', 'value', 'period'),
         ('quote_trace', 'source'))
# Fields consumed by assemble_rows before compact search serialization. Nested
# trace objects stay intact: do not reconstruct or duplicate provenance fields.
COMPACT_PATHS = tuple((key,) for key in (
    'logo_url', 'code', 'name', 'aliases', 'market', 'sector', 'price', 'change',
    'as_of', 'received_at', 'market_status', 'source_trace', 'quote_trace',
    'facts', 'daily_facts',
)) + tuple(('facts', key) for key in ('price', 'change', 'volume', 'value')) + (
    ('daily_facts', 'volume'), ('daily_facts', 'value'),
)
_lock = threading.Lock()
_cache = OrderedDict()


def _columns(sqlite):
    columns = ['code']
    for path in PATHS:
        if sqlite:
            location = '$.' + '.'.join(path)
            columns.extend((f"json_extract(value, '{location}')",
                            f"json_type(value, '{location}')"))
        else:
            location = '{' + ','.join(path) + '}'
            columns.extend((f"value #> '{location}'",
                            f"jsonb_typeof(value #> '{location}')"))
    return columns


def _records(store, statement, params=None):
    with store.read_connection() as db:
        # Execute includes driver/server round trips, not just server CPU time.
        with timings.stage('search_sql'):
            result = db.execute(statement, params or {})
        with timings.stage('search_result'):
            return result.all()


@timings.timed('search_decode')
def _decode(records, sqlite, paths=PATHS):
    projected = {}
    for record in records:
        if record[0] is None:
            continue
        item = {}
        for index, path in enumerate(paths):
            value, kind = record[1 + index * 2:3 + index * 2]
            if kind is None:
                continue
            if sqlite and kind in ('array', 'object'):
                value = json.loads(value)
            elif sqlite and kind in ('true', 'false'):
                value = kind == 'true'
            target = item
            for part in path[:-1]:
                target = target.setdefault(part, {})
            target[path[-1]] = value
        projected[record[0]] = item
    return projected


def _quote_heads(store):
    # Read only small version columns. No legacy JSON or quote payloads cross
    # this boundary, even when every listed company has a live quote.
    return dict(_records(store, text(
        "SELECT key, updated_at FROM market_snapshots "
        "WHERE key IN ('quotes', 'quote-book') OR key LIKE 'quote:%'")))


def _quote_delta(store, cached):
    """Reuse unchanged rows; abandon the delta if any concurrent writer intervenes."""
    previous = cached[2]
    heads = _quote_heads(store)
    if heads.get('quotes') != previous.get('quotes') or previous.keys() - heads.keys():
        return None
    changed = [key for key, revision in heads.items()
               if key.startswith('quote:') and previous.get(key) != revision]
    # Large bursts use one full projection rather than expanding a huge IN list.
    if len(changed) > 30:
        return None
    sqlite = store.engine.dialect.name == 'sqlite'
    rows = []
    if changed:
        value = 'payload' if sqlite else 'payload::jsonb'
        materialized = '' if sqlite else 'MATERIALIZED '
        statement = text(
            f"WITH entries AS {materialized}(SELECT substr(key, 7) AS code, {value} AS value "
            "FROM market_snapshots WHERE key IN :keys) SELECT "
            + ', '.join(_columns(sqlite)) + ' FROM entries'
        ).bindparams(bindparam('keys', expanding=True))
        rows = _records(store, statement, {'keys': changed})
    # The heads before and after the selected read must match, including absent
    # and removed rows. A changed global source never labels old values as fresh.
    if _quote_heads(store) != heads:
        return None
    from .quote_store import book_revision
    revision = book_revision(heads.get('quotes'), heads.get('quote-book'))
    projected = dict(cached[1])
    projected.update(_decode(rows, sqlite))
    timings.count('search_delta')
    timings.count('search_delta_rows', len(rows))
    return revision, projected, heads


def _light_map(store, key, revision):
    identity = (store, key)
    with timings.timed_lock(_lock, 'search_lock'):
        cached = _cache.get(identity)
        if revision is not None and cached and cached[0] == revision:
            _cache.move_to_end(identity)
            return cached[1]
        if key == 'quotes' and cached:
            incremental = _quote_delta(store, cached)
            if incremental is not None:
                _cache[identity] = incremental
                _cache.move_to_end(identity)
                return incremental[1]
            timings.count('search_fallback')
        sqlite = store.engine.dialect.name == 'sqlite'
        entries = ("SELECT e.key AS code, e.value AS value, 0 AS priority, NULL AS item_revision "
                   "FROM market_snapshots s JOIN json_each(s.payload) e ON true "
                   "WHERE s.key = :key" if sqlite else
                   "SELECT e.key AS code, e.value AS value, 0 AS priority, NULL AS item_revision "
                   "FROM market_snapshots s CROSS JOIN LATERAL "
                   "jsonb_each(s.payload::jsonb) e WHERE s.key = :key")
        if key == 'quotes':
            value = 'payload' if sqlite else 'payload::jsonb'
            entries += (f" UNION ALL SELECT substr(key, 7), {value}, 1, updated_at "
                        "FROM market_snapshots WHERE key LIKE 'quote:%'")
        # Include an empty sentinel so even an empty/deleted source has a head
        # observation in this very SELECT, without a second revision query.
        empty = "'{}'" if sqlite else "'{}'::jsonb"
        entries += f' UNION ALL SELECT NULL, {empty}, -1, NULL'
        columns = _columns(sqlite)
        columns.extend(("(SELECT updated_at FROM heads WHERE key = :key)",
                        "(SELECT updated_at FROM heads WHERE key = 'quote-book')",
                        'item_revision'))
        statement = text("WITH heads AS (SELECT key, updated_at FROM market_snapshots "
                         "WHERE key IN (:key, 'quote-book')), entries AS (" + entries
                         + ') SELECT ' + ', '.join(columns)
                         + ' FROM entries ORDER BY priority')
        records = _records(store, statement, {'key': key})
        timings.count('search_full')
        projected = _decode(records, sqlite)
        observed_revision = records[0][-3]
        heads = {}
        if key == 'quotes':
            from .quote_store import book_revision
            heads = {key: value for key, value in (('quotes', observed_revision),
                     ('quote-book', records[0][-2])) if value is not None}
            heads.update({'quote:' + record[0]: record[-1] for record in records
                          if record[-1] is not None})
            observed_revision = book_revision(observed_revision, records[0][-2])
        # Attach only the source heads observed with the actual projected values.
        if observed_revision is not None:
            _cache[identity] = (observed_revision, projected, heads)
            _cache.move_to_end(identity)
            while len(_cache) > 4:
                _cache.popitem(last=False)
        return projected


def candidates(store, universe, query, limit, revisions):
    """Rank all matching identities, then return at most 30 hydration targets."""
    from .service_scope import universe_view

    bases = universe_view(universe)['rows']
    if not bases:
        return {'rows': [], 'total': 0}
    details = _light_map(store, 'enriched', revisions.get('enriched'))
    quotes = _light_map(store, 'quotes', revisions.get('quotes'))
    rows = []
    for base in bases:
        code = base['code']
        detail, quote = details.get(code, {}), quotes.get(code, {})
        primary = quote.get('quote_trace', {}).get('source') in ('toss', 'toss_websocket')
        if (not primary and quote.get('as_of') and base.get('as_of')
                and quote['as_of'] < base['as_of']):
            quote = {}
        identity = {**{key: base[key] for key in ('code', 'name', 'aliases', 'market',
                     'sector', 'value', 'as_of') if key in base}, **detail, **quote}
        if not matches_security(identity, query):
            continue
        baseline = {'value': {'value': base.get('value'), 'period': base.get('as_of')}}
        select_daily_measures(identity, [quote.get('daily_facts', {}),
                              detail.get('facts', {}), base.get('facts', {}), baseline])
        rows.append(identity)
    return apply_query(rows, Query(q=query, limit=min(30, max(1, limit))))


def selected_inputs(store, codes, *, compact=False):
    """Hydrate at most 30 companies in one snapshot, including legacy fallbacks."""
    from .enriched_view import PREFIX, STATE

    codes = tuple(dict.fromkeys(codes))
    if not codes:
        return {}, {}
    if len(codes) > 30:
        raise ValueError('Search hydration is limited to 30 securities')
    requested = values(column('code', String)).data([(code,) for code in codes]).cte('requested')
    source = store.snapshots.alias('enriched_source')
    state = store.snapshots.alias('enriched_state')
    detail = store.snapshots.alias('enriched_item')
    legacy = store.snapshots.alias('legacy_quotes')
    quote = store.snapshots.alias('selected_quote')
    if store.engine.dialect.name == 'sqlite':
        path = func.printf('$."%s"', requested.c.code)
        source_revision = func.json_extract(state.c.payload, '$.source_revision')
        fallback_detail = func.json_extract(source.c.payload, path)
        fallback_quote = func.json_extract(legacy.c.payload, path)
        projected_detail, projected_quote = detail.c.payload, quote.c.payload
    else:
        source_revision = cast(state.c.payload, JSONB)['source_revision'].astext
        fallback_detail = cast(source.c.payload, JSONB).op('->')(requested.c.code)
        fallback_quote = cast(legacy.c.payload, JSONB).op('->')(requested.c.code)
        projected_detail = cast(detail.c.payload, JSONB)
        projected_quote = cast(quote.c.payload, JSONB)
    detail_value = case((source_revision == source.c.updated_at, projected_detail),
                        else_=fallback_detail)
    quote_value = case((quote.c.key.is_not(None), projected_quote), else_=fallback_quote)
    query = select(requested.c.code, detail_value.label('detail'),
                   quote_value.label('quote')).select_from(
        requested.outerjoin(source, source.c.key == 'enriched')
        .outerjoin(state, state.c.key == STATE)
        .outerjoin(detail, detail.c.key == literal(PREFIX) + requested.c.code)
        .outerjoin(legacy, legacy.c.key == 'quotes')
        .outerjoin(quote, quote.c.key == literal('quote:') + requested.c.code))
    if compact:
        selected = query.cte('search_selected')
        if store.engine.dialect.name == 'postgresql':
            # Evaluate the bounded legacy/projection choice once per selected
            # company, not once for every JSON path in the compact projection.
            selected = selected.prefix_with('MATERIALIZED')
        sqlite = store.engine.dialect.name == 'sqlite'
        columns = [selected.c.code]
        for field in (selected.c.detail, selected.c.quote):
            for path in COMPACT_PATHS:
                if sqlite:
                    location = '$.' + '.'.join(path)
                    value = func.json_extract(field, location)
                    kind = func.json_type(field, location)
                    if path in (('facts',), ('daily_facts',)):
                        value = case((kind == 'object', literal('{}')), else_=value)
                    columns.extend((value, kind))
                else:
                    value = field.op('#>')(cast(list(path), ARRAY(Text)))
                    kind = func.jsonb_typeof(value)
                    if path in (('facts',), ('daily_facts',)):
                        value = case((kind == 'object', cast(literal('{}'), JSONB)), else_=value)
                    columns.extend((value, kind))
        records = _records(store, select(*columns))
        width = len(COMPACT_PATHS) * 2
        return (
            _decode([(row[0], *row[1:1 + width]) for row in records], sqlite, COMPACT_PATHS),
            _decode([(row[0], *row[1 + width:]) for row in records], sqlite, COMPACT_PATHS),
        )
    records = _records(store, query)
    details, quotes = {}, {}
    with timings.stage('search_decode'):
        for code, detail_raw, quote_raw in records:
            details[code] = (json.loads(detail_raw) if isinstance(detail_raw, str)
                             else detail_raw or {})
            quotes[code] = json.loads(quote_raw) if isinstance(quote_raw, str) else quote_raw or {}
    return details, quotes
