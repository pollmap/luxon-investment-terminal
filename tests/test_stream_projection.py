"""fixture_non_production: all events archived, only read models coalesced."""
import json
import pytest
from collections import Counter
from datetime import UTC, datetime

from services.market_dashboard.store import Store
from services.market_dashboard.stream_projection import StreamProjection
from services.market_dashboard.toss import TossCollector


def message(kind='trade', price='100', stamp='2026-10-13T10:00:00+09:00'):
    data = {'timestamp': stamp, 'currency': 'KRW'}
    if kind == 'trade':
        data.update(price=price, volume='1')
    else:
        data.update(asks=[{'price': price, 'volume': '20'}],
                    bids=[{'price': '99', 'volume': '30'}])
    return {'type': 'message', 'topic': f'{kind}:kr:005930', 'data': data}


def source(tmp_path):
    return TossCollector(Store('sqlite://', tmp_path))


def test_batch_preserves_equal_time_executions_but_writes_depth_and_quote_once(tmp_path, monkeypatch):
    collector = source(tmp_path)
    store = collector.store
    monitor = StreamProjection(collector, clock=lambda: 0)
    writes, archives = Counter(), []
    put, raw = store.put, store.raw
    def counted(key, *args, **kwargs):
        writes[key] += 1
        return put(key, *args, **kwargs)
    def original(namespace, topic, body):
        archives.append(json.loads(body))
        return raw(namespace, topic, body)
    monkeypatch.setattr(store, 'put', counted)
    monkeypatch.setattr(store, 'raw', original)
    received = datetime.now(UTC).isoformat()
    for _ in range(20):
        monitor.record(message(), ['005930'], 'a' * 32, received)
    monitor.record(message('orderbook', '101'), ['005930'], 'a' * 32, received)
    monitor.finish()
    assert len(archives) == 21 and len(store.get('depth:005930')['trades']) == 20
    assert writes['depth:005930'] == writes['quote:005930'] == 1
    assert writes['provider-toss-stream'] == 1
    assert store.get_quote('005930')['price'] == 100
    assert store.get_quote('005930')['received_at'] == received
    assert all(trade['cumulative_volume'] is None for trade in store.get('depth:005930')['trades'])


def test_late_book_and_trade_do_not_reverse_last_good_quote_or_book(tmp_path):
    collector = source(tmp_path)
    monitor = StreamProjection(collector)
    received = datetime.now(UTC).isoformat()
    monitor.record(message('trade', '110', '2026-10-13T10:01:00+09:00'), ['005930'], 'a' * 32, received)
    monitor.record(message('trade', '100'), ['005930'], 'a' * 32, received)
    monitor.record(message('orderbook', '111', '2026-10-13T10:01:00+09:00'), ['005930'], 'a' * 32, received)
    monitor.record(message('orderbook', '101'), ['005930'], 'a' * 32, received)
    monitor.finish()
    depth = collector.store.get('depth:005930')
    assert collector.store.get_quote('005930')['price'] == 110
    assert depth['book'][0]['ask'] == 111 and len(depth['trades']) == 2


def test_invalid_currency_is_archived_and_cannot_replace_normal_data(tmp_path):
    collector = source(tmp_path)
    monitor = StreamProjection(collector)
    valid = message()
    invalid = message(price='200')
    invalid['data']['currency'] = 'USD'
    received = datetime.now(UTC).isoformat()
    monitor.record(valid, ['005930'], 'a' * 32, received)
    monitor.record(invalid, ['005930'], 'a' * 32, received)
    monitor.finish()
    assert collector.store.get_quote('005930')['price'] == 100
    assert collector.store.get('toss-stream-rejected:005930')['reason'] == 'unsupported_or_invalid_payload'


def test_receipt_clock_stays_before_slow_projection_work(tmp_path, monkeypatch):
    collector = source(tmp_path)
    monitor = StreamProjection(collector)
    received = '2026-10-13T01:00:01+00:00'
    later = '2026-10-13T01:01:00+00:00'
    monkeypatch.setattr('services.market_dashboard.toss.now', lambda: later)
    monitor.record(message(), ['005930'], 'a' * 32, received)
    monitor.finish()
    assert collector.store.get_quote('005930')['received_at'] == received
    evidence = collector.store.get('toss-stream-evidence:2026-10-13:005930:trade')
    assert evidence['last_received_at'] == received and evidence['last_lag_seconds'] == 1


