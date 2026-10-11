"""fixture_non_production: protocol messages, not actual market reception."""
from services.market_dashboard.store import Store
from services.market_dashboard.stream_evidence import StreamEvidence


def test_subscription_is_not_live_and_old_messages_do_not_prove_realtime(tmp_path):
    s=Store('sqlite://',tmp_path)
    monitor=StreamEvidence(s)
    args=dict(code='005930',epoch='fixture',source_time='2026-10-05T09:00:00+09:00',
              received_at='2026-10-05T00:00:01+00:00',evidence={'source':'toss_websocket','sha256':'a'*64})
    monitor.record(kind='subscriptions',**args)
    assert not s.list_prefix('toss-stream-evidence:')
    monitor.record(kind='trade',**args)
    monitor.record(kind='trade',**{**args,'source_time':'2026-10-02T09:00:00+09:00'})
    monitor.flush()
    row=s.get('toss-stream-evidence:2026-10-05:005930:trade')
    assert row['messages']==2 and row['fresh_messages']==1


def test_reconnect_preserves_counts_and_both_epochs_without_claiming_complete_recovery(tmp_path):
    s=Store('sqlite://',tmp_path)
    for epoch in ('first','second'):
        m=StreamEvidence(s)
        m.record(kind='orderbook',code='005930',epoch=epoch,source_time='invalid',
                 received_at='2026-10-05T00:00:00+00:00',evidence={'source':'fixture_non_production'})
        m.flush()
    row=s.get('toss-stream-evidence:2026-10-05:005930:orderbook')
    assert row['epochs']==['first','second'] and row['messages']==2 and row['fresh_messages']==0
    assert 'screen_verification_required' in row['limitations']


def test_real_trace_constructor_records_fresh_message_without_legacy_sha_alias(tmp_path):
    import hashlib

    from services.market_dashboard.providers import trace

    digest = hashlib.sha256(b'fixture_non_production').hexdigest()
    evidence = trace('toss_websocket', 'wss://openapi-ws.tossinvest.com/ws/v1', digest,
                     '2026-10-05T09:00:00+09:00')
    assert evidence['source_document_id'] == digest and 'sha256' not in evidence
    store = Store('sqlite://', tmp_path)
    monitor = StreamEvidence(store)
    monitor.record(kind='trade', code='005930', epoch='fixture',
                   source_time='2026-10-05T09:00:00+09:00', evidence=evidence,
                   received_at='2026-10-05T00:00:01+00:00')
    monitor.flush()
    row = store.get('toss-stream-evidence:2026-10-05:005930:trade')
    assert row['messages'] == row['fresh_messages'] == 1
    assert row['source_trace'] == evidence
    store.engine.dispose()


def test_hash_shape_and_conflicting_alias_never_prove_fresh_message(tmp_path):
    from services.market_dashboard.stream_evidence import message_digest

    for evidence in ({}, {'source_document_id': None, 'sha256': 'a'*64},
                     {'sha256': 'fixture_non_production'}, {'source_document_id': 'A'*64},
                     {'source_document_id': 'a'*64, 'sha256': 'b'*64}):
        assert message_digest(evidence) is None
    assert message_digest({'source_document_id': 'a'*64, 'sha256': 'a'*64}) == 'a'*64
