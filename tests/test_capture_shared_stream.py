"""fixture_non_production: local archives and mocked DB, never provider evidence."""

import gzip
import hashlib
import json
import stat
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops.market import capture_shared_stream as capture

DAY = '2026-10-12'
CODE = '005930'
AT = datetime(2026, 10, 12, 0, 1, tzinfo=UTC)
EPOCH = 'a' * 32


def archive(root, namespace, body, url):
    content = json.dumps(body).encode()
    digest = hashlib.sha256(content).hexdigest()
    directory = root / 'raw' / namespace
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (digest + '.bin')).write_bytes(content)
    (directory / (digest + '.json')).write_text(json.dumps({
        'sha256': digest, 'bytes': len(content), 'url': url,
        'payload': content.decode(), 'fixture_non_production': True}), encoding='utf8')
    return {'source_document_id': digest, 'filing_id': digest}


def fixture(root, *, epoch=EPOCH, seconds=0, count=10):
    source = AT.replace(second=seconds).isoformat()
    calendar = {'today': {'date': DAY, 'integrated': {
        'preMarket': None, 'afterMarket': None,
        'regularMarket': {'startTime': DAY + 'T09:00:00+09:00',
                          'endTime': DAY + 'T15:30:00+09:00'}}},
        'nextBusinessDay': {'date': '2026-10-13', 'integrated': None}}
    trace = archive(root, 'toss', {'result': calendar},
                    'https://openapi.tossinvest.com/api/v1/market-calendar/KR')
    values = {'toss-calendar:KR': {'calendar': calendar,
                                 'source_trace': {**trace, 'source': 'toss'}},
              'provider-toss-stream': {'epoch': epoch, 'status': 'subscribed',
                                      'token': 'fixture_private_never_exported'}}
    for kind in capture.KINDS:
        data = {'currency': 'KRW', 'timestamp': source}
        if kind == 'trade':
            data.update(price='100', volume='2')
        else:
            data.update(asks=[{'price': '101', 'volume': '10'}],
                        bids=[{'price': '99', 'volume': '12'}])
        trace = archive(root, 'toss-stream', {'type': 'message', 'topic': f'{kind}:kr:{CODE}',
                        'data': data, 'fixture_non_production': True}, f'{kind}:kr:{CODE}')
        values[f'toss-stream-evidence:{DAY}:{CODE}:{kind}'] = {
            'day': DAY, 'security_code': CODE, 'kind': kind, 'last_source_time': source,
            'last_received_at': source, 'epochs': [epoch], 'messages': count,
            'fresh_messages': 0,  # Historical counter bug cannot invent or hide raw proof.
            'source_trace': {**trace, 'source': 'toss_websocket'}}
    return values


def observe(root, **options):
    values = fixture(root, **options)
    return capture.observation(values, root, DAY, [CODE],
                               AT.replace(second=options.get('seconds', 0) + 1))


def test_actual_raw_shape_and_new_receipts_are_required_not_subscriptions(tmp_path):
    before = observe(tmp_path)
    after = observe(tmp_path, seconds=20, count=30)
    result = capture.compare(before, after)
    assert result['both_kinds_observed'] is True
    assert result['epoch_recovery_observed'] is False
    assert not result['browser_render_verified'] and not result['forced_disconnect_verified']
    assert after['evidence'][0]['worker_fresh_counter'] == 0
    assert 'fixture_private_never_exported' not in json.dumps(after)
    empty = capture.observation({'provider-toss-stream': {'status': 'subscribed',
        'accepted': ['trade:kr:005930'], 'last_event_at': AT.isoformat()}},
        tmp_path, DAY, [CODE], AT)
    assert not any(row['fresh_at_capture'] for row in empty['evidence'])


@pytest.mark.parametrize('change', ['old_time', 'wrong_symbol', 'rest_source', 'invalid_numeric',
                                  'old_digest', 'unknown_calendar', 'holiday', 'outside_session'])
def test_invalid_or_nonlive_proof_never_passes(tmp_path, change):
    values = fixture(tmp_path)
    row = values[f'toss-stream-evidence:{DAY}:{CODE}:trade']
    if change == 'old_time':
        row['last_source_time'] = '2026-10-08T09:01:00+09:00'
    elif change == 'wrong_symbol':
        row['security_code'] = '000660'
    elif change == 'rest_source':
        row['source_trace']['source'] = 'toss'
    elif change == 'old_digest':
        row['source_trace']['sha256'] = 'b'*64
    elif change == 'invalid_numeric':
        trace = archive(tmp_path, 'toss-stream', {'type': 'message', 'topic': f'trade:kr:{CODE}',
            'data': {'currency': 'KRW', 'timestamp': AT.isoformat(),
                     'price': 'NaN', 'volume': '2'}},
            f'trade:kr:{CODE}')
        row['source_trace'].update(trace)
    elif change == 'unknown_calendar':
        values.pop('toss-calendar:KR')
    elif change == 'holiday':
        calendar = {'today': {'date': DAY, 'integrated': None}}
        trace = archive(tmp_path, 'toss', {'result': calendar},
                        'https://openapi.tossinvest.com/api/v1/market-calendar/KR')
        values['toss-calendar:KR'] = {'calendar': calendar,
                                    'source_trace': {**trace, 'source': 'toss'}}
    captured = AT.replace(hour=23) if change == 'outside_session' else AT
    actual = capture.observation(values, tmp_path, DAY, [CODE], captured)
    assert actual['evidence'][0]['fresh_at_capture'] is False


