"""Exact final-order adapter for the certified owned PG17 default-C target.

Frozen verifier SQL and canonical/hash code remain unchanged. Only the three
fixed restored-target ordering statements are translated at their delivery
boundary, after catalog and actual unforced EXPLAIN checks. No server, process,
database connection or verification starts by importing this module.
"""

from __future__ import annotations

from contextlib import contextmanager

from . import resume_restore as resume

ROWS_ORDER = ('SELECT series,time,digest,payload FROM market_compact_restore.rows '
              'WHERE series=%s ORDER BY time COLLATE "C"')
TRACES_ORDER = ('SELECT digest,byte_count,payload,original FROM market_compact_restore.traces '
                'ORDER BY digest COLLATE "C"')
ORIGINAL_ORDER = ('SELECT digest FROM market_compact_restore.traces '
                  'WHERE original ORDER BY digest COLLATE "C"')
ORDER_QUERIES = {
    ROWS_ORDER: ROWS_ORDER.removesuffix(' COLLATE "C"'),
    TRACES_ORDER: TRACES_ORDER.removesuffix(' COLLATE "C"'),
    ORIGINAL_ORDER: ORIGINAL_ORDER.removesuffix(' COLLATE "C"'),
}
ORDER_KINDS = {
    ROWS_ORDER: ('rows', 'verify_rows'),
    TRACES_ORDER: ('traces', 'verify_traces'),
    ORIGINAL_ORDER: ('traces', 'original_trace_keys'),
}
SNAPSHOT_SETTINGS = (
    "SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation')"
)


def final_index_plan(plan, table, index, *, condition=False, original=False):
    """Require the actual whole ordering SELECT to use its certified PK directly."""
    if not isinstance(plan, list) or len(plan) != 1 or not isinstance(plan[0], dict):
        raise resume.local.Refused('final_order_index_plan_invalid')
    leaf = plan[0].get('Plan')
    if (not isinstance(leaf, dict)
            or leaf.get('Node Type') not in ('Index Scan', 'Index Only Scan')
            or leaf.get('Schema') != 'market_compact_restore'
            or leaf.get('Relation Name') != table or leaf.get('Index Name') != index
            or leaf.get('Scan Direction') != 'Forward' or leaf.get('Plans')
            or (condition and not leaf.get('Index Cond'))
            or (not original and leaf.get('Filter'))):
        raise resume.local.Refused('final_order_index_plan_required')
    # The exact WHERE original statement may have a residual original filter.
    # Its fixed SQL, not a hand-written plan filter, defines the selected rows.
    return leaf['Node Type']


class _OrderingCursor:
    def __init__(self, owner, cursor, name):
        self.owner, self.cursor, self.name = owner, cursor, name

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def execute(self, statement, parameters=None, **kwargs):
        translated = self.owner.order_statement(statement, parameters, name=self.name,
                                                  kwargs=kwargs)
        self.cursor.execute(translated, parameters, **kwargs)
        return self


