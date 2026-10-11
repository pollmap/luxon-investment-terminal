"""fixture_non_production: source identity and explicit reviewer decisions."""
import json
from copy import deepcopy

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from services.market_dashboard.share_basis_review_store import (
    load_reviews,
    record_review,
    review_index_key,
    review_revision,
)
from services.market_dashboard.store import Store

CODE, COMPANY, RECEIPT = '005930', '00126380', '20251001000001'
SOURCE = 'fixture-review-source'


def review_source(store, receipt=RECEIPT, content=b'fixture_non_production original document'):
    url = 'https://opendart.fss.or.kr/api/document.xml?rcept_no=' + receipt
    digest = store.raw(SOURCE, url, content)
    return {'company_id': COMPANY, 'receipt': receipt,
            'effect': 'share_count_change_same_unit',
            'reason': 'fixture_non_production: reviewed ordinary-share count change only',
            'source_trace': {'company_id': COMPANY, 'receipt': receipt,
                             'source_url': url, 'sha256': digest}}


@pytest.fixture
def context(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.put('dart-corporations', {'codes': {CODE: COMPANY, '005935': COMPANY}})
    return store


def save(store, review, **kwargs):
    return record_review(store, security_code=CODE, company_id=COMPANY,
                         review=review, raw_source=SOURCE, **kwargs)


def load(store, receipts=None):
    return load_reviews(store, security_code=CODE, company_id=COMPANY,
                        receipts=receipts or [RECEIPT])


def test_legacy_review_shape_is_preserved_and_replay_does_not_create_verification(context):
    # Matches the inner review in the existing v83 extension evidence. Its
    # effect/reason is a supplied reviewer decision, not inferred from the title.
    review = review_source(context)
    original = deepcopy(review)
    key = save(context, review)
    revision = review_revision(context, CODE)
    assert save(context, review) == key
    assert review_revision(context, CODE) == revision
    assert load(context) == [original] and review == original
    assert 'verified' not in load(context)[0]
    assert not context.list_prefix('share-basis:')
    assert not context.list_prefix('official:')


def test_review_keys_fit_actual_postgres_schema_and_enforced_length_limit(context):
    limit = context.snapshots.c.key.type.length
    assert limit == 128
    ddl = str(CreateTable(context.snapshots).compile(dialect=postgresql.dialect()))
    assert 'VARCHAR(128)' in ddl
    # SQLite does not enforce VARCHAR length. Reproduce the production length
    # constraint locally instead of treating a SQLite write as PostgreSQL proof.
    with context.engine.begin() as db:
        db.exec_driver_sql('''CREATE TRIGGER fixture_snapshot_key_limit
            BEFORE INSERT ON market_snapshots WHEN length(NEW.key) > 128
            BEGIN SELECT RAISE(ABORT, 'fixture VARCHAR(128) exceeded'); END''')
    key = save(context, review_source(context))
    assert len(key) == 104 and len(key) <= limit
    assert len(review_index_key(CODE)) <= limit
    assert load(context)[0]['receipt'] == RECEIPT
    with pytest.raises(IntegrityError, match='VARCHAR'):
        context.put('x' * (limit + 1), {'fixture_non_production': True})


@pytest.mark.parametrize('field,value', [('company_id', '12345678'), ('source_hash', 'a' * 64)])
def test_short_key_still_binds_company_and_source_digest(context, field, value):
    key = save(context, review_source(context))
    envelope = context.get(key)
    envelope[field] = value
    context.put(key, envelope)
    with pytest.raises(ValueError, match='identity changed'):
        load(context)


@pytest.mark.parametrize('code', ['0001A0', '0004V0'])
def test_uppercase_alphanumeric_security_still_requires_company_mapping(context, code):
    context.put('universe', {'rows': [{'code': code, 'market': 'KOSDAQ',
                                     'name': 'fixture_non_production'}]})
    context.put('dart-corporations', {'codes': {code: COMPANY}})
    review = review_source(context)
    key = record_review(context, security_code=code, company_id=COMPANY,
                        review=review, raw_source=SOURCE)
    assert len(key) == 104
    assert load_reviews(context, security_code=code, company_id=COMPANY,
                        receipts=[RECEIPT]) == [review]
    context.put('dart-corporations', {'codes': {code: '12345678'}})
    with pytest.raises(ValueError, match='company do not match'):
        load_reviews(context, security_code=code, company_id=COMPANY, receipts=[RECEIPT])


@pytest.mark.parametrize('code', ['0001a0', '../foo', '005930/', '0001A00', '999999'])
def test_invalid_or_unknown_security_cannot_enter_review_store(context, code):
    with pytest.raises(ValueError):
        record_review(context, security_code=code, company_id=COMPANY,
                      review=review_source(context), raw_source=SOURCE)
    assert not context.list_prefix('share-unit-review')


@pytest.mark.parametrize('change', [
    {'company_id': '12345678'}, {'security_code': '005935'},
    {'receipt': '20251001000002'}, {'effect': 'split'}, {'reason': ''},
    {'verified': False}, {'status': 'candidate'}, {'share_class': 'preferred'},
    {'dimensions': {'ClassesOfShareCapitalAxis': 'PreferenceSharesMember'}},
    {'dimensions': {'BusinessSegmentsAxis': 'segment'}},
])
def test_wrong_identity_unreviewed_and_fact_dimension_inputs_are_rejected(context, change):
    review = {**review_source(context), **change}
    with pytest.raises(ValueError):
        save(context, review)
    assert not context.list_prefix('share-unit-review')


@pytest.mark.parametrize('change', [
    {'company_id': '12345678'}, {'receipt': '20251001000002'},
    {'filing_id': '20251001000002'}, {'security_code': '005935'},
    {'source_url': 'https://opendart.fss.or.kr/api/document.xml?rcept_no=20251001000002'},
    {'source_url': 'https://example.test/document.xml?rcept_no=' + RECEIPT},
    {'source_url': ('https://opendart.fss.or.kr/api/document.xml?rcept_no='
                    + RECEIPT + '&crtfc_key=fixture')},
    {'sha256': 'a' * 64}, {'dimensions': {'SomeAxis': 'SomeMember'}},
])
def test_source_company_receipt_hash_and_archive_url_are_bound(context, change):
    review = review_source(context)
    review['source_trace'].update(change)
    with pytest.raises(ValueError):
        save(context, review)


@pytest.mark.parametrize('damage', ['bytes', 'missing', 'url', 'envelope', 'pointer'])
def test_every_read_revalidates_original_bytes_and_immutable_envelope(context, damage):
    review = review_source(context)
    key = save(context, review)
    digest = review['source_trace']['sha256']
    directory = context.root / 'raw' / SOURCE
    if damage == 'bytes':
        (directory / (digest + '.bin')).write_bytes(b'fixture_non_production corrupted')
    elif damage == 'missing':
        (directory / (digest + '.bin')).unlink()
        (directory / (digest + '.json')).unlink()
    elif damage == 'url':
        path = directory / (digest + '.json')
        meta = json.loads(path.read_bytes())
        path.write_text(json.dumps({**meta, 'url': 'https://example.test/wrong'}))
    elif damage == 'envelope':
        value = context.get(key)
        value['review']['reason'] = 'unreviewed edit'
        context.put(key, value)
    else:
        value = context.get(review_index_key(CODE))
        value['entries'][RECEIPT] = 'share-unit-review:another-company'
        context.put(review_index_key(CODE), value)
    with pytest.raises(ValueError):
        load(context)


def test_new_correction_receipt_never_inherits_the_old_review(context):
    original = review_source(context)
    original_key = save(context, original)
    correction_receipt = '20251002000002'
    assert load(context, [correction_receipt]) == []
    correction = review_source(context, correction_receipt, b'fixture_non_production correction')
    correction_key = save(context, correction)
    assert correction_key != original_key
    assert load(context, [RECEIPT, correction_receipt]) == [original, correction]
    assert context.get(original_key)['review'] == original


def test_revised_bytes_require_explicit_replacement_and_preserve_prior_review(context):
    original = review_source(context)
    prior_key = save(context, original)
    prior_revision = review_revision(context, CODE)
    fresh = review_source(context, content=b'fixture_non_production revised same receipt')
    with pytest.raises(ValueError, match='exact previous'):
        save(context, fresh)
    key = save(context, fresh, replaces=prior_key)
    assert key != prior_key and review_revision(context, CODE) != prior_revision
    assert context.get(prior_key)['review'] == original
    assert load(context) == [fresh]


def test_security_scope_and_archive_path_cannot_be_reused(context):
    review = review_source(context)
    save(context, review)
    assert load_reviews(context, security_code='005935', company_id=COMPANY,
                        receipts=[RECEIPT]) == []
    with pytest.raises(ValueError, match='company do not match'):
        record_review(context, security_code=CODE, company_id='12345678',
                      review=review, raw_source=SOURCE)
    with pytest.raises(ValueError, match='archive name'):
        record_review(context, security_code=CODE, company_id=COMPANY,
                      review=review, raw_source='../outside')