def test_unchanged_old_receipt_is_not_a_new_message(tmp_path):
    before = observe(tmp_path)
    after = deepcopy(before)
    after['captured_at'] = AT.replace(second=21).isoformat()
    assert capture.compare(before, after)['both_kinds_observed'] is False


def test_existing_compressed_calendar_is_verified_without_mutation(tmp_path):
    values = fixture(tmp_path)
    digest = values['toss-calendar:KR']['source_trace']['source_document_id']
    directory = tmp_path / 'raw' / 'toss'
    binary = directory / (digest + '.bin')
    compressed = directory / (digest + '.bin.gz')
    compressed.write_bytes(gzip.compress(binary.read_bytes()))
    binary.unlink()  # Isolated fixture only; production tool never removes files.
    before = compressed.read_bytes()
    result = capture.observation(values, tmp_path, DAY, [CODE], AT)
    assert result['calendar']['open_at_capture'] is True
    assert result['calendar']['provider_next_business_day'] == '2026-10-13'
    assert compressed.read_bytes() == before and not binary.exists()
    compressed.write_bytes(gzip.compress(b'x' * (capture.MAX_BYTES + 1)))
    with pytest.raises(capture.Refused, match='archive_expansion_limit'):
        capture.original(tmp_path, 'toss', digest)


def test_other_topic_or_subscription_raw_is_never_trade_evidence(tmp_path):
    values = fixture(tmp_path)
    row = values[f'toss-stream-evidence:{DAY}:{CODE}:trade']
    for raw in ({'type': 'subscriptions', 'subscribed': [f'trade:kr:{CODE}']},
                {'type': 'message', 'topic': 'trade:kr:000660', 'data': {}}):
        trace = archive(tmp_path, 'toss-stream', raw, f'trade:kr:{CODE}')
        row['source_trace'].update(trace)
        result = capture.observation(values, tmp_path, DAY, [CODE], AT)
        assert result['evidence'][0]['reason'] == 'message_topic'


def test_epoch_change_needs_completed_matching_rest_recovery(tmp_path):
    before = observe(tmp_path)
    values = fixture(tmp_path, epoch='b'*32, seconds=20, count=30)
    after = capture.observation(values, tmp_path, DAY, [CODE], AT.replace(second=21))
    assert capture.compare(before, after)['epoch_recovery_observed'] is False
    values[f'toss-recovery:{CODE}'] = {'status': 'ready', 'epoch': 'b'*32,
                                      'completed_at': AT.replace(second=10).isoformat()}
    after = capture.observation(values, tmp_path, DAY, [CODE], AT.replace(second=21))
    assert capture.compare(before, after)['epoch_recovery_observed'] is True
    values[f'toss-recovery:{CODE}']['epoch'] = EPOCH
    after = capture.observation(values, tmp_path, DAY, [CODE], AT.replace(second=21))
    assert capture.compare(before, after)['epoch_recovery_observed'] is False


def test_corrupt_oversized_archive_and_symlink_are_rejected(tmp_path, monkeypatch):
    values = fixture(tmp_path)
    trace = values[f'toss-stream-evidence:{DAY}:{CODE}:trade']['source_trace']
    digest = trace['source_document_id']
    path = tmp_path / 'raw' / 'toss-stream' / (digest+'.bin')
    path.write_bytes(b'changed fixture')
    result = capture.observation(values, tmp_path, DAY, [CODE], AT)
    assert result['evidence'][0]['reason'] == 'archive_hash_mismatch'
    path.write_bytes(b'x'*(capture.MAX_BYTES+1))
    with pytest.raises(capture.Refused, match='size_limit'):
        capture.original(tmp_path, 'toss-stream', digest)
    real_lstat = Path.lstat
    def fixture_link(self):
        if self == path:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return real_lstat(self)
    monkeypatch.setattr(Path, 'lstat', fixture_link)
    with pytest.raises(capture.Refused, match='archive_link_refused'):
        capture.original(tmp_path, 'toss-stream', digest)


class DB:
    def __init__(self, *, fail=False):
        self.statements = []
        self.fail = fail

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if sql.startswith('SELECT transaction_timestamp'):
            return type('One', (), {'fetchone': lambda _: (AT, 'on', 'repeatable read')})()
        if sql.startswith('SELECT key'):
            if self.fail:
                raise RuntimeError('fixture_private_error')
            return [('provider-toss-stream', '{}')]
        return self


def test_readonly_exact_primary_keys_bounded_body_and_rollback():
    keys = capture.snapshot_keys(DAY, [CODE])
    assert len(keys) == 6 and len(capture.snapshot_keys(DAY, [CODE, '000660', '000270'])) == 12
    db = DB()
    assert capture.read_snapshot(db, keys) == (AT, {'provider-toss-stream': {}})
    assert db.statements[0][0] == 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY'
    assert db.statements[-1][0] == 'ROLLBACK'
    assert 'WHERE key=ANY(%s)' in db.statements[2][0]
    assert db.statements[2][1] == (capture.MAX_BYTES, list(keys))
    db = DB(fail=True)
    with pytest.raises(RuntimeError):
        capture.read_snapshot(db, keys)
    assert db.statements[-1][0] == 'ROLLBACK'


@pytest.mark.parametrize('codes', [[], [CODE]*2, [CODE, '000660', '000270', '005380'],
                                 ['../secret'], ['0001a0']])
def test_capture_targets_are_bounded(codes):
    with pytest.raises(capture.Refused):
        capture.snapshot_keys(DAY, codes)
