from services.market_dashboard.source_support import diagnose_not_found
from services.market_dashboard.store import Store,now


def test_complete_provider_catalogue_omission_is_distinct_and_revision_bound(tmp_path):
    s=Store('sqlite://',tmp_path)
    s.put('universe',{'rows':[{'code':'052960'}]})
    assert diagnose_not_found(s,'052960','history')['status']=='catalogue_check_pending'
    for market in ('KOSPI','KOSDAQ','KR_ETC'):
        s.put('toss-listing:'+market,{'status':'ready','received_at':now(),'rows':[],
                                    'source_trace':{'source':'fixture_non_production'}})
    result=diagnose_not_found(s,'052960','history')
    assert result['status']=='provider_catalogue_omission' and not result['retryable']
    assert result['catalogue_revision']
    assert len(result['catalogue_evidence'])==3
    assert all('source_trace' not in item for item in result['catalogue_evidence'])
    assert list(s.listed_codes())==['052960']
    s.put('toss-listing:KR_ETC',{'status':'ready','received_at':now(),'rows':[{'symbol':'052960'}],
                             'source_trace':{'source':'fixture_non_production'}})
    updated=diagnose_not_found(s,'052960','history')
    assert updated['status']=='endpoint_or_parameters' and updated['retryable']
    assert updated['catalogue_revision'] != result['catalogue_revision']
