import asyncio
from datetime import datetime
import io
from types import SimpleNamespace
import zipfile
from zoneinfo import ZoneInfo

import httpx
import pytest

from services.market_dashboard.authenticated_sources import AuthenticatedSources, DartProviderUnavailable
from services.market_dashboard.dart_corporations import DartCorporationResponseError, parse_corporations
from services.market_dashboard.store import Store


def archive(xml):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('CORPCODE.xml', xml)
    return stream.getvalue()


VALID = archive('<result><list><stock_code>005930</stock_code><corp_code>00126380</corp_code></list></result>')
MAINTENANCE = b'<?xml version="1.0" encoding="UTF-8"?><result><status>800</status><message>maintenance</message></result>'


def source_for(store, responses):
    calls = []
    class Client:
        async def get(self, url, params):
            calls.append(url)
            await asyncio.sleep(0)
            content = responses[min(len(calls)-1, len(responses)-1)]
            return httpx.Response(200, content=content, request=httpx.Request('GET', url))
    source = AuthenticatedSources(SimpleNamespace(store=store, client=Client()))
    source.dart = 'fixture_non_production'
    return source, calls


@pytest.mark.parametrize('content', [MAINTENANCE, b'\xef\xbb\xbf'+MAINTENANCE,
                                    b'{"status":"800","message":"maintenance"}'])
def test_provider_maintenance_backoff_shared_between_instances(tmp_path, content):
    store = Store('sqlite://', tmp_path)
    first, calls = source_for(store, [content])
    with pytest.raises(DartProviderUnavailable):
        asyncio.run(first._dart_get('https://example.test/corpCode.xml', params={}))
    retry = store.get('dart-provider-retry')
    assert retry['status'] == '800' and retry['source_hash']
    other, second_calls = source_for(store, [VALID])
    with pytest.raises(DartProviderUnavailable):
        asyncio.run(other._dart_get('https://example.test/report.json', params={}))
    assert len(calls) == 1 and second_calls == []
    assert store.get('dart-usage')['count'] == 1


def test_bad_refresh_preserves_existing_mapping_without_freshening_date(tmp_path):
    store = Store('sqlite://', tmp_path)
    existing = {'date': '2000-01-01', 'codes': {'005930': '00126380'}}
    store.put('dart-corporations', existing)
    source, calls = source_for(store, [MAINTENANCE])
    assert asyncio.run(source.corporations()) == existing['codes']
    assert store.get('dart-corporations') == existing
    assert store.get('dart-corporations-refresh')['provider_status'] == '800'
    assert asyncio.run(source.corporations()) == existing['codes']
    assert len(calls) == 1


def test_missing_mapping_and_pending_maintenance_does_not_return_empty_success(tmp_path):
    store = Store('sqlite://', tmp_path)
    source, calls = source_for(store, [MAINTENANCE])
    with pytest.raises(DartProviderUnavailable):
        asyncio.run(source.corporations())
    with pytest.raises(DartCorporationResponseError, match='pending'):
        asyncio.run(source.corporations())
    assert store.get('dart-corporations') is None
    assert len(calls) == 1


def test_successful_refresh_after_backoff_stores_source_and_single_shared_request(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.put('dart-provider-retry', {'status': '800', 'next_at': '2000-01-01T00:00:00+00:00'})
    store.put('dart-corporations-refresh', {'status': 'error', 'next_at': '2000-01-01T00:00:00+00:00'})
    source, calls = source_for(store, [VALID])
    async def parallel():
        return await asyncio.gather(source.corporations(), source.corporations(), source.corporations())
    results = asyncio.run(parallel())
    assert results == [{'005930': '00126380'}]*3
    assert len(calls) == 1
    saved = store.get('dart-corporations')
    assert saved['source_hash']
    assert saved['date'] == datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    assert store.get('dart-corporations-refresh')['status'] == 'ready'


@pytest.mark.parametrize('content', [b'PK\x03\x04truncated', archive('<result/>'),
    archive('<result><list><stock_code>005930</stock_code><corp_code>123</corp_code></list></result>'),
    archive('<result><list><stock_code>005930</stock_code><corp_code>00126380</corp_code></list>'
            '<list><stock_code>005930</stock_code><corp_code>00126381</corp_code></list></result>')])
def test_invalid_archives_cannot_replace_usable_mapping(tmp_path, content):
    store = Store('sqlite://', tmp_path)
    existing = {'date': '2000-01-01', 'codes': {'005930': '00126380'}}
    store.put('dart-corporations', existing)
    source, calls = source_for(store, [content])
    assert asyncio.run(source.corporations()) == existing['codes']
    assert store.get('dart-corporations') == existing
    assert store.get('dart-corporations-refresh')['source_hash']
    assert len(calls) == 1


def test_non_zip_status_has_safe_error_without_provider_message():
    with pytest.raises(DartCorporationResponseError, match='response: 800') as caught:
        parse_corporations(b'<result><status>800</status><message>sensitive body</message></result>')
    assert 'sensitive body' not in str(caught.value)
