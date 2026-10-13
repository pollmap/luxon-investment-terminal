"""Search identities and ranking facts, without decoding every company's details."""

import json
import threading
from collections import OrderedDict
from functools import lru_cache

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
IDENTITY_PATHS = (('code',), ('name',), ('aliases',), ('market',), ('sector',),
                  ('as_of',), ('quote_trace', 'source'))
# Fields consumed by assemble_rows before compact search serialization. Nested
# trace objects stay intact: do not reconstruct or duplicate provenance fields.
COMPACT_PATHS = tuple((key,) for key in (
    'logo_url', 'code', 'name', 'aliases', 'market', 'sector', 'price', 'change',
    'as_of', 'received_at', 'market_status', 'source_trace', 'quote_trace',
    'facts', 'daily_facts',
)) + tuple(('facts', key) for key in ('price', 'change', 'volume', 'value')) + (
    ('daily_facts', 'volume'), ('daily_facts', 'value'),
)
SUMMARY_PATHS = tuple((key,) for key in (
    'code','name','aliases','market','sector','logo_url','price','change','as_of','received_at',
    'market_status','volume','value','cap','foreign','per','pbr','roe','dividend',
    'source_trace','quote_trace','facts','daily_facts','company',
)) + tuple(('facts',key) for key in (
    'price','change','volume','value','cap','foreign','per','pbr','roe','dividend',
)) + (('daily_facts','volume'),('daily_facts','value')) + tuple(
    ('company',key) for key in ('products','sector','listed_at'))
_lock = threading.Lock()
_cache = OrderedDict()
_identity_lock = threading.Lock()
_identity_cache = OrderedDict()
MAX_RANK_CANDIDATES = 512


def _columns(sqlite, paths=PATHS):
    columns = ['code']
    for path in paths:
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


def _quote_delta(store, cached, paths=PATHS):
    """Reuse unchanged rows; abandon the delta if any concurrent writer intervenes."""
    previous = cached[2]
    heads = _quote_heads(store)
    if heads.get('quotes') != previous.get('quotes') or previous.keys() - heads.keys():
        return None
    changed = [key for key, revision in heads.items()
               if key.startswith('quote:') and previous.get(key) != revision]
    # Large bursts use one full projection rather than expanding a huge IN list.
    if len(changed) > (30 if paths == PATHS else MAX_RANK_CANDIDATES):
        return None
    sqlite = store.engine.dialect.name == 'sqlite'
    rows = []
    if changed:
        value = 'payload' if sqlite else 'payload::jsonb'
        materialized = '' if sqlite else 'MATERIALIZED '
        statement = text(
            f"WITH entries AS {materialized}(SELECT substr(key, 7) AS code, {value} AS value "
            "FROM market_snapshots WHERE key IN :keys) SELECT "
            + ', '.join(_columns(sqlite, paths)) + ' FROM entries'
        ).bindparams(bindparam('keys', expanding=True))
        rows = _records(store, statement, {'keys': changed})
    # The heads before and after the selected read must match, including absent
    # and removed rows. A changed global source never labels old values as fresh.
    if _quote_heads(store) != heads:
        return None
    from .quote_store import book_revision
    revision = book_revision(heads.get('quotes'), heads.get('quote-book'))
    projected = dict(cached[1])
    projected.update(_decode(rows, sqlite, paths))
    timings.count('search_delta')
    timings.count('search_delta_rows', len(rows))
    return revision, projected, heads


