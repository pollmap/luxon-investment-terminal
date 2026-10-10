"""fixture_non_production: issuer review boundaries; no operational approval."""

import hashlib
import json
from copy import deepcopy

import pytest

from ops.market import review_jyp_eps as review


def fixture_rows():
    return [['', '보통주'], ['지배기업의 보통주에 귀속되는 당기순이익(손실)', '10,000'],
            ['가중평균유통보통주식수(단위 : 주)', '100'], ['기본주당이익(손실)', '100']]


def test_fixture_values_reconcile_without_share_approval():
    rows = fixture_rows()
    result = review.parse_eps_rows(rows, rows, [('2025-01-01', '2025-12-31')])
    assert [str(v) for v in result[0][2:]] == ['10000', '100', '100']


@pytest.mark.parametrize('damage', ['preferred', 'missing', 'changed', 'period', 'rounding'])
def test_wrong_share_class_missing_or_changed_fact_cannot_pass(damage):
    expected = fixture_rows()
    rows = deepcopy(expected)
    periods = [('2025-01-01', '2025-12-31')]
    if damage == 'preferred':
        rows[0][1] = '우선주'
        expected = deepcopy(rows)  # Even a matching binding cannot change semantic class.
    elif damage == 'missing':
        rows[-2][1] = '-'
        expected = deepcopy(rows)
    elif damage == 'changed':
        rows[1][1] = '11,000'
    elif damage == 'period':
        periods = [('2025-12-01', '2025-12-31')]
    else:
        rows[-1][1] = '99'
        expected = deepcopy(rows)
    with pytest.raises(ValueError):
        review.parse_eps_rows(rows, expected, periods)


def test_binding_manifest_is_fixed_and_company_scoped():
    binding = review.load_manifest()
    assert binding['code'] == '035900' and binding['company_id'] == '00258689'
    assert binding['start'] == '2025-01-01' and binding['end'] == '2026-06-30'
    assert len(binding['documents']) == 22
    assert all('dart.fss.or.kr' in d['url'] for d in binding['documents'])


def test_original_hash_and_receipt_are_both_required(tmp_path):
    raw = b'<html>fixture_non_production</html>'
    name = '20260318001519-54.html'
    (tmp_path / name).write_bytes(raw)
    doc = {'file': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
           'url': 'https://dart.fss.or.kr/report/viewer.do?rcpNo=20260318001519'}
    assert review.read_document(tmp_path, doc)
    with pytest.raises(ValueError, match='identity'):
        review.read_document(tmp_path, {**doc, 'url': doc['url'].replace('20260318', '20260319')})
    (tmp_path / name).write_bytes(raw.replace(b'fixture', b'changed'))
    with pytest.raises(ValueError, match='hash'):
        review.read_document(tmp_path, doc)


def test_company_mapping_rejected_before_original_read(tmp_path):
    metadata = {'code': '035900', 'readonly': True, 'snapshots': {
        'dart-corporations': {'value': {'company_id': '00106641'}}}}
    with pytest.raises(ValueError, match='mapping'):
        review.prepare(tmp_path, metadata)


def test_binding_replacement_and_path_escape_are_rejected(tmp_path, monkeypatch):
    manifest = tmp_path / 'changed.json'
    manifest.write_text(json.dumps({'fixture_non_production': True}))
    monkeypatch.setattr(review, 'MANIFEST', manifest)
    with pytest.raises(ValueError, match='binding'):
        review.load_manifest()
    with pytest.raises(ValueError, match='path'):
        review.read_document(tmp_path, {'file': '../other.html'})
