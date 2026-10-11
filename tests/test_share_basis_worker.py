import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from services.market_dashboard.authenticated_sources import AuthenticatedSources
from services.market_dashboard.share_basis_worker import review_share_basis, review_targets, verified_window
from services.market_dashboard.share_basis_review_store import record_review
from services.market_dashboard.store import Store


CODE = '005930'
COMPANY = '00126380'
EVIDENCE = {'security_code': CODE, 'company_id': COMPANY, 'verified': True,
    'no_split_interval': True, 'start': '2024-01-01', 'end': '2025-09-30',
    'basis_id': 'fixture-review-v1', 'basis_date': '2025-09-30',
    'source_trace': {'source_url': 'https://example.test/issuer-review', 'sha256': 'a'*64}}


def filing(receipt='20251001000001', title='현금ㆍ현물배당결정'):
    return {'corp_code': COMPANY, 'rcept_no': receipt, 'rcept_dt': '20251001', 'report_nm': title}


def response(rows=None, page=1, pages=1, count=None):
    rows = rows if rows is not None else [filing()]
    return {'status': '000', 'page_no': page, 'total_page': pages,
            'total_count': len(rows) if count is None else count, 'list': rows}


@pytest.fixture
def context(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.put('universe', {'rows': [{'code': CODE, 'name': '삼성전자', 'market': 'KOSPI'}]})
    store.put('dart-corporations', {'codes': {CODE: COMPANY}})
    store.put('share-basis:'+CODE, EVIDENCE)
    store.put(f'bars:{CODE}:통합:raw', {'adjustment': 'raw', 'venue': '통합', 'rows': [
        {'date': '2025-10-02', 'close': 100}]})
    return store


def source_for(store, payloads):
    calls = []
    class Client:
        async def get(self, url, params):
            calls.append({k:v for k,v in params.items() if k != 'crtfc_key'})
            payload = payloads[min(len(calls)-1, len(payloads)-1)]
            return httpx.Response(200, json=payload, request=httpx.Request('GET', url))
    source = AuthenticatedSources(SimpleNamespace(store=store, client=Client()))
    source.dart = 'fixture_non_production'
    return source, calls


def test_full_window_extends_evidence_without_modifying_original_facts(context):
    store = context
    source, calls = source_for(store, [response()])
    result = asyncio.run(review_share_basis(source, CODE))
    assert result['status'] == 'ready'
    assert len(calls) == 1
    assert calls[0]['bgn_de'] == '20251001' and calls[0]['end_de'] == '20251002'
    assert calls[0]['pblntf_ty'] == 'I' and calls[0]['last_reprt_at'] == 'N'
    saved = store.get('share-basis:'+CODE)
    assert saved['end'] == '2025-10-02'
    assert saved['review_extension']['page_statuses'] == ['000']
    assert list(store.list_prefix('share-basis-history:'+CODE).values()) == [EVIDENCE]
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'current'
    assert len(calls) == 1


def test_genuine_no_data_retains_status_013_and_exact_request_evidence(context):
    source, calls = source_for(context, [{'status': '013', 'message': 'no data'}])
    result = asyncio.run(review_share_basis(source, CODE))
    assert result['status'] == 'ready'
    assert result['pages'][0]['status'] == '013'
    assert result['pages'][0]['empty_response_verified'] is True
    assert result['pages'][0]['request']['corp_code'] == COMPANY
    saved = context.get('share-basis:'+CODE)
    assert saved['review_extension']['page_statuses'] == ['013']
    assert saved['review_extension']['filing_rows_reviewed'] == 0


def test_incomplete_index_waits_then_resumes_one_page_per_turn(context):
    source, calls = source_for(context, [response(page=1,pages=2,count=2),
        response([filing('20251001000002')],page=2,pages=2,count=2)])
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'partial'
    assert len(calls) == 1 and context.get('share-basis:'+CODE) == EVIDENCE
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'ready'
    assert len(calls) == 2 and calls[1]['page_no'] == '2'


@pytest.mark.parametrize('title', [
    '주식분할결정', '유상증자결정', '자기주식소각결정',
    '주요사항보고서(회사합병결정)', '회사분할결정', '분할합병결정',
    '흡수합병완료', '주식교환결정', '주식이전결정',
    '[정정] 주식의 포괄적 교환 결정', '주식의 포괄적 이전 결정',
    '주식 포괄적 교환 결정', '주식 포괄적 이전 결정',
])
def test_share_changes_require_review_and_do_not_advance_basis(context, title):
    source, calls = source_for(context, [response([filing(title=title)])])
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'review_required'
    assert context.get('share-basis:'+CODE) == EVIDENCE
    assert not context.list_prefix('share-basis-history:'+CODE)


def test_provider_maintenance_keeps_evidence_and_blocks_further_calls(context):
    source, calls = source_for(context, [{'status': '800', 'message': 'maintenance'}])
    result = asyncio.run(review_share_basis(source, CODE))
    assert result['status'] == 'error' and result['error'] == 'DartProviderUnavailable'
    assert context.get('share-basis:'+CODE) == EVIDENCE
    assert context.get('dart-provider-retry')['status'] == '800'
    asyncio.run(review_share_basis(source, CODE))
    assert len(calls) == 1


@pytest.mark.parametrize('payload', [
    response([{**filing(), 'corp_code': 'different'}]),
    response([{**filing(), 'rcept_dt': '20240901'}]),
    response(page=2), response(pages=0),
    {'status': '013', 'list': [filing()]},
])
def test_bad_scope_and_totals_never_advance(context, payload):
    source, _ = source_for(context, [payload])
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'review_required'
    assert context.get('share-basis:'+CODE) == EVIDENCE


def test_duplicate_across_pages_is_not_full_coverage(context):
    source, _ = source_for(context, [response(page=1,pages=2,count=2), response(page=2,pages=2,count=2)])
    asyncio.run(review_share_basis(source, CODE))
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'review_required'
    assert context.get('share-basis:'+CODE) == EVIDENCE


def test_corrupted_page_archive_cannot_promote(context):
    source, _ = source_for(context, [response(page=1,pages=2,count=2),
        response([filing('20251001000002')],page=2,pages=2,count=2)])
    first = asyncio.run(review_share_basis(source, CODE))
    digest = first['pages'][0]['sha256']
    (context.root/'raw/dart-share-window'/(digest+'.bin')).write_bytes(b'corrupt')
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'review_required'
    assert context.get('share-basis:'+CODE) == EVIDENCE


def test_today_cannot_be_certified_by_still_changing_disclosure_index(context):
    today = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    context.put(f'bars:{CODE}:통합:raw', {'adjustment': 'raw', 'rows': [{'date': today, 'close': 100}]})
    source, calls = source_for(context, [response()])
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'disclosure_day_not_closed'
    assert calls == []


def test_empty_proof_cannot_reuse_different_request_window(context):
    source, _ = source_for(context, [{'status': '013'}])
    job = asyncio.run(review_share_basis(source, CODE))
    changed = deepcopy(job)
    changed['pages'][0]['request']['bgn_de'] = '20200101'
    with pytest.raises(ValueError, match='request window'):
        verified_window(context, changed)


def test_long_downtime_advances_bounded_windows_instead_of_stalling(context):
    context.put(f'bars:{CODE}:통합:raw', {'adjustment': 'raw', 'rows': [{'date': '2025-12-20', 'close': 100}]})
    source, calls = source_for(context, [{'status': '013'}])
    assert asyncio.run(review_share_basis(source, CODE))['reviewed_end'] == '2025-10-31'
    assert len(calls) == 1
    assert asyncio.run(review_share_basis(source, CODE))['reviewed_end'] == '2025-12-01'
    assert calls[1]['bgn_de'] == '20251101' and calls[1]['end_de'] == '20251201'
    assert asyncio.run(review_share_basis(source, CODE))['reviewed_end'] == '2025-12-20'
    assert len(calls) == 3
    assert len(context.list_prefix('share-basis-history:'+CODE)) == 3


def test_new_verified_security_participates_without_hardcoded_code(context):
    new_code, company = '012345', '12345678'
    context.put('universe', {'rows': [{'code': CODE, 'name': '삼성전자', 'market': 'KOSPI'},
        {'code': new_code, 'name': '검증된 fixture 회사', 'market': 'KOSDAQ'},
        {'code': '999999', 'name': '미검토 fixture 회사', 'market': 'KOSPI'}]})
    context.put('dart-corporations', {'codes': {CODE: COMPANY, new_code: company, '999999': '99999999'}})
    context.put('share-basis:'+new_code, {**EVIDENCE, 'security_code': new_code, 'company_id': company})
    context.put(f'bars:{new_code}:통합:raw', {'adjustment': 'raw', 'rows': [{'date': '2025-10-02', 'close': 100}]})
    assert review_targets(context) == [CODE, new_code]
    source, calls = source_for(context, [{'status': '013'}])
    assert asyncio.run(review_share_basis(source, new_code))['status'] == 'ready'
    assert calls[0]['corp_code'] == company
    assert asyncio.run(review_share_basis(source, '999999'))['status'] == 'initial_share_unit_review_required'
    assert asyncio.run(review_share_basis(source, '987654'))['status'] == 'out_of_scope'
    assert len(calls) == 1


def test_company_mismatch_or_unverified_basis_is_not_a_review_target(context):
    context.put('share-basis:'+CODE, {**EVIDENCE, 'company_id': 'different'})
    assert review_targets(context) == []
    context.put('share-basis:'+CODE, {**EVIDENCE, 'verified': False})
    assert review_targets(context) == []


def save_count_review(store, receipt='20251001000001'):
    url = 'https://opendart.fss.or.kr/api/document.xml?rcept_no=' + receipt
    content = ('fixture_non_production reviewed unit decision ' + receipt).encode()
    digest = store.raw('fixture-unit-review', url, content)
    review = {'company_id': COMPANY, 'receipt': receipt,
        'effect': 'share_count_change_same_unit',
        'reason': 'fixture_non_production: ordinary-share count changed without a unit change',
        'source_trace': {'company_id': COMPANY, 'receipt': receipt,
                         'source_url': url, 'sha256': digest}}
    record_review(store, security_code=CODE, company_id=COMPANY,
                  review=review, raw_source='fixture-unit-review')
    return review


def test_review_revision_replays_saved_window_without_refetch_or_wait(context):
    source, calls = source_for(context, [response([filing(title='유상증자결정')])])
    blocked = asyncio.run(review_share_basis(source, CODE))
    assert blocked['status'] == 'review_required'
    assert context.get('share-basis:'+CODE) == EVIDENCE
    review = save_count_review(context)
    # The next-at time is still in the future; the explicit review is usable now.
    result = asyncio.run(review_share_basis(source, CODE))
    assert result['status'] == 'ready' and len(calls) == 1
    saved = context.get('share-basis:'+CODE)
    assert saved['end'] == '2025-10-02'
    assert saved['review_extension']['share_count_events'][0]['review'] == review
    assert list(context.list_prefix('share-basis-history:'+CODE).values()) == [EVIDENCE]


def test_same_review_revision_does_not_poll_after_old_retry_time(context):
    source, calls = source_for(context, [response([filing(title='유상증자결정')])])
    blocked = asyncio.run(review_share_basis(source, CODE))
    blocked['next_at'] = '2000-01-01T00:00:00+00:00'
    context.put('share-basis-window:'+CODE, blocked)
    for _ in range(3):
        assert asyncio.run(review_share_basis(source, CODE))['status'] == 'review_required'
    assert len(calls) == 1


def test_next_day_refetches_frozen_window_once_and_detects_new_correction(context):
    correction = filing('20251001000002', '[정정] 유상증자결정')
    source, calls = source_for(context, [response([filing(title='유상증자결정')]),
        response([filing(title='유상증자결정'), correction])])
    blocked = asyncio.run(review_share_basis(source, CODE))
    yesterday = (datetime.now(ZoneInfo('Asia/Seoul')).date() - timedelta(days=1)).isoformat()
    blocked.update(review_checked_day=yesterday, next_at='2000-01-01T00:00:00+00:00')
    context.put('share-basis-window:'+CODE, blocked)
    save_count_review(context)
    # Even if newer prices arrive, the previously blocked range must finish first.
    context.put(f'bars:{CODE}:통합:raw', {'adjustment': 'raw',
        'rows': [{'date': '2025-10-20', 'close': 101}]})
    result = asyncio.run(review_share_basis(source, CODE))
    assert result['status'] == 'review_required' and len(calls) == 2
    assert calls[1]['bgn_de'] == '20251001' and calls[1]['end_de'] == '20251002'
    assert result['pages'][0]['total_count'] == 2
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'review_required'
    assert len(calls) == 2 and context.get('share-basis:'+CODE) == EVIDENCE
    # The corrected filing must have its own decision, not inherit the first one.
    save_count_review(context, '20251001000002')
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'ready'
    assert len(calls) == 2


@pytest.mark.parametrize('damage', ['review_raw', 'disclosure_raw', 'missing_disclosure'])
def test_local_retry_rechecks_all_original_bytes(context, damage):
    source, calls = source_for(context, [response([filing(title='주식소각결정')])])
    blocked = asyncio.run(review_share_basis(source, CODE))
    review = save_count_review(context)
    if damage == 'review_raw':
        path = context.root/'raw/fixture-unit-review'/(review['source_trace']['sha256']+'.bin')
        path.write_bytes(b'fixture_non_production corrupted review')
    else:
        digest = blocked['pages'][0]['sha256']
        path = context.root/'raw/dart-share-window'/(digest+'.bin')
        if damage == 'disclosure_raw':
            path.write_bytes(b'fixture_non_production corrupted index')
        else:
            path.unlink()
            path.with_suffix('.json').unlink()
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'review_required'
    assert context.get('share-basis:'+CODE) == EVIDENCE and len(calls) == 1


def test_same_unit_review_cannot_override_a_split_disclosure(context):
    save_count_review(context)
    source, calls = source_for(context, [response([filing(title='주식분할결정')])])
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'review_required'
    assert context.get('share-basis:'+CODE) == EVIDENCE and len(calls) == 1


def test_complete_archive_can_resume_after_interruption_without_page_past_end(context):
    source, calls = source_for(context, [response([filing(title='유상증자결정')])])
    blocked = asyncio.run(review_share_basis(source, CODE))
    # Model a process stopping after the final page was persisted as partial.
    blocked.update(status='partial', next_at='2000-01-01T00:00:00+00:00')
    context.put('share-basis-window:'+CODE, blocked)
    save_count_review(context)
    assert asyncio.run(review_share_basis(source, CODE))['status'] == 'ready'
    assert len(calls) == 1