class FinalOrderConnection:
    """Explicit wrapper of IndexedCountConnection; no global monkeypatch.

The frozen final verifier's managed transaction establishes a fresh catalog
lease. Every translated ordering SELECT gets an EXPLAIN with its real bound
parameters, without ANALYZE, LIMIT, planner forcing or payload reads. Its
canonical byte-order validation and final proof are performed by the original
verifier, never this adapter.
"""

    def __init__(self, db):
        self.db = db
        self.final_certified = False
        self.managed_depth = 0
        self.indexes = {}
        self.fresh_evidence = True

    def __getattr__(self, name):
        return getattr(self.db, name)

    @contextmanager
    def transaction(self, *args, **kwargs):
        with self.db.transaction(*args, **kwargs) as transaction:
            outer = self.managed_depth == 0
            if outer:
                self.final_certified = False
                self.indexes.clear()
                self.fresh_evidence = True
            self.managed_depth += 1
            try:
                yield transaction
            finally:
                self.managed_depth -= 1
                if outer:
                    self.final_certified = False
                    self.indexes.clear()

    def _snapshot(self):
        self.db.budget.check()
        if (self.managed_depth < 1 or self.db.info.transaction_status.name != 'INTRANS'
                or self.db.info.encoding.lower().replace('-', '') != 'utf8'):
            raise resume.local.Refused('final_order_owned_snapshot_required')
        settings = self.db._read(SNAPSHOT_SETTINGS).fetchone()
        if settings not in (('on', 'repeatable read'), ('on', 'serializable')):
            raise resume.local.Refused('final_order_readonly_snapshot_required')

    def _catalogue(self):
        if self.final_certified:
            return
        # Revalidate the owned target and its exact default-C key/index contract
        # in this final snapshot, even if another transaction certified counts.
        self.db.certify()
        plans = self.db.report['count_index_contract']['plans']
        indexes = {entry['table']: entry['index'] for entry in plans}
        if set(indexes) != {'rows', 'traces'}:
            raise resume.local.Refused('final_order_key_contract_required')
        self.indexes = indexes
        self.final_certified = True

    def order_statement(self, statement, parameters=None, *, name=None, kwargs=None):
        if not isinstance(statement, str) or statement not in ORDER_QUERIES:
            return statement
        table, kind = ORDER_KINDS[statement]
        if self.fresh_evidence:
            self.db.report['final_order_index_contract'] = {
                'state': 'checking', 'adapter': 'owned-default-c-final-order-v1',
                'plans': [], 'ordering_checks': 0,
                'source_references_verified': False, 'ordered_hash_verified': False,
                'restore_verified': False,
            }
            self.fresh_evidence = False
        evidence = self.db.report['final_order_index_contract']
        try:
            if kwargs or (name is not None and name != kind):
                raise resume.local.Refused('final_order_parameters_invalid')
            if statement == ROWS_ORDER:
                if (not isinstance(parameters, tuple) or len(parameters) != 1
                        or not isinstance(parameters[0], str) or not 0 < len(parameters[0]) <= 128):
                    raise resume.local.Refused('final_order_parameters_invalid')
            elif parameters is not None and parameters != ():
                raise resume.local.Refused('final_order_parameters_invalid')
            self._snapshot()
            self._catalogue()
            translated = ORDER_QUERIES[statement]
            # Do not reuse a generic prepared EXPLAIN plan from another series.
            timeout = min(30000, self.db.budget.check())
            self.db.execute("SELECT set_config('statement_timeout',%s,true)", (str(timeout),))
            plan = self.db.execute('EXPLAIN (FORMAT JSON, VERBOSE) ' + translated,
                                   parameters, prepare=False).fetchone()[0]
            self.db.budget.check()
            node = final_index_plan(plan, table, self.indexes[table],
                                    condition=statement == ROWS_ORDER,
                                    original=statement == ORIGINAL_ORDER)
            # Retain at most one summary per fixed kind, never parameter values
            # or provider data. Every real per-series plan is still checked.
            evidence['plans'] = [row for row in evidence['plans'] if row['kind'] != kind]
            evidence['plans'].append({'kind': kind, 'table': table, 'node': node,
                                      'index': self.indexes[table]})
            evidence['ordering_checks'] += 1
            evidence.update(state='verified', pg_major=17, encoding='UTF8', collation='C',
                            all_ordering_kinds_checked=len(evidence['plans']) == 3)
            self.db.budget.check()
            return translated
        except BaseException:
            evidence['state'] = 'refused'
            self.final_certified = False
            self.indexes.clear()
            raise

    def execute(self, statement, parameters=None, **kwargs):
        translated = self.order_statement(statement, parameters, kwargs=kwargs)
        if isinstance(statement, str) and statement in ORDER_QUERIES:
            return self.db.execute(translated, parameters, prepare=False)
        return self.db.execute(translated, parameters, **kwargs)

    def cursor(self, *args, **kwargs):
        cursor = self.db.cursor(*args, **kwargs)
        name = kwargs.get('name', args[0] if args else None)
        if name in {value[1] for value in ORDER_KINDS.values()}:
            return _OrderingCursor(self, cursor, name)
        return cursor
