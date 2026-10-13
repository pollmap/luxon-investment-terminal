"""fixture_non_production: no server start, restore, socket or supplier calls."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_market_compact_count_batches import Database, build

from ops.market.compact_restore import indexed_resume as m


class IndexedDatabase(Database):
    def __init__(self, base, data):
        super().__init__(base.traces, base.series)
        self.identity = [m.resume.local.DATABASE, '127.0.0.1', m.resume.local.PORT,
                         str(data.resolve()), 170010, 'UTF8', 'C', 'C', 'c']
        self.indexes = [('rows', 'series', True, True, True, True, True, 2, 'btree', 'rows_pkey'),
                        ('rows', 'time', True, True, True, True, True, 2, 'btree', 'rows_pkey'),
                        ('traces', 'digest', True, True, True, True, True, 1,
                         'btree', 'traces_pkey')]
        self.plan_override = None

    def execute(self, statement, parameters=None):
        if statement == m.DATABASE_CONTRACT:
            self.calls.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: self.identity)
        if statement == m.INDEX_CONTRACT:
            self.calls.append((statement, parameters))
            return SimpleNamespace(fetchmany=lambda maximum: self.indexes[:maximum])
        if statement.startswith('EXPLAIN '):
            self.calls.append((statement, parameters))
            assert 'ANALYZE' not in statement and 'COLLATE' not in statement
            table = 'traces' if m.resume.TRACE_COUNT_PAGE in statement else 'rows'
            leaf = {'Node Type': 'Index Only Scan', 'Schema': 'market_compact_restore',
                    'Relation Name': table, 'Index Name': table + '_pkey'}
            if len(parameters) > 1:
                leaf['Index Cond'] = 'fixture_non_production_condition'
            plan = [{'Plan': {'Node Type': 'Limit', 'Plans': [leaf]}}]
            return SimpleNamespace(fetchone=lambda: (self.plan_override or plan,))
        return super().execute(statement, parameters)


def adapted(monkeypatch, tmp_path):
    old, base, budget, report, state = build(monkeypatch)
    db = IndexedDatabase(base, tmp_path)
    adapter = m.IndexedCountConnection(db, data=tmp_path, budget=budget, report=report)
    sink = m.resume.ResumeSink(adapter, old.manifest, budget, report)
    sink.locked = True  # Explicit fake target lock; not operational evidence.
    return sink, db, adapter, report, state


def test_exact_counts_keep_replay_and_final_hash_gates(monkeypatch, tmp_path):
    sink, db, _, report, state = adapted(monkeypatch, tmp_path)
    progress = deepcopy(report['row_progress'])
    sink.check_counts(state)
    assert report['checkpoint_counts']['state'] == 'matched_checkpoint'
    assert report['checkpoint_counts']['trace_rows'] == 2
    assert report['checkpoint_counts']['series_rows'] == 5
    assert report['checkpoint_counts']['source_references_verified'] is False
    assert report['checkpoint_counts']['ordered_hash_verified'] is False
    assert report['row_progress'] == progress and report['restore_verified'] is False
    assert report['count_index_contract']['state'] == 'verified'
    assert len(report['count_index_contract']['plans']) == 4
    counts = [(sql, params) for sql, params in db.calls
              if sql.startswith((m.resume.TRACE_COUNT_PAGE, m.resume.SERIES_COUNT_PAGE))]
    assert counts and all('COLLATE' not in sql for sql, _ in counts)
    assert all(params[-1] == 2 for _, params in counts)
    assert all(sql.startswith(('SELECT', 'EXPLAIN')) for sql, _ in db.calls)
    assert m.IndexedResumeRestore.restore is not m.resume.ResumeRestore.restore
    assert m.IndexedResumeRestore.run is m.resume.ResumeRestore.run
    assert m.IndexedResumeRestore.final_verify is m.resume.ResumeRestore.final_verify


@pytest.mark.parametrize('slot,value', [(0, 'postgres'), (1, 'remote'), (2, 5432),
    (3, 'other-data'), (4, 160010), (5, 'LATIN1'), (6, 'en_US'), (7, 'en_US'), (8, 'i')])
def test_changed_identity_or_default_collation_refuses_before_count(
        monkeypatch, tmp_path, slot, value):
    sink, db, _, report, state = adapted(monkeypatch, tmp_path)
    db.identity[slot] = value
    with pytest.raises(m.resume.local.Refused, match='pg17_default_c'):
        sink.check_counts(state)
    assert report['count_index_contract']['state'] == 'refused'
    assert not any(sql in m.COUNT_QUERIES.values() for sql, _ in db.calls)


@pytest.mark.parametrize('slot,value', [(1, 'wrong-key'), (2, False), (3, False),
    (4, False), (5, False), (6, False), (7, 1), (8, 'hash'), (9, '')])
def test_invalid_key_index_contract_refuses(monkeypatch, tmp_path, slot, value):
    sink, db, _, _, state = adapted(monkeypatch, tmp_path)
    row = list(db.indexes[0])
    row[slot] = value
    db.indexes[0] = row
    with pytest.raises(m.resume.local.Refused, match='collation_required'):
        sink.check_counts(state)


@pytest.mark.parametrize('kind', ['Seq Scan', 'Sort', 'Gather', 'Bitmap Heap Scan'])
def test_actual_nonindex_plans_are_refused(monkeypatch, tmp_path, kind):
    sink, db, _, report, state = adapted(monkeypatch, tmp_path)
    db.plan_override = [{'Plan': {'Node Type': 'Limit', 'Plans': [{'Node Type': kind}]}}]
    with pytest.raises(m.resume.local.Refused, match='index_plan_required'):
        sink.check_counts(state)
    assert report['count_index_contract']['state'] == 'refused'
    assert report['restore_verified'] is False


def test_continuation_requires_index_condition():
    plan = [{'Plan': {'Node Type': 'Limit', 'Plans': [
        {'Node Type': 'Index Scan', 'Schema': 'market_compact_restore',
         'Relation Name': 'rows', 'Index Name': 'rows_pkey'}]}}]
    with pytest.raises(m.resume.local.Refused, match='index_plan_required'):
        m.index_plan(plan, 'rows', 'rows_pkey', continuation=True)


def test_catalog_and_plan_rechecked_for_post_copy_count(monkeypatch, tmp_path):
    sink, db, _, _, state = adapted(monkeypatch, tmp_path)
    sink.check_counts(state)
    db.info.transaction_status.name = 'IDLE'
    sink.check_counts(state)
    assert sum(sql == m.DATABASE_CONTRACT for sql, _ in db.calls) == 2
    assert db.transactions == ['begin', 'commit']


def test_unowned_transaction_and_wrong_bound_are_refused(monkeypatch, tmp_path):
    _, db, adapter, _, _ = adapted(monkeypatch, tmp_path)
    with pytest.raises(m.resume.local.Refused, match='parameters_invalid'):
        adapter.execute(m.TRACE_FIRST, (999,))
    db.info.transaction_status.name = 'IDLE'
    with pytest.raises(m.resume.local.Refused, match='owned_transaction'):
        adapter.execute(m.TRACE_FIRST, (m.resume.COUNT_BATCH_ROWS,))


def test_noncount_queries_are_unchanged_and_bound_keys_not_interpolated(monkeypatch, tmp_path):
    sink, db, adapter, _, state = adapted(monkeypatch, tmp_path)
    sink.check_counts(state)
    adapter.execute("SELECT set_config('statement_timeout',%s,true)", ('123',))
    assert db.calls[-1] == ("SELECT set_config('statement_timeout',%s,true)", ('123',))
    key = "'; DROP TABLE fixture; --"
    adapter.execute(m.ROWS_NEXT, (key, '2026-01-01', m.resume.COUNT_BATCH_ROWS))
    assert key not in db.calls[-1][0] and db.calls[-1][1][0] == key


def test_offline_entrypoint_never_constructs_or_starts_runner(monkeypatch, capsys):
    monkeypatch.setattr(m, 'IndexedResumeRestore', lambda *args, **kwargs: pytest.fail('offline'))
    assert m.main([]) == 0
    assert '"executed": false' in capsys.readouterr().out


def offline_cli(tmp_path, *arguments):
    """Run the real -m entrypoint, without bytecode or restore-side effects."""
    environment = dict(os.environ)
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*'))
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'ops.market.compact_restore.indexed_resume', *arguments],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        timeout=45, check=False,
    )
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')) == before
    return result


def test_real_module_cli_requires_nonempty_offline_json(tmp_path):
    result = offline_cli(tmp_path)
    assert result.returncode == 0 and result.stdout.strip() and not result.stderr
    assert json.loads(result.stdout) == {
        'executed': False, 'restore_verified': False,
        'adapter': 'owned-default-c-index-v1', 'index_plan_verified': False,
    }


def test_real_preflight_cli_without_bindings_refuses_instead_of_empty_success(tmp_path):
    result = offline_cli(tmp_path, '--preflight')
    assert result.returncode == 2 and not result.stdout.strip()
    message = 'Explicit owned run, archive bindings and both space limits are required'
    assert message in result.stderr
