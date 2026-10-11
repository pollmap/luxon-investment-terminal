from copy import deepcopy

import pytest

from services.market_dashboard.share_basis import extend_reviewed_history
from services.market_dashboard.split_units import valid_history

TRACE = {'source_url': 'https://example.test/issuer', 'sha256': 'a' * 64}
EVIDENCE = {
    'verified': True,
    'complete_split_history': True,
    'basis_id': '005930:split-history:201601-202606:v70',
    'security_code': '005930',
    'company_id': '00126380',
    'start': '2016-01-01',
    'end': '2026-06-30',
    'basis_date': '2026-06-30',
    'source_trace': TRACE,
    'splits': [{
        'kind': 'split', 'effective_date': '2018-05-03',
        'old_shares': '1', 'new_shares': '50',
        'verified': True, 'source_trace': TRACE,
    }],
}
PAGES = [
    {'page': 1, 'total_pages': 2, 'status': '000', 'sha256': 'b' * 64,
     'sha256_valid': True, 'returned_count': 100, 'company_id': '00126380',
     'disclosure_type': 'I'},
    {'page': 2, 'total_pages': 2, 'status': '000', 'sha256': 'c' * 64,
     'sha256_valid': True, 'returned_count': 57, 'company_id': '00126380',
     'disclosure_type': 'I'},
]
FILINGS = [{'date': '20260730', 'receipt': 'receipt-1', 'title': '현금ㆍ현물배당결정'}]


def extend(**overrides):
    args = {
        'evidence': EVIDENCE,
        'security_code': '005930',
        'company_id': '00126380',
        'target_end': '2026-10-02',
        'disclosure_end': '2026-10-03',
        'disclosure_type': 'I',
        'pages': PAGES,
        'filings': FILINGS,
        'issuer_trace': {**TRACE, 'company_id': '00126380'},
    }
    args.update(overrides)
    return extend_reviewed_history(**args)


def test_extension_requires_complete_hashed_issuer_pages_and_preserves_original():
    original = deepcopy(EVIDENCE)
    updated = extend()
    assert EVIDENCE == original
    assert updated['end'] == updated['basis_date'] == '2026-10-02'
    assert updated['basis_id'].endswith(':reviewed-through:20261002')
    assert updated['review_extension']['page_hashes'] == ['b' * 64, 'c' * 64]
    assert updated['review_extension']['filing_rows_reviewed'] == 157
    assert updated['review_extension']['share_changing_filings'] == []
    assert valid_history(updated)


@pytest.mark.parametrize('changes', [
    {'pages': PAGES[:1]},
    {'pages': [{**PAGES[0], 'sha256_valid': False}, PAGES[1]]},
    {'pages': [{**PAGES[0], 'status': '013'}, PAGES[1]]},
    {'disclosure_end': '2026-09-30'},
    {'disclosure_type': 'A'},
    {'company_id': 'wrong'},
    {'security_code': '005935'},
    {'filings': FILINGS + [{'date': '20260930', 'receipt': 'receipt-2',
                            'title': '주식분할결정'}]},
])
def test_extension_rejects_incomplete_or_conflicting_evidence(changes):
    with pytest.raises(ValueError):
        extend(**changes)


def test_extension_rejects_legacy_interval_without_complete_split_history():
    with pytest.raises(ValueError):
        extend(evidence={**EVIDENCE, 'complete_split_history': False})


def test_extension_can_continue_a_source_reviewed_no_split_interval():
    evidence = {
        **EVIDENCE,
        'complete_split_history': False,
        'no_split_interval': True,
        'basis_id': '000660:review:20260630:v69',
        'security_code': '000660',
        'company_id': '00164779',
        'start': '2021-01-01',
        'end': '2026-06-30',
    }
    updated = extend(
        evidence=evidence,
        security_code='000660',
        company_id='00164779',
        issuer_trace={**TRACE, 'company_id': '00164779'},
        filings=[{'date': '20260730', 'receipt': 'receipt-3', 'title': '현금ㆍ현물배당결정',
                 'company_id': '00164779'}],
        pages=[{**page, 'company_id': '00164779'} for page in PAGES],
    )
    assert updated['no_split_interval'] is True
    assert updated['complete_split_history'] is False
    assert updated['start'] == evidence['start']
    assert updated['end'] == '2026-10-02'
    assert updated['review_extension']['window_start'] == '2026-07-01'


def test_extension_rejects_disclosure_rows_for_another_issuer():
    with pytest.raises(ValueError, match='another issuer'):
        extend(filings=[{'date': '20260730', 'receipt': 'receipt-4',
                         'title': '현금배당결정', 'company_id': 'different'}])


def test_extension_requires_document_review_for_share_count_events():
    filing = {'date': '20260715', 'receipt': 'receipt-5',
              'title': '유상증자 또는 주식관련사채 발행결과'}
    with pytest.raises(ValueError, match='source-backed unit review'):
        extend(filings=[filing])


def test_extension_records_verified_share_count_events_without_rebasing_units():
    filing = {'date': '20260715', 'receipt': 'receipt-5',
              'title': '유상증자 또는 주식관련사채 발행결과'}
    review = {
        'company_id': '00126380',
        'receipt': 'receipt-5',
        'effect': 'share_count_change_same_unit',
        'reason': '신주 발행은 발행주식 수를 바꾸지만 보통주 1주의 액면 단위는 바꾸지 않는다.',
        'source_trace': {
            'company_id': '00126380', 'receipt': 'receipt-5',
            'source_url': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=receipt-5',
            'sha256': 'd' * 64,
        },
    }
    updated = extend(filings=[filing], filing_reviews=[review])
    event = updated['review_extension']['share_count_events'][0]
    assert event['receipt'] == 'receipt-5'
    assert event['review'] == review


def test_extension_rejects_share_unit_event_even_with_same_unit_review():
    with pytest.raises(ValueError, match='requires manual review'):
        extend(filings=[{'date': '20260730', 'receipt': 'receipt-6',
                         'title': '주식분할결정'}], filing_reviews=[{
                             'company_id': '00126380', 'receipt': 'receipt-6',
                             'effect': 'share_count_change_same_unit', 'reason': 'reviewed',
                             'source_trace': {'company_id': '00126380', 'receipt': 'receipt-6',
                                 'source_url': 'https://example.test', 'sha256': 'e' * 64},
                         }])
