from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.trading_history import read_history,trading_history


def test_requested_range_uses_database_bounds_without_losing_total_coverage(tmp_path,monkeypatch):
    s=Store('sqlite://',tmp_path)
    key='toss:investor-trading:005930'
    put_series(s,key,{'rows':[{'date':d,'individual':{'netBuyVolume':'7'}}
        for d in ['2019-01-01','2025-06-01','2025-06-02','2026-01-01']],
        'status':'ready','backfill_complete':True})
    actual=s.time_series.rows; calls=[]
    def fetch(key,**kwargs):
        calls.append(kwargs);return actual(key,**kwargs)
    monkeypatch.setattr(s.time_series,'rows',fetch)
    snap=read_history(s,key,'2025-06-01','2025-06-30')
    result=trading_history(snap,'investor-trading',start='2025-06-01',end='2025-06-30')
    assert calls==[{'start':'2025-06-01','end':'2025-06-30'}]
    assert len(result['rows'])==2
    assert result['available_start']=='2019-01-01' and result['available_end']=='2026-01-01'
    assert result['history_complete']


def test_legacy_snapshot_keeps_same_date_filter_and_missing_semantics(tmp_path):
    s=Store('sqlite://',tmp_path);key='toss:investor-trading:005930'
    s.put(key,{'rows':[{'date':'2025-06-01'},{'date':'2025-06-02','individual':{'netBuyVolume':'0'}}]})
    result=trading_history(read_history(s,key,'2025-06-02'), 'investor-trading',start='2025-06-02')
    assert result['rows'][0]['individual']==0
    assert result['rows'][0]['foreigner'] is None
    assert result['available_start']=='2025-06-01'
