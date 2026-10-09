"""fixture_non_production: protocol messages, not actual market reception."""
from services.market_dashboard.stream_evidence import StreamEvidence
from services.market_dashboard.store import Store


def test_subscription_is_not_live_and_old_messages_do_not_prove_realtime(tmp_path):
    s=Store('sqlite://',tmp_path); monitor=StreamEvidence(s)
    args=dict(code='005930',epoch='fixture',source_time='2026-10-05T09:00:00+09:00',
              received_at='2026-10-05T00:00:01+00:00',evidence={'source':'toss_websocket','sha256':'fixture_non_production'})
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