def _light_map(store, key, revision, paths=PATHS):
    identity = (store, key)
    cache, lock = (_cache, _lock) if paths == PATHS else (_identity_cache, _identity_lock)
    with timings.timed_lock(lock, 'search_lock'):
        cached = cache.get(identity)
        if revision is not None and cached and cached[0] == revision:
            cache.move_to_end(identity)
            return cached[1]
        if key == 'quotes' and cached:
            incremental = _quote_delta(store, cached, paths)
            if incremental is not None:
                cache[identity] = incremental
                cache.move_to_end(identity)
                return incremental[1]
            if paths == PATHS:
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
        columns = _columns(sqlite, paths)
        columns.extend(("(SELECT updated_at FROM heads WHERE key = :key)",
                        "(SELECT updated_at FROM heads WHERE key = 'quote-book')",
                        'item_revision'))
        statement = text("WITH heads AS (SELECT key, updated_at FROM market_snapshots "
                         "WHERE key IN (:key, 'quote-book')), entries AS (" + entries
                         + ') SELECT ' + ', '.join(columns)
                         + ' FROM entries ORDER BY priority')
        records = _records(store, statement, {'key': key})
        if paths == PATHS:
            timings.count('search_full')
        projected = _decode(records, sqlite, paths)
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
            cache[identity] = (observed_revision, projected, heads)
            cache.move_to_end(identity)
            while len(cache) > 4:
                cache.popitem(last=False)
        return projected


def identity_candidates(store, universe, query, revisions):
    """Identify a bounded match set without loading financial or ranking fields.

    Broad/empty queries retain the market-wide ranking contract. Quote aliases
    remain searchable, including legacy writes and stale public quote rejection.
    """
    if not query.strip():
        return None
    from .service_scope import universe_view

    details = _light_map(store, 'enriched', revisions.get('enriched'), IDENTITY_PATHS)
    quotes = _light_map(store, 'quotes', revisions.get('quotes'), IDENTITY_PATHS)
    codes = []
    for base in universe_view(universe)['rows']:
        code = base['code']
        quote = quotes.get(code, {})
        if (quote.get('quote_trace', {}).get('source') not in ('toss', 'toss_websocket')
                and quote.get('as_of') and base.get('as_of')
                and quote['as_of'] < base['as_of']):
            quote = {}
        if matches_security({**base, **details.get(code, {}), **quote}, query):
            codes.append(code)
            if len(codes) > MAX_RANK_CANDIDATES:
                return None
    return tuple(codes)


def candidate_dependencies(store, codes, revisions):
    """Reuse the exact quote heads observed with the identity projection."""
    with _identity_lock:
        cached = _identity_cache.get((store, 'quotes'))
        quote_heads = dict(cached[2]) if cached else {}
    result = {key: revisions[key] for key in ('universe',) if key in revisions}
    # A normal enrichment update in another company must not invalidate this
    # response. Store resolves item heads against the legacy projection state.
    result.update(timings.read_revisions(store, tuple('enriched-item:' + code for code in codes)))
    result['candidate-codes'] = json.dumps(codes, separators=(',', ':'))
    for code in codes:
        key = 'quote:' + code
        # A per-security record overrides the legacy book in selected_inputs.
        # Changes to other per-security records do not invalidate these bytes.
        result[key] = quote_heads.get(key, quote_heads.get('quotes'))
    return result


def candidates(store, universe, query, limit, revisions):
    """Rank all matching identities, then return at most 30 hydration targets."""
    from .service_scope import universe_view

    bases = universe_view(universe)['rows']
    if not bases:
        return {'rows': [], 'total': 0}
    details = _light_map(store, 'enriched', revisions.get('enriched'))
    quotes = _light_map(store, 'quotes', revisions.get('quotes'))
    return _rank_identities(bases, details, quotes, query, limit)


def _rank_identities(bases, details, quotes, query, limit):
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
    return _selected_inputs(store, codes, compact=compact)


def summary_inputs(store, code):
    """The summary does not decode lifetime company history on every price tick."""
    return _selected_inputs(store, (code,), compact=True, paths=SUMMARY_PATHS)


def rank_candidates(store, identities, codes, query, limit):
    """Read ordering facts only for matches; hydrate only the eventual top 30."""
    details, quotes = _selected_inputs(store, codes, compact=True, paths=PATHS,
                                       maximum=MAX_RANK_CANDIDATES)
    return _rank_identities([identities[code] for code in codes], details, quotes, query, limit)


