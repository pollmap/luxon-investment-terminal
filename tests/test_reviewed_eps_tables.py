"""fixture_non_production: table binding, ordinary columns and reconciliation."""
import hashlib
import pytest
from services.market_dashboard.reviewed_eps_tables import parse


def fixture(profit='10', shares='1,000', eps='10,000'):
    content = f'''<h1>주당이익 (연결)</h1><table>
    <tr><td>이익</td><td>{profit}</td><td>20</td><td>999</td></tr>
    <tr><td>주식 수</td><td>{shares}</td><td>1,000</td><td>999</td></tr>
    <tr><td>EPS</td><td>{eps}</td><td>20,000</td><td>999</td></tr>
    </table>'''.encode()
    binding = {'sha256':hashlib.sha256(content).hexdigest(),'title':'주당이익 (연결)',
        'years':[2026],'labels':{'profit':'이익','shares':'주식 수','eps':'EPS'},
        'url':'https://example.test/fixture','filing_id':'fixture','company_id':'fixture',
        'published_at':'2026-08-14'}
    return content, binding


def test_ordinary_columns_and_currency_scaling():
    content, binding = fixture()
    facts = parse(content, binding)
    assert len(facts) == 6
    assert facts[0]['value'] == '10000000'
    assert facts[0]['period_start'] == '2026-04-01'
    assert facts[3]['period_start'] == '2026-01-01'
    assert facts[3]['period_end'] == '2026-06-30'
    assert all(f['value'] != '999' for f in facts)
    assert all(f['published_at'] == '2026-08-14' for f in facts)


def test_changed_document_and_wrong_eps_are_rejected():
    content, binding = fixture()
    with pytest.raises(ValueError, match='document changed'):
        parse(content+b'changed', binding)
    for inputs in ({'eps':'9,000'}, {'shares':'0'}, {'profit':'-'}):
        content,binding=fixture(**inputs)
        with pytest.raises(ValueError):
            parse(content,binding)


def test_duplicate_account_cannot_choose_an_arbitrary_row():
    content,binding=fixture()
    content=content.replace(b'</table>',b'<tr><td>EPS</td><td>10,000</td><td>20,000</td></tr></table>')
    binding['sha256']=hashlib.sha256(content).hexdigest()
    with pytest.raises(ValueError,match='account row changed'):
        parse(content,binding)


@pytest.mark.asyncio
async def test_collection_is_idempotent_and_keeps_existing_company_data(tmp_path):
    from types import SimpleNamespace
    import httpx
    from services.market_dashboard.store import Store
    from services.market_dashboard.reviewed_eps_tables import collect
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005930','market':'KOSPI'}]})
    store.put('dart-corporations',{'codes':{'005930':'fixture'}})
    store.put('official:005930',{'company':{'name':'fixture_non_production'}})
    content,binding=fixture()
    binding['code']='005930'
    calls=[]
    def response(request):
        calls.append(request.url)
        return httpx.Response(200,content=content)
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        source=SimpleNamespace(store=store,client=client)
        first=await collect(source,binding)
        second=await collect(source,binding)
    assert first==second and first['inserted']==6 and len(calls)==1
    assert store.get('official:005930')['company']['name']=='fixture_non_production'
    assert len(store.get('official:005930')['xbrl_facts'])==6
