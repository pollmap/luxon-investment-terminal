from services.market_dashboard.app import assemble_rows
from services.market_dashboard.quote_metrics import trading_day
from services.market_dashboard.store import Store


def make_store(tmp_path, old_period, listing_day='2026-10-02'):
    store = Store('sqlite://', tmp_path)
    # Use an in-scope market: KONEX is intentionally filtered from the public
    # universe, so it cannot exercise quote-value reconciliation here.
    store.put('universe', {'rows': [{
        'code': '052960',
        'name': 'fixture',
        'market': 'KOSDAQ',
        'as_of': listing_day,
        'volume': 0,
        'value': 0,
        'source_trace': {'source': 'fixture_non_production'},
    }]})
    store.put('quotes', {'052960':{'price':3515, 'as_of':'2026-10-02T20:20:23+09:00',
                                 'quote_trace':{'source':'fixture_non_production'}}})
    store.put('enriched', {'052960': {'volume':1, 'value':100,
              'facts': {key:{'value':value, 'period':old_period, 'unit':unit,
                            'source_trace':{'source':'fixture_non_production'}}
                        for key,value,unit in [('volume',1,'주'),('value',100,'원')]}}})
    return store


def test_unknown_old_enrichment_does_not_replace_current_real_zero(tmp_path):
    store=make_store(tmp_path, '기준일 미제공')
    current=assemble_rows(store)[0][0]
    assert current['volume']==0 and current['value']==0
    assert current['facts']['volume']['period']=='2026-10-02'
    assert store.get('enriched')['052960']['facts']['volume']['value']==1


def test_no_same_day_measure_is_unknown_not_stale_or_invented_zero(tmp_path):
    store=make_store(tmp_path, '2026-09-27', listing_day=None)
    current=assemble_rows(store)[0][0]
    assert current['volume'] is None and current['value'] is None
    assert current['facts']['volume']['availability_reason']=='different_or_unknown_trading_day'
    assert current['price']==3515


def test_same_day_enrichment_remains_preferred(tmp_path):
    store=make_store(tmp_path, '2026.10.02.')
    current=assemble_rows(store)[0][0]
    assert current['volume']==1 and current['value']==100


def test_korean_trading_day_is_not_utc_calendar_day():
    assert trading_day('2026-10-01T15:30:00Z')=='2026-10-02'
    assert trading_day('20261002')=='2026-10-02'
    assert trading_day('2026-10-02T15:30:00') is None
    assert trading_day('2026.13.02.') is None


def test_legacy_candle_volume_trace_has_field_units_without_rewriting_evidence():
    from copy import deepcopy
    from services.market_dashboard.quote_metrics import select_daily_measures
    fact = {'value': 123, 'period': '2026-10-08', 'unit': '주', 'venue': '통합',
            'source_trace': {'source': 'toss', 'source_document_id': 'raw-hash',
                'source_url': 'https://openapi.tossinvest.com/api/v1/candles?symbol=005930&interval=1d&adjusted=true',
                'unit': '원', 'period': '2026-10-09T00:40:00Z', 'ingested_at': '2026-10-09T00:40:00Z'}}
    before = deepcopy(fact)
    row = select_daily_measures({'code':'005930', 'as_of':'2026-10-08T19:59:59+09:00'}, [{'volume':fact}])
    trace = row['facts']['volume']['source_trace']
    assert row['volume'] == 123
    assert (trace['unit'], trace['period'], trace['field']) == ('주', '2026-10-08', 'volume')
    assert trace['source_document_id'] == 'raw-hash'
    assert trace['ingested_at'] == before['source_trace']['ingested_at']
    assert fact == before


def test_unrelated_source_or_security_trace_is_not_relabelled():
    from services.market_dashboard.quote_metrics import volume_field_trace
    for url in ('https://example.com/api/v1/candles?symbol=005930&interval=1d',
                'https://openapi.tossinvest.com/api/v1/candles?symbol=000660&interval=1d',
                'https://openapi.tossinvest.com/api/v1/candles?symbol=005930&interval=1m'):
        fact = {'value':123,'period':'2026-10-08','unit':'주','venue':'통합',
                'source_trace':{'source':'toss','source_url':url,'unit':'원'}}
        assert volume_field_trace(fact, '005930') is fact