def _selected_inputs(store, codes, *, compact=False, paths=COMPACT_PATHS, maximum=30):
    from .enriched_view import PREFIX, STATE

    codes = tuple(dict.fromkeys(codes))
    if not codes:
        return {}, {}
    if len(codes) > maximum:
        raise ValueError(f'Search projection is limited to {maximum} securities')
    if compact:
        sqlite = store.engine.dialect.name == 'sqlite'
        params = {'codes':json.dumps(codes) if sqlite else list(codes)}
        records = _records(store, _compact_statement(sqlite, paths), params)
        width = len(paths) * 2
        return (_decode([(row[0], *row[1:1+width]) for row in records],sqlite,paths),
                _decode([(row[0], *row[1+width:]) for row in records],sqlite,paths))
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
    records = _records(store, query)
    details, quotes = {}, {}
    with timings.stage('search_decode'):
        for code, detail_raw, quote_raw in records:
            details[code] = (json.loads(detail_raw) if isinstance(detail_raw, str)
                             else detail_raw or {})
            quotes[code] = json.loads(quote_raw) if isinstance(quote_raw, str) else quote_raw or {}
    return details, quotes


@lru_cache(maxsize=8)
def _compact_statement(sqlite, paths):
    """Reuse a fixed bound query, avoiding hundreds of expression compilations.

    Paths are internal constants. Only the code array is supplied by callers;
    legacy JSON/projection validity is chosen once per requested company.
    """
    from .enriched_view import PREFIX, STATE
    requested = ('SELECT value AS code FROM json_each(:codes)' if sqlite else
                 'SELECT unnest(CAST(:codes AS text[])) AS code')
    generation = ("json_extract(state.payload, '$.source_revision')" if sqlite else
                  "state.payload::jsonb ->> 'source_revision'")
    detail = 'detail.payload' if sqlite else 'detail.payload::jsonb'
    quote = 'quote.payload' if sqlite else 'quote.payload::jsonb'
    old_detail = ("json_extract(source.payload, '$.\"' || requested.code || '\"')" if sqlite else
                  'source.payload::jsonb -> requested.code')
    old_quote = ("json_extract(legacy.payload, '$.\"' || requested.code || '\"')" if sqlite else
                 'legacy.payload::jsonb -> requested.code')
    columns = ['code']
    for field in ('detail', 'quote'):
        for path in paths:
            location = '$.' + '.'.join(path) if sqlite else '{' + ','.join(path) + '}'
            value = f"json_extract({field}, '{location}')" if sqlite else f"{field} #> '{location}'"
            kind = f"json_type({field}, '{location}')" if sqlite else f'jsonb_typeof({value})'
            if path in (('facts',), ('daily_facts',), ('company',)):
                empty = "'{}'" if sqlite else "'{}'::jsonb"
                value = f"CASE WHEN {kind} = 'object' THEN {empty} ELSE {value} END"
            columns.extend((value, kind))
    sql = (f'WITH requested AS ({requested}), search_selected AS MATERIALIZED ('
           f'SELECT requested.code, CASE WHEN {generation} = source.updated_at THEN {detail} '
           f'ELSE {old_detail} END AS detail, CASE WHEN quote.key IS NOT NULL THEN {quote} '
           f'ELSE {old_quote} END AS quote FROM requested '
           "LEFT JOIN market_snapshots source ON source.key = 'enriched' "
           f"LEFT JOIN market_snapshots state ON state.key = '{STATE}' "
           f"LEFT JOIN market_snapshots detail ON detail.key = '{PREFIX}' || requested.code "
           "LEFT JOIN market_snapshots legacy ON legacy.key = 'quotes' "
           "LEFT JOIN market_snapshots quote ON quote.key = 'quote:' || requested.code) "
           'SELECT ' + ', '.join(columns) + ' FROM search_selected')
    statement = text(sql)
    return statement if sqlite else statement.bindparams(bindparam('codes',type_=ARRAY(Text)))
