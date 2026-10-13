"""Owned PG17 resume with proven default-C index pages; offline by default.

Explicit connection adapters change four bounded checkpoint-count SELECTs and
three exact final-order statements, including their named-cursor delivery.
Owned PG17 UTF8/default-C catalogs and unforced plans gate each translation.
An explicit prefix sink reuses native COPY stages and verified trace decoding.
The frozen final verifier, canonical hash and source-reference/resource gates
remain unchanged; adapter plan evidence is never whole-restore proof.
No runtime monkeypatch, index creation or planner forcing.
"""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from pathlib import Path

from . import resume_restore as resume
from .indexed_counts import FinalOrderConnection
from .optimized_prefix_replay import restore_with_prefix_sink

TRACE_FIRST = resume.TRACE_COUNT_PAGE + 'ORDER BY digest COLLATE "C" LIMIT %s'
TRACE_NEXT = (resume.TRACE_COUNT_PAGE + 'WHERE digest COLLATE "C" > %s COLLATE "C" '
              'ORDER BY digest COLLATE "C" LIMIT %s')
ROWS_FIRST = resume.SERIES_COUNT_PAGE + 'ORDER BY series COLLATE "C",time COLLATE "C" LIMIT %s'
ROWS_NEXT = (resume.SERIES_COUNT_PAGE +
             'WHERE (series COLLATE "C",time COLLATE "C") > '
             '(%s COLLATE "C",%s COLLATE "C") '
             'ORDER BY series COLLATE "C",time COLLATE "C" LIMIT %s')
COUNT_QUERIES = {
    TRACE_FIRST: resume.TRACE_COUNT_PAGE + 'ORDER BY digest LIMIT %s',
    TRACE_NEXT: resume.TRACE_COUNT_PAGE + 'WHERE digest > %s ORDER BY digest LIMIT %s',
    ROWS_FIRST: resume.SERIES_COUNT_PAGE + 'ORDER BY series,time LIMIT %s',
    ROWS_NEXT: resume.SERIES_COUNT_PAGE +
               'WHERE (series,time) > (%s,%s) ORDER BY series,time LIMIT %s',
}

DATABASE_CONTRACT = """SELECT current_database(),host(inet_server_addr()),
inet_server_port(),current_setting('data_directory'),
current_setting('server_version_num')::int,current_setting('server_encoding'),
d.datcollate,d.datctype,d.datlocprovider FROM pg_database d
WHERE d.datname=current_database()"""
INDEX_CONTRACT = """SELECT t.relname,a.attname,
a.attcollation='pg_catalog."default"'::regcollation,
i.indcollation[k.position::int-1]=a.attcollation,
i.indisvalid,i.indisready,i.indisunique,i.indnkeyatts,am.amname,ix.relname
FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
JOIN pg_namespace ns ON ns.oid=t.relnamespace
JOIN pg_class ix ON ix.oid=i.indexrelid JOIN pg_am am ON am.oid=ix.relam
CROSS JOIN LATERAL unnest(i.indkey::smallint[]) WITH ORDINALITY k(attnum,position)
JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
WHERE ns.nspname='market_compact_restore' AND t.relname IN ('rows','traces')
AND i.indisprimary ORDER BY t.relname,k.position"""


def index_plan(plan, table, index, *, continuation):
    """Accept only an unforced Limit over the exact expected btree index."""
    if not isinstance(plan, list) or len(plan) != 1 or not isinstance(plan[0], dict):
        raise resume.local.Refused('count_index_plan_invalid')
    node = plan[0].get('Plan')
    if not isinstance(node, dict) or node.get('Node Type') != 'Limit':
        raise resume.local.Refused('count_index_plan_required')
    children = node.get('Plans')
    if not isinstance(children, list) or len(children) != 1:
        raise resume.local.Refused('count_index_plan_required')
    leaf = children[0]
    if (not isinstance(leaf, dict)
            or leaf.get('Node Type') not in ('Index Scan', 'Index Only Scan')
            or leaf.get('Relation Name') != table
            or leaf.get('Schema') != 'market_compact_restore'
            or leaf.get('Index Name') != index or leaf.get('Plans')
            or leaf.get('Filter') or (continuation and not leaf.get('Index Cond'))):
        raise resume.local.Refused('count_index_plan_required')
    return leaf['Node Type']


