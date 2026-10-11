"""Search identities and ranking facts, without decoding every company's details."""

import json
import threading
from collections import OrderedDict

from sqlalchemy import String, case, cast, column, func, literal, select, text, values
from sqlalchemy.dialects.postgresql import JSONB

from .models import Query, apply_query, matches_security
from .quote_metrics import select_daily_measures

# Keep the same merge, matching and same-trading-day turnover rules as the list.
# Financial statements and provenance are hydrated only for the selected results.
PATHS = (('code',), ('name',), ('aliases',), ('market',), ('sector',), ('value',),
         ('as_of',), ('facts', 'value', 'value'), ('facts', 'value', 'period'),
         ('daily_facts', 'value', 'value'), ('daily_facts', 'value', 'period'),
         ('quote_trace', 'source'))
_lock = threading.Lock()
_cache = OrderedDict()


def _light_map(store, key, revision):
    identity = (store, key)
    with _lock:
        cached = _cache.get(identity)
        if revision is not None and cached and cached[0] == revision:
            _cache.move_to_end(identity)
            return cached[1]
        sqlite = store.engine.dialect.name == 'sqlite'
        entries = ("SELECT e.key AS code, e.value AS value, 0 AS priority "
                   "FROM market_snapshots s JOIN json_each(s.payload) e ON true "
                   "WHERE s.key = :key" if sqlite else
                   "SELECT e.key AS code, e.value AS value, 0 AS priority "
                   "FROM market_snapshots s CROSS JOIN LATERAL "
                   "jsonb_each(s.payload::jsonb) e WHERE s.key = :key")
        if key == 'quotes':
            value = 'payload' if sqlite else 'payload::jsonb'
            entries += (f" UNION ALL SELECT substr(key, 7), {value}, 1 "
                        "FROM market_snapshots WHERE key LIKE 'quote:%'")
        # Include an empty sentinel so even an empty/deleted source has a head
        # observation in this very SELECT, without a second revision query.
        empty = "'{}'" if sqlite else "'{}'::jsonb"
        entries += f' UNION ALL SELECT NULL, {empty}, -1'
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
        columns.extend(("(SELECT updated_at FROM heads WHERE key = :key)",
                        "(SELECT updated_at FROM heads WHERE key = 'quote-book')"))
        statement = text("WITH heads AS (SELECT key, updated_at FROM market_snapshots "
                         "WHERE key IN (:key, 'quote-book')), entries AS (" + entries
                         + ') SELECT ' + ', '.join(columns)
                         + ' FROM entries ORDER BY priority')
        with store.read_connection() as db:
            records = db.execute(statement, {'key': key}).all()
        values = {}
        for record in records:
            if record[0] is None:
                continue
            item = {}
            for index, path in enumerate(PATHS):
                value, kind = record[1 + index * 2:3 + index * 2]
                if kind is None:
                    continue
                if sqlite and kind in ('array', 'object'):
                    value = json.loads(value)
                target = item
                for part in path[:-1]:
                    target = target.setdefault(part, {})
                target[path[-1]] = value
            values[record[0]] = item
        observed_revision = records[0][-2]
        if key == 'quotes':
            from .quote_store import book_revision
            observed_revision = book_revision(observed_revision, records[0][-1])
        # Attach only the source heads observed with the actual projected values.
        if observed_revision is not None:
            _cache[identity] = (observed_revision, values)
            _cache.move_to_end(identity)
            while len(_cache) > 4:
                _cache.popitem(last=False)
        return values


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


def selected_inputs(store, codes):
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
    query = select(requested.c.code, detail_value, quote_value).select_from(
        requested.outerjoin(source, source.c.key == 'enriched')
        .outerjoin(state, state.c.key == STATE)
        .outerjoin(detail, detail.c.key == literal(PREFIX) + requested.c.code)
        .outerjoin(legacy, legacy.c.key == 'quotes')
        .outerjoin(quote, quote.c.key == literal('quote:') + requested.c.code))
    with store.read_connection() as db:
        records = db.execute(query).all()
    details, quotes = {}, {}
    for code, detail_raw, quote_raw in records:
        details[code] = json.loads(detail_raw) if isinstance(detail_raw, str) else detail_raw or {}
        quotes[code] = json.loads(quote_raw) if isinstance(quote_raw, str) else quote_raw or {}
    return details, quotes
