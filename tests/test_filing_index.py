"""fixture_non_production: publication indexing and immutable archive replay."""
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from services.market_dashboard.filing_index import collect_periodic_index
from services.market_dashboard.store import Store
from services.market_dashboard.xbrl_facts import collect_xbrl


def test_old_index_reports_queue_once_without_guessing_fiscal_quarters(tmp_path):
    from services.market_dashboard.filing_index import queue_index_reports
    store = Store('sqlite://', tmp_path)
    rows = [{'receipt':f'20180{i}01000001','title':title,'source_trace':{'sha256':'fixture_non_production'}}
            for i,title in enumerate(['사업보고서 (2017.12)','[기재정정]반기보고서 (2017.09)',
              '분기보고서 (2017.03)','[첨부정정]사업보고서 (2017.12)'],1)]
    store.put('filing-index:005930', {'rows':rows,'status':'ready'})
    assert queue_index_reports(store,'005930') == 2
    assert queue_index_reports(store,'005930') == 0
    assert store.get('xbrl-request:'+rows[0]['receipt'])['report_code']=='11011'
    assert store.get('xbrl-request:'+rows[1]['receipt'])['report_code']=='11012'
    assert not store.get('xbrl-request:'+rows[2]['receipt'])
    assert not store.get('xbrl-request:'+rows[3]['receipt'])
    assert store.get('xbrl-index-discovered:005930')['quarter_identity_pending']==[rows[2]['receipt']]


@pytest.mark.asyncio
async def test_index_resumes_pages_preserves_dates_and_omits_secret(tmp_path):
    store = Store('sqlite://', tmp_path)
    calls = []

    async def corporations():
        return {'005930': '00126380'}

    async def get(url, *, params):
        calls.append(params)
        page = int(params['page_no'])
        payload = {'status': '000', 'total_page': 2, 'list': [{
            'corp_code': '00126380', 'rcept_no': f'2026031400000{page}',
            'rcept_dt': '20260316', 'report_nm': 'fixture_non_production'}]}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload,
                               content=json.dumps(payload).encode())

    source = SimpleNamespace(store=store, dart='never-persist-secret',
                             corporations=corporations, _dart_get=get)
    assert (await collect_periodic_index(source, '005930'))['status'] == 'partial'
    ready = await collect_periodic_index(source, '005930')
    assert ready['status'] == 'ready' and len(ready['rows']) == 2
    assert {row['date'] for row in ready['rows']} == {'20260316'}
    assert calls[1]['bgn_de'] == calls[0]['bgn_de']
    await collect_periodic_index(source, '005930')
    assert len(calls) == 2
    assert all(source.dart not in path.read_text(encoding='utf-8') for path in (tmp_path/'raw').rglob('*.json'))


@pytest.mark.asyncio
async def test_failed_index_keeps_last_rows_and_cursor_and_backs_off(tmp_path):
    store = Store('sqlite://', tmp_path)
    before = {'status': 'partial', 'rows': [{'receipt': 'old', 'date': '20250314'}],
              'pagination': {'next_page': 2, 'begin': '20190101', 'end': '20261003'}}
    store.put('filing-index:005930', before)
    calls = []

    async def corporations(): return {'005930': '00126380'}
    async def get(*args, **kwargs):
        calls.append(True)
        raise RuntimeError('do not persist this secret')

    source = SimpleNamespace(store=store, dart='test', corporations=corporations, _dart_get=get)
    result = await collect_periodic_index(source, '005930')
    await collect_periodic_index(source, '005930')
    assert result['rows'] == before['rows'] and result['pagination'] == before['pagination']
    assert result['last_error'] == 'RuntimeError' and len(calls) == 1
    assert 'secret' not in json.dumps(result)


@pytest.mark.asyncio
async def test_publication_refresh_replays_original_bytes_and_checks_hash(tmp_path):
    store = Store('sqlite://', tmp_path)
    xml = b'''<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:i="http://ifrs.example/2025" xmlns:iso="http://www.xbrl.org/2003/iso4217">
      <context id="c"><entity><identifier scheme="test">corp</identifier></entity><period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period></context>
      <unit id="won"><measure>iso:KRW</measure></unit><i:Revenue contextRef="c" unitRef="won">100</i:Revenue></xbrl>'''
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z: z.writestr('instance.xbrl', xml)
    digest = store.raw('dart-xbrl', 'https://example.test/fixture', archive.getvalue())
    filing = '20260314000001'
    store.put('xbrl-job:' + filing, {'status': 'ready', 'raw_hash': digest, 'publication': None})

    async def corporations(): return {'005930': 'corp'}
    async def get(*args, **kwargs): raise AssertionError('Archived original should not be downloaded again')
    source = SimpleNamespace(store=store, dart='test', corporations=corporations, _dart_get=get)
    result = await collect_xbrl(source, '005930', filing, '11011', publication='20260316')
    assert result['status'] == 'ready' and result['facts'] == 1
    assert store.get('official:005930')['xbrl_facts'][0]['published_at'] == '20260316'
    original = store.get('official:005930')
    (tmp_path/'raw'/'dart-xbrl'/(digest + '.bin')).write_bytes(b'corrupted')
    failed = await collect_xbrl(source, '005930', filing, '11011', publication='20260317')
    assert failed['status'] == 'error'
    assert store.get('official:005930') == original