class IndexedCountConnection:
    """Explicit connection decorator, never a replacement of module globals."""

    def __init__(self, db, *, data, budget, report):
        self.db, self.data, self.budget, self.report = db, Path(data).resolve(), budget, report
        self.certified = False

    def __getattr__(self, name):
        return getattr(self.db, name)

    def _read(self, sql, parameters=None):
        timeout = min(30000, self.budget.check())
        self.db.execute("SELECT set_config('statement_timeout',%s,true)", (str(timeout),))
        cursor = self.db.execute(sql, parameters)
        self.budget.check()
        return cursor

    def certify(self):
        self.report['count_index_contract'] = {
            'state': 'checking', 'adapter': 'owned-default-c-index-v1',
            'source_references_verified': False, 'ordered_hash_verified': False,
        }
        if self.db.info.transaction_status.name != 'INTRANS':
            raise resume.local.Refused('count_index_owned_transaction_required')
        identity = self._read(DATABASE_CONTRACT).fetchone()
        if (not isinstance(identity, (tuple, list)) or len(identity) != 9
                or identity[0] != resume.local.DATABASE or identity[1] != '127.0.0.1'
                or identity[2] != resume.local.PORT or Path(identity[3]).resolve() != self.data
                or type(identity[4]) is not int or not 170000 <= identity[4] < 180000
                or tuple(identity[5:]) != ('UTF8', 'C', 'C', 'c')):
            raise resume.local.Refused('count_index_pg17_default_c_required')
        rows = self._read(INDEX_CONTRACT).fetchmany(4)
        expected = [('rows', 'series', 2), ('rows', 'time', 2), ('traces', 'digest', 1)]
        indexes = {}
        if len(rows) != len(expected):
            raise resume.local.Refused('count_index_collation_required')
        for row, (table, column, keys) in zip(rows, expected, strict=True):
            if (len(row) != 10 or tuple(row[:2]) != (table, column)
                    or any(value is not True for value in row[2:7])
                    or row[7] != keys or row[8] != 'btree'
                    or not isinstance(row[9], str) or not row[9]
                    or (table in indexes and indexes[table] != row[9])):
                raise resume.local.Refused('count_index_collation_required')
            indexes[table] = row[9]
        evidence = []
        for original, params, table in (
                (TRACE_FIRST, (resume.COUNT_BATCH_ROWS,), 'traces'),
                (TRACE_NEXT, ('0' * 64, resume.COUNT_BATCH_ROWS), 'traces'),
                (ROWS_FIRST, (resume.COUNT_BATCH_ROWS,), 'rows'),
                (ROWS_NEXT, ('', '', resume.COUNT_BATCH_ROWS), 'rows')):
            plan = self._read('EXPLAIN (FORMAT JSON, VERBOSE) ' + COUNT_QUERIES[original],
                              params).fetchone()[0]
            node = index_plan(plan, table, indexes[table], continuation=len(params) > 1)
            evidence.append({'table': table, 'continuation': len(params) > 1,
                             'node': node, 'index': indexes[table]})
        self.report['count_index_contract'] = {
            'state': 'verified', 'adapter': 'owned-default-c-index-v1',
            'pg_major': 17, 'encoding': 'UTF8', 'collation': 'C', 'plans': evidence,
            'source_references_verified': False, 'ordered_hash_verified': False,
        }
        self.certified = True

    def execute(self, statement, parameters=None, **kwargs):
        if statement not in COUNT_QUERIES:
            return self.db.execute(statement, parameters, **kwargs)
        if kwargs or not isinstance(parameters, tuple) or not parameters:
            raise resume.local.Refused('count_index_parameters_invalid')
        expected = 3 if statement == ROWS_NEXT else 2 if statement == TRACE_NEXT else 1
        if len(parameters) != expected or parameters[-1] != resume.COUNT_BATCH_ROWS:
            raise resume.local.Refused('count_index_parameters_invalid')
        if statement == TRACE_FIRST:
            # check_counts starts here, including its later post-COPY transaction.
            self.certified = False
        if not self.certified:
            try:
                self.certify()
            except BaseException:
                self.report['count_index_contract']['state'] = 'refused'
                raise
        if self.db.info.transaction_status.name != 'INTRANS':
            raise resume.local.Refused('count_index_owned_transaction_required')
        return self.db.execute(COUNT_QUERIES[statement], parameters)


class IndexedResumeRestore(resume.ResumeRestore):
    def restore(self, manifest):
        return restore_with_prefix_sink(self, manifest)

    @contextmanager
    def connect(self, database=resume.local.DATABASE):
        # The inherited connection validates the owned local cluster identity.
        with super().connect(database) as db:
            yield FinalOrderConnection(
                IndexedCountConnection(db, data=self.data, budget=self.budget, report=self.report),
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--preflight', action='store_true')
    for name in ('work-directory', 'archive', 'archive-receipt', 'source-report'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--max-total-cluster-bytes', type=int)
    parser.add_argument('--max-growth-bytes', type=int)
    parser.add_argument('--max-seconds', type=int, default=14400)
    args = parser.parse_args(argv)
    if not args.run and not args.preflight:
        print(json.dumps({'executed': False, 'restore_verified': False,
                          'adapter': 'owned-default-c-index-v1', 'index_plan_verified': False}))
        return 0
    if not all((args.work_directory, args.archive, args.archive_receipt, args.source_report,
                args.max_total_cluster_bytes, args.max_growth_bytes)):
        parser.error('Explicit owned run, archive bindings and both space limits are required')
    runner = None
    try:
        inputs = resume.local.validate_inputs(
            args.archive, args.archive_receipt, args.source_report,
        )
        runner = IndexedResumeRestore(inputs, args.work_directory,
                                      max_growth_bytes=args.max_growth_bytes,
                                      max_total_cluster_bytes=args.max_total_cluster_bytes,
                                      max_seconds=args.max_seconds)
        if args.preflight:
            print(json.dumps({'executed': False, 'state': 'preflight_passed',
                              'archive_sha256': runner.saved_manifest.archive_sha256,
                              'expected_rows': runner.saved_manifest.counts(),
                              'space': runner.budget.diagnostic('ready'),
                              'index_plan_verified': False, 'checkpoint_reload_required': True,
                              'archive_content_verified': False, 'restore_verified': False,
                              'production_contacted': False}))
            return 0
        report = runner.run()
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'executed': runner is not None and args.run, 'restore_verified': False,
                          'error_code': 'indexed_resume_refused_or_evidence_failed'}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['restore_verified'] or report['fixture_restore_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