def test_message_budget_flushes_without_waiting_for_timer(tmp_path):
    collector = source(tmp_path)
    monitor = StreamProjection(collector, max_messages=2)
    received = datetime.now(UTC).isoformat()
    monitor.record(message(), ['005930'], 'a' * 32, received)
    assert collector.store.get('depth:005930') is None
    monitor.record(message(), ['005930'], 'a' * 32, received)
    assert len(collector.store.get('depth:005930')['trades']) == 2 and monitor.count == 0


def test_partial_write_retry_cannot_duplicate_executions(tmp_path, monkeypatch):
    collector = source(tmp_path)
    monitor = StreamProjection(collector, clock=lambda: 0)
    received = datetime.now(UTC).isoformat()
    monitor.record(message(), ['005930'], 'a' * 32, received)
    update = collector.store.update_quotes
    monkeypatch.setattr(collector.store, 'update_quotes', lambda *_: (_ for _ in ()).throw(OSError('fixture')))
    assert monitor.try_finish() is False
    assert len(collector.store.get('depth:005930')['trades']) == 1
    monkeypatch.setattr(collector.store, 'update_quotes', update)
    monitor.finish()
    assert len(collector.store.get('depth:005930')['trades']) == 1
    assert monitor.pending == {} and monitor.prepared is None


def test_ambiguous_depth_commit_retry_and_legacy_rows(tmp_path, monkeypatch):
    collector = source(tmp_path)
    collector.store.put('depth:005930', {'trades': [{'time': '09:00:00', 'price': 90}]})
    monitor = StreamProjection(collector, clock=lambda: 0)
    received = datetime.now(UTC).isoformat()
    monitor.record(message(), ['005930'], 'a' * 32, received)
    put = collector.store.put
    def committed_then_failed(key, value):
        put(key, value)
        if key == 'depth:005930':
            raise OSError('fixture ambiguous commit')
    monkeypatch.setattr(collector.store, 'put', committed_then_failed)
    with pytest.raises(OSError):
        monitor.finish()
    monkeypatch.setattr(collector.store, 'put', put)
    monitor.finish()
    assert len(collector.store.get('depth:005930')['trades']) == 2


def test_book_and_each_trade_keep_exact_new_event_evidence(tmp_path):
    collector = source(tmp_path)
    collector.store.put('depth:005930', {'book_trace': {'source_document_id': 'old'}, 'trades_trace': {'source_document_id': 'old'}})
    monitor = StreamProjection(collector, clock=lambda: 0)
    received = datetime.now(UTC).isoformat()
    monitor.record(message(price='101'), ['005930'], 'a' * 32, received)
    monitor.record(message(price='102'), ['005930'], 'a' * 32, received)
    monitor.record(message('orderbook'), ['005930'], 'a' * 32, received)
    monitor.finish()
    depth = collector.store.get('depth:005930')
    traces = [row['source_trace']['source_document_id'] for row in depth['trades']]
    assert len(set(traces)) == 2 and all(len(digest) == 64 for digest in traces)
    assert depth['book_trace']['source'] == 'toss_websocket'
    assert depth['book_trace']['source_document_id'] not in traces


def test_projection_reuses_received_byte_digest_without_serializing_again(tmp_path, monkeypatch):
    from services.market_dashboard.raw_archive import read_original_verified
    collector = source(tmp_path)
    original = (' { "topic" : "trade:kr:005930", "type" : "message", "data" : '
                '{"timestamp":"2026-10-13T10:00:00+09:00", "price":"101",'
                ' "volume":"1", "currency":"KRW", "note":"원문"} }\n').encode('utf-8')
    digest = collector.store.raw('toss-stream', 'wss://openapi-ws.tossinvest.com/ws/v1', original)
    monkeypatch.setattr(collector.store, 'raw', lambda *_: (_ for _ in ()).throw(AssertionError('Second archive write')))
    monitor = StreamProjection(collector)
    monitor.record(json.loads(original), ['005930'], 'a' * 32,
                   '2026-10-13T01:00:01+00:00', archived_digest=digest)
    monitor.finish()
    directory = collector.store.root / 'raw/toss-stream'
    assert read_original_verified(directory, digest) == original
    assert len(list(directory.iterdir())) == 1
    trade = collector.store.get('depth:005930')['trades'][0]
    assert trade['source_trace']['source_document_id'] == digest
    assert collector.store.get_quote('005930')['quote_trace']['source_document_id'] == digest
