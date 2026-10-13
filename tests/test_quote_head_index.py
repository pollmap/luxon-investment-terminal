"""fixture_non_production: an explicit, bounded, additive database operation."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('quote_head_index', Path(__file__).parents[1] /
                                            'ops/market/ensure_quote_head_index.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Database:
    def __init__(self, present=False, valid=True):
        self.present, self.valid, self.calls = present, valid, []
    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        if sql == module.DDL:
            self.present, self.valid = True, True
        self.last = sql
        return self
    def fetchone(self):
        if self.last.startswith('EXPLAIN'):
            return ([{'Plan': {'Node Type': 'Index Only Scan' if self.present else 'Seq Scan'}}],)
        return (self.valid, 1024, module.DDL) if self.present else None


def test_default_inspection_never_creates_an_index():
    db = Database()
    assert module.run(db)['mode'] == 'readonly'
    assert all(not sql.startswith('CREATE') for sql, _ in db.calls)


def test_additive_operation_is_concurrent_payload_free_and_idempotent():
    db = Database()
    first = module.run(db, apply=True, free_bytes=1024**3)
    second = module.run(db, apply=True, free_bytes=1024**3)
    assert first['changed'] and not second['changed']
    assert first['data_rows_modified'] == 0
    assert 'CONCURRENTLY' in module.DDL and 'payload' not in module.DDL
    assert all(not sql.startswith(('DROP', 'DELETE', 'UPDATE', 'TRUNCATE')) for sql, _ in db.calls)


@pytest.mark.parametrize('db,free', [(Database(), 1), (Database(True, False), 1024**3)])
def test_space_or_invalid_existing_index_refuses_mutation(db, free):
    with pytest.raises(ValueError):
        module.run(db, apply=True, free_bytes=free)
    assert all(not sql.startswith('CREATE') for sql, _ in db.calls)
