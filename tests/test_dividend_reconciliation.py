"""Fixture data tests only; not evidence of historical payments."""
from copy import deepcopy
from services.market_dashboard.dividend_evidence import enrich_dividends
from services.market_dashboard.performance import performance


def event():
    return dict(id='filing',kind='dividend',security_code='005930',share_class='ordinary',
                record_date='2025-06-30',amount='100',currency='KRW',
                payment_date='2025-08-20',scheduled_payment_date='2025-08-20',
                payment_status='announced',verified=False,source_trace={'source':'fixture_non_production'})


def observation(**changes):
    return {**event(),'id':'payment-confirmation','reviewed':True,'ex_date':'2025-06-27',
            'confirmed_payment_date':'2025-08-21','source_trace':{'source':'fixture_non_production',
            'source_url':'https://example.test/payment','sha256':'a'*64,'available_at':'2025-08-22'},**changes}


def test_actual_payment_overrides_schedule_with_field_evidence_and_no_source_mutation():
    original=event(); copy=deepcopy(original)
    row=enrich_dividends([original],[observation()])[0]
    assert original==copy
    assert row['verified'] and row['payment_date']=='2025-08-21'
    assert row['scheduled_payment_date']=='2025-08-20'
    assert row['field_evidence']['payment_date'][0]['observation_id']=='payment-confirmation'


def test_other_class_amount_and_future_confirmation_cannot_unlock_calculation():
    for obs in [observation(share_class='preferred'),observation(amount='101'),
                observation(reviewed=False),observation(confirmed_payment_date='2025-08-23')]:
        assert not enrich_dividends([event()],[obs])[0]['verified']


def test_conflicting_dates_block_and_distinct_source_ids_do_not_double_pay():
    a=observation();b=observation(id='other-source',ex_date='2025-06-26')
    row=enrich_dividends([event()],[a,b])[0]
    assert not row['verified'] and row['status']=='conflicting_dividend_evidence'
    row=enrich_dividends([event()],[a])[0]
    prices={'adjustment':'raw','venue':'KRX','rows':[
        {'date':'2025-06-26','close':1000},{'date':'2025-06-27','close':900},
        {'date':'2025-08-21','close':1000},{'date':'2025-08-22','close':1000}]}
    events={'venue':'KRX','coverage':{'verified':True,'start':'2025-01-01','end':'2025-12-31'},
            'rows':[row,{**row,'id':'duplicate-source'}]}
    assert performance(prices,events,code='005930')['status']=='duplicate_dividend_right_requires_review'
    events['rows']=[row]
    result=performance(prices,events,code='005930')
    assert result['status']=='ready' and result['total_return']==10
    assert result['ledger'][-1]['date']=='2025-08-22'


def test_partial_replay_cannot_clear_previously_conflicting_evidence():
    row=enrich_dividends([event()],[observation(),observation(id='other',ex_date='2025-06-26')])[0]
    replay=enrich_dividends([row],[])[0]
    assert replay['verified'] is False
    assert replay['status']=='conflicting_dividend_evidence'
    assert replay['evidence_conflicts']==['ex_date']


def test_confirmation_label_without_payment_date_is_not_verified():
    row={**event(),'ex_date':'2025-06-27','payment_date':None,'payment_status':'confirmed'}
    result=enrich_dividends([row],[])[0]
    assert result['verified'] is False
    assert result['status']=='payment_confirmation_required'
    assert result['scheduled_payment_date']=='2025-08-20'
    # Never fill the missing actual date from the scheduled date.
    assert result['payment_date'] is None


def test_invalid_existing_dates_are_not_promoted_by_confirmation_status():
    for patch in ({'ex_date':'2025-07-01'}, {'payment_date':'2025-06-01'}, {'ex_date':'not-a-date'}):
        row={**event(),'ex_date':'2025-06-27','payment_status':'confirmed',**patch}
        result=enrich_dividends([row],[])[0]
        assert result['verified'] is False
        assert result['status']=='dividend_dates_require_review'
