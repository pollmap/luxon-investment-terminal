"""fixture_non_production: bounded reads preserve full-period OHLC and returns."""
from datetime import date,timedelta
import gzip
import json
import pytest
from fastapi.testclient import TestClient
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.price_window import price_window,months_before
from services.market_dashboard.bars import aggregate_bars
from services.market_dashboard import app as api


@pytest.fixture
def sample(tmp_path):
    store=Store('sqlite://',tmp_path)
    rows=[]
    for i in range(1500):
        day=date(2022,1,1)+timedelta(days=i)
        if day.weekday()>4:
            continue
        rows.append({'date':day.isoformat(),'time':day.isoformat()+'T15:30:00+09:00','open':100+i,'high':105+i,'low':99+i,
            'close':103+i,'volume':10+i,'confirmed':True,
            'source_trace':{'source':'fixture_non_production','large_raw_field':'x'*10000}})
    store.put('universe',{'rows':[{'code':'005930','name':'fixture','market':'KOSPI','source_trace':{'source':'fixture_non_production'}}]})
    put_series(store,'bars:005930:통합:day',{'rows':rows,'venue':'통합','adjustment':'source',
        'source_trace':{'source':'fixture_non_production'}})
    return store,rows


@pytest.mark.parametrize('interval',['day','week','month'])
def test_bounded_window_equals_full_history_aggregation(sample,interval,monkeypatch):
    store,rows=sample
    # No Store.get path may hydrate the entire archive.
    monkeypatch.setattr(store,'get',lambda *args,**kwargs:pytest.fail('full snapshot materialized'))
    result=price_window(store,'005930',range_id=21,interval=interval)
    start=months_before(date.fromisoformat(rows[-1]['date']),1).isoformat()
    expected=aggregate_bars(rows,interval=interval,start=start)['rows']
    assert result['rows']==expected
    assert result['coverage']=={'start':rows[0]['date'],'end':rows[-1]['date']}
    assert len(gzip.compress(json.dumps(result).encode()))<100000
    assert 'large_raw_field' not in json.dumps(result)


def test_returns_are_exact_anchors_without_entire_archive(sample):
    store,rows=sample
    result=price_window(store,'005930',view='returns')
    expected={}
    last=date.fromisoformat(rows[-1]['date'])
    for days in (0,7,30,90,365):
        target=(last-timedelta(days=days)).isoformat()
        row=next(r for r in reversed(rows) if r['date']<=target)
        expected[row['date']]={'date':row['date'],'close':row['close']}
    assert result['rows']==sorted(expected.values(),key=lambda r:r['date'])
    assert len(result['rows'])<=5


def test_legacy_and_normalized_queries_agree(sample):
    store,rows=sample
    store.put('prices:000660',{'rows':rows,'venue':'KRX'})
    normalized=price_window(store,'005930',range_id=252,interval='week')
    legacy=price_window(store,'000660',range_id=252,interval='week')
    assert normalized['rows']==legacy['rows']
    assert legacy['venue']=='KRX'


def test_endpoint_revision_invalidation_and_conditional_get(sample,monkeypatch):
    store,rows=sample
    monkeypatch.setattr(api,'get_store',lambda:store)
    with TestClient(api.app) as client:
        url=api.BASE+'/securities/005930/price-window?range=21'
        first=client.get(url)
        assert first.status_code==200
        assert client.get(url,headers={'If-None-Match':first.headers['etag']}).status_code==304
        changed={**rows[-1],'close':rows[-1]['close']+1}
        put_series(store,'bars:005930:통합:day',{'rows':[changed],'venue':'통합'})
        second=client.get(url,headers={'If-None-Match':first.headers['etag']})
        assert second.status_code==200 and second.json()['rows'][-1]['close']==changed['close']
        assert client.get(url+'&interval=garbage').status_code==422
        assert client.get(url.replace('005930','999999')).status_code==404


def test_missing_candle_does_not_become_fake_ohlc(sample):
    store,_=sample
    store.put('prices:000660',{'rows':[{'date':'2026-01-02','close':100}],'venue':'KRX'})
    row=price_window(store,'000660')['rows'][0]
    assert row['close']==100 and row['open'] is None and row['volume'] is None


@pytest.mark.parametrize('interval',['week','month'])
def test_ma_keeps_twenty_observed_periods_across_long_suspension(tmp_path,interval):
    store=Store('sqlite://',tmp_path)
    rows=[]
    for i in range(2400):
        d=date(2019,1,1)+timedelta(days=i)
        if d.weekday()<5 and not date(2021,1,1)<d<date(2025,1,1):
            rows.append({'date':d.isoformat(),'open':100,'high':110,'low':90,'close':100+i,'volume':10})
    put_series(store,'prices:005930',{'rows':rows})
    actual=price_window(store,'005930',range_id=21,interval=interval)
    start=months_before(date.fromisoformat(rows[-1]['date']),1).isoformat()
    assert actual['rows']==aggregate_bars(rows,interval=interval,start=start)['rows']
