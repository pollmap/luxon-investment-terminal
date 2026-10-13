"""fixture_non_production: fake catalogs/plans only; no PG, sockets or restore."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_market_compact_verify import FakeDB, NamedCursor, Result, fixtures, verify

from ops.market.compact_restore import indexed_counts as m
from ops.market.compact_restore import indexed_resume as indexed
from ops.market.compact_restore import ordered_verify as frozen


class Budget:
    def __init__(self):
        self.calls = 0
        self.failure = None

    def check(self):
        self.calls += 1
        if self.failure:
            raise self.failure
        return 30000


class NativeCursor(NamedCursor):
    def execute(self, statement, parameters=None, **kwargs):
        assert not kwargs
        if statement in m.ORDER_QUERIES.values():
            original = next(key for key, value in m.ORDER_QUERIES.items() if value == statement)
            # The fixture's existing canonical verifier emulates PG byte order.
            # Adapt only its delivery assertion, not the produced values/hash.
            before = len(self.db.statements)
            if self.name == 'original_trace_keys':
                self.db.statements.append((statement, parameters))
                self.values = [(row[0],) for row in sorted(self.db.traces) if row[3]]
            else:
                super().execute(original, parameters)
                self.db.statements[before] = (statement, parameters)
            return self
        super().execute(statement, parameters)
        return self


class FinalDatabase(FakeDB):
    def __init__(self, original, data):
        super().__init__('restored', original.rows, original.traces)
        self.info.transaction_status.name = 'IDLE'
        self.identity = [indexed.resume.local.DATABASE, '127.0.0.1', indexed.resume.local.PORT,
                         str(data.resolve()), 170011, 'UTF8', 'C', 'C', 'c']
        self.key_indexes = [
            ('rows', 'series', True, True, True, True, True, 2, 'btree', 'rows_pkey'),
            ('rows', 'time', True, True, True, True, True, 2, 'btree', 'rows_pkey'),
            ('traces', 'digest', True, True, True, True, True, 1, 'btree', 'traces_pkey'),
        ]
        self.plan_override = None
        self.transactions = []
        self.plan_preparations = []

    @contextmanager
    def transaction(self, *args, **kwargs):
        assert not args and not kwargs
        self.info.transaction_status.name = 'INTRANS'
        self.transactions.append('begin')
        try:
            yield 'fixture_non_production_transaction'
        except BaseException:
            self.transactions.append('rollback')
            raise
        else:
            self.transactions.append('commit')
        finally:
            self.info.transaction_status.name = 'IDLE'

    def cursor(self, name):
        return NativeCursor(self, name)

    def execute(self, statement, parameters=None, **kwargs):
        assert not kwargs or kwargs == {'prepare': False}
        if statement == "SELECT set_config('statement_timeout',%s,true)":
            self.statements.append((statement, parameters))
            return None
        if statement == indexed.DATABASE_CONTRACT:
            self.statements.append((statement, parameters))
            return Result([self.identity])
        if statement == indexed.INDEX_CONTRACT:
            self.statements.append((statement, parameters))
            return SimpleNamespace(fetchmany=lambda size: self.key_indexes[:size])
        if statement.startswith('EXPLAIN '):
            self.statements.append((statement, parameters))
            self.plan_preparations.append((statement, kwargs.get('prepare')))
            assert 'ANALYZE' not in statement and 'COLLATE' not in statement
            table = 'rows' if 'FROM market_compact_restore.rows' in statement else 'traces'
            leaf = {'Node Type': 'Index Scan', 'Schema': 'market_compact_restore',
                    'Relation Name': table, 'Index Name': table + '_pkey',
                    'Scan Direction': 'Forward'}
            if any(value in statement for value in
                   ('WHERE series=%s', 'WHERE (', 'WHERE digest >')):
                leaf['Index Cond'] = 'fixture_non_production_bound_condition'
            if 'WHERE original' in statement:
                leaf['Filter'] = 'original'
            if 'LIMIT %s' in statement:
                plan = [{'Plan': {'Node Type': 'Limit', 'Plans': [leaf]}}]
            else:
                plan = self.plan_override or [{'Plan': leaf}]
            return Result([(plan,)])
        if statement in m.ORDER_QUERIES.values():
            self.statements.append((statement, parameters))
            return Result([])
        return super().execute(statement, parameters)


def build(tmp_path):
    source, original, _, _ = fixtures()
    db = FinalDatabase(original, tmp_path)
    budget, report = Budget(), {'restore_verified': False, 'row_progress': {'unchanged': True}}
    counted = indexed.IndexedCountConnection(db, data=tmp_path, budget=budget, report=report)
    return source, db, m.FinalOrderConnection(counted), budget, report


def test_whole_canonical_hash_and_source_proof_are_identical(tmp_path):
    source, db, adapter, budget, report = build(tmp_path)
    expected = verify(source)
    original_progress = deepcopy(report['row_progress'])
    with adapter.transaction() as transaction:
        assert transaction == 'fixture_non_production_transaction'
        actual = frozen.verify_snapshot(adapter, 'restored', check=budget.check,
                                        max_rows=4, max_traces=3)
        with adapter.cursor(name='original_trace_keys') as cursor:
            assert cursor.execute(m.ORIGINAL_ORDER) is cursor
            assert cursor.fetchmany(400) == sorted((row[0],) for row in db.traces if row[3])
    assert frozen.equal_reports(expected, actual)['data_equal'] is True
    assert actual['logical_sha256'] == expected['logical_sha256']
    assert actual['original_traces_sha256'] == expected['original_traces_sha256']
    assert actual['rows'] == 4 and actual['original_traces'] == 2
    assert report['restore_verified'] is False and report['row_progress'] == original_progress
    evidence = report['final_order_index_contract']
    assert evidence['state'] == 'verified' and evidence['all_ordering_kinds_checked'] is True
    assert evidence['ordering_checks'] == 5 and len(evidence['plans']) == 3
    assert not any(evidence[key] for key in
                   ('source_references_verified', 'ordered_hash_verified', 'restore_verified'))
    assert not adapter.final_certified and not adapter.managed_depth
    assert db.closed.count('verify_rows') == 3 and 'verify_traces' in db.closed
    assert all('COLLATE' not in sql for sql, _ in db.statements if sql.startswith('EXPLAIN '))
    assert indexed.IndexedResumeRestore.final_verify is indexed.resume.ResumeRestore.final_verify
    # The independent original verifier and frozen delivery SQL remain explicit C.
    original_db = fixtures()[1]
    original_report = verify(original_db)
    assert original_report == actual
    assert any('ORDER BY time COLLATE "C"' in sql for sql, _ in original_db.statements)


def test_each_actual_series_plan_is_checked_with_bound_parameters(tmp_path):
    _, db, adapter, _, report = build(tmp_path)
    key = "'; DROP TABLE fixture; --"
    with adapter.transaction():
        with adapter.cursor(name='verify_rows') as cursor:
            cursor.execute(m.ROWS_ORDER, ('a',))
        adapter.execute(m.ROWS_ORDER, (key,))
    explains = [(sql, params) for sql, params in db.statements
                if sql == 'EXPLAIN (FORMAT JSON, VERBOSE) ' + m.ORDER_QUERIES[m.ROWS_ORDER]]
    assert [params for _, params in explains] == [('a',), (key,)]
    assert all(key not in sql for sql, _ in explains)
    assert all(prepare is False for sql, prepare in db.plan_preparations
               if sql == 'EXPLAIN (FORMAT JSON, VERBOSE) ' + m.ORDER_QUERIES[m.ROWS_ORDER])
    assert report['final_order_index_contract']['ordering_checks'] == 2
    assert not report['final_order_index_contract']['all_ordering_kinds_checked']


@pytest.mark.parametrize('slot,value', [(0, 'postgres'), (1, 'remote'), (2, 5432),
    (3, 'other-data'), (4, 160011), (5, 'LATIN1'), (6, 'en_US'), (7, 'en_US'), (8, 'i')])
def test_changed_owned_catalog_refuses_before_ordering_read(tmp_path, slot, value):
    _, db, adapter, _, report = build(tmp_path)
    db.identity[slot] = value
    with pytest.raises(indexed.resume.local.Refused, match='pg17_default_c'):
        with adapter.transaction():
            adapter.execute(m.TRACES_ORDER)
    assert report['final_order_index_contract']['state'] == 'refused'
    assert not any(sql in m.ORDER_QUERIES.values() for sql, _ in db.statements)


def test_index_object_collation_must_match_default_key_and_pk(tmp_path):
    _, db, adapter, _, _ = build(tmp_path)
    row = list(db.key_indexes[1])
    row[3] = False
    db.key_indexes[1] = row
    with pytest.raises(indexed.resume.local.Refused, match='collation_required'):
        with adapter.transaction():
            adapter.execute(m.ROWS_ORDER, ('a',))


@pytest.mark.parametrize('kind', ['Seq Scan', 'Sort', 'Gather', 'Bitmap Heap Scan', 'Limit'])
def test_actual_final_nonordered_index_plan_is_refused(tmp_path, kind):
    _, db, adapter, _, report = build(tmp_path)
    db.plan_override = [{'Plan': {'Node Type': kind}}]
    with pytest.raises(indexed.resume.local.Refused, match='final_order_index_plan_required'):
        with adapter.transaction():
            with adapter.cursor(name='verify_traces') as cursor:
                cursor.execute(m.TRACES_ORDER, ())
    assert db.closed == ['verify_traces'] and db.transactions == ['begin', 'rollback']
    assert report['final_order_index_contract']['state'] == 'refused'
    assert not any(sql == m.ORDER_QUERIES[m.TRACES_ORDER] for sql, _ in db.statements)


@pytest.mark.parametrize('change', [{'Index Name': 'other'}, {'Relation Name': 'other'},
    {'Schema': 'public'}, {'Scan Direction': 'No Movement'}, {'Scan Direction': 'Backward'},
    {'Plans': [{}]},
    {'Index Cond': None}, {'Filter': 'unrelated'}])
def test_final_plan_identity_condition_and_no_hidden_sort_are_required(tmp_path, change):
    _, db, adapter, _, _ = build(tmp_path)
    leaf = {'Node Type': 'Index Scan', 'Schema': 'market_compact_restore',
            'Relation Name': 'rows', 'Index Name': 'rows_pkey',
            'Scan Direction': 'Forward', 'Index Cond': 'fixture_non_production'}
    leaf.update(change)
    db.plan_override = [{'Plan': leaf}]
    with pytest.raises(indexed.resume.local.Refused, match='final_order_index_plan_required'):
        with adapter.transaction():
            adapter.execute(m.ROWS_ORDER, ('a',))


@pytest.mark.parametrize('settings', [('off', 'repeatable read'), ('on', 'read committed')])
def test_final_requires_readonly_repeatable_snapshot(tmp_path, settings):
    _, db, adapter, _, _ = build(tmp_path)
    db.settings = settings
    with pytest.raises(indexed.resume.local.Refused, match='readonly_snapshot'):
        with adapter.transaction():
            adapter.execute(m.TRACES_ORDER)
    assert not any(sql.startswith('EXPLAIN ') for sql, _ in db.statements)


def test_unmanaged_transaction_refuses_and_new_snapshot_recertifies(tmp_path):
    _, db, adapter, _, report = build(tmp_path)
    db.info.transaction_status.name = 'INTRANS'
    with pytest.raises(indexed.resume.local.Refused, match='owned_snapshot'):
        adapter.execute(m.TRACES_ORDER)
    db.info.transaction_status.name = 'IDLE'
    for _ in range(2):
        with adapter.transaction():
            adapter.execute(m.TRACES_ORDER)
    assert sum(sql == indexed.DATABASE_CONTRACT for sql, _ in db.statements) == 2
    assert report['final_order_index_contract']['ordering_checks'] == 1


@pytest.mark.parametrize('sql,params,kwargs', [(m.ROWS_ORDER, (), {}),
    (m.ROWS_ORDER, ['a'], {}), (m.ROWS_ORDER, ('a', 'b'), {}),
    (m.TRACES_ORDER, ('a',), {}), (m.ORIGINAL_ORDER, None, {'binary': True})])
def test_fixed_query_parameter_contract_refuses(tmp_path, sql, params, kwargs):
    _, db, adapter, _, _ = build(tmp_path)
    with pytest.raises(indexed.resume.local.Refused, match='parameters_invalid'):
        with adapter.transaction():
            adapter.execute(sql, params, **kwargs)
    assert not any(statement.startswith('EXPLAIN ') for statement, _ in db.statements)


def test_budget_cancel_closes_cursor_and_never_executes_unproved_order(tmp_path):
    _, db, adapter, budget, report = build(tmp_path)
    budget.failure = KeyboardInterrupt('fixture_non_production_cancel')
    with pytest.raises(KeyboardInterrupt):
        with adapter.transaction():
            with adapter.cursor(name='verify_rows') as cursor:
                cursor.execute(m.ROWS_ORDER, ('a',))
    assert db.closed == ['verify_rows'] and db.transactions == ['begin', 'rollback']
    assert report['final_order_index_contract']['state'] == 'refused'


def test_unrelated_sql_and_distinct_series_keys_are_not_rewritten(tmp_path):
    _, db, adapter, _, report = build(tmp_path)
    sql = frozen._series_statement('market_compact_restore.rows')
    with adapter.transaction():
        with adapter.cursor(name='verify_series') as cursor:
            cursor.execute(sql, ())
            assert cursor.fetchmany(400)
        adapter.execute(m.SNAPSHOT_SETTINGS)
    assert (sql, ()) in db.statements
    assert 'final_order_index_contract' not in report


def test_runner_connect_installs_wrapper_without_changing_run_or_final(monkeypatch, tmp_path):
    _, db, _, budget, report = build(tmp_path)

    @contextmanager
    def fake_connect(self, database):
        assert database == indexed.resume.local.DATABASE
        yield db

    monkeypatch.setattr(indexed.resume.ResumeRestore, 'connect', fake_connect)
    runner = object.__new__(indexed.IndexedResumeRestore)
    runner.data, runner.budget, runner.report = tmp_path, budget, report
    with runner.connect() as adapter:
        assert isinstance(adapter, m.FinalOrderConnection)
        assert isinstance(adapter.db, indexed.IndexedCountConnection)
        assert adapter.db.db is db and adapter.budget is budget
    assert indexed.IndexedResumeRestore.run is indexed.resume.ResumeRestore.run
    assert indexed.IndexedResumeRestore.final_verify is indexed.resume.ResumeRestore.final_verify
