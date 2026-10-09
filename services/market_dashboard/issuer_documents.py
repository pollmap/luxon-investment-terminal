"""Company documents shared only through reviewed security identities.

Company documents and financial totals can share a reviewed issuer. Prices,
per-share facts and security-specific corporate events remain independent.
"""
from copy import deepcopy
from .issuer_dividends import SYMBOLS, IDENTITY_URL, VERSION

REVIEWED = {
    **{code:('005380',name,IDENTITY_URL,VERSION) for code,(name,_) in SYMBOLS.items() if code!='005380'},
    '005935':('005930','삼성전자우',
        'https://www.samsung.com/global/ir/stock-information/listing-Info/',
        'samsung-listed-security-v1'),
}
ISSUER_NAMES = {'005380':'현대차','005930':'삼성전자'}


def company_link(store, code, corporations=None):
    corporations = corporations if corporations is not None else store.get('dart-corporations',{}).get('codes',{})
    if corporations.get(code) or code not in REVIEWED:
        return None
    issuer,name,url,version = REVIEWED[code]
    listed = {r['code']:r.get('name') for r in store.get('universe',{}).get('rows',[])}
    if not corporations.get(issuer) or listed.get(code)!=name or listed.get(issuer)!=ISSUER_NAMES[issuer]:
        return None
    return {'issuer_security_code':issuer,'security_code':code,
            'source_url':url,'version':version,'scope':'company_documents_only'}


def report_dependencies(store, code):
    link=company_link(store,code)
    return list(dict.fromkeys(['official:'+code,'universe','dart-corporations']+
        (['official:'+link['issuer_security_code']] if link else [])))


def company_report(snapshot, link):
    """Expose issuer totals without relabelling ordinary per-share rows."""
    if not link:
        return snapshot
    def per_share(row):
        name=str(row.get('name') or '').upper()
        account=str(row.get('account_id') or '').upper()
        return ('주당' in name or name in {'EPS','BPS','PER','PBR'} or
                'PER SHARE' in name or 'PERSHARE' in account or
                '/SHARE' in str(row.get('unit') or '').upper())
    rows=[row for row in snapshot.get('rows',[]) if not per_share(row)]
    return {**snapshot,'rows':rows,'issuer_link':{**link,'scope':'company_financial_totals'},
            'excluded_per_share_rows':len(snapshot.get('rows',[]))-len(rows)}


def company_profile(source, own, link):
    """Share corporate facts, keeping security rights and dividend rows separate."""
    result=dict(own or {})
    if source.get('company'):
        result.update(company=source['company'],company_source_trace=source.get('source_trace'),issuer_link=link)
    sections=dict((result.get('report_facts') or {}).get('sections') or {})
    corporate={'shareholders','employees','executives','minority_shareholders','holder_changes','auditor','investments'}
    for key,value in (source.get('report_facts') or {}).get('sections',{}).items():
        if key in corporate:
            sections[key]={**value,'issuer_link':link}
    if sections:
        result['report_facts']={**(result.get('report_facts') or {}),'sections':sections}
    return result or None


def document_issuer(store, code, corporations):
    if corporations.get(code):
        return code
    link=company_link(store,code,corporations)
    return link['issuer_security_code'] if link else None


def project_documents(store, code, issuer):
    """Persist exact issuer documents with a separate security mapping trace."""
    link=company_link(store,code)
    if not link or link['issuer_security_code']!=issuer:
        raise ValueError('Unreviewed company document projection')
    filings = store.get('filings:' + issuer, {})
    # An empty/error response must not erase existing good disclosures.
    if filings.get('rows'):
        store.put('filings:' + code, {**deepcopy(filings), 'issuer_link': link})
    source = store.get('official:' + issuer, {})
    if source.get('company'):
        own = store.get('official:' + code, {})
        # Retain any independently collected financials and their provenance.
        own['company'] = deepcopy(source['company'])
        own['company_source_trace'] = deepcopy(source.get('source_trace', {}))
        own['company_received_at'] = source.get('company_received_at')
        own['issuer_link'] = link
        store.put('official:' + code, own)
