"""Annual statement trends. Original fiscal-year labels and currency are retained."""
from .analysis import numeric

ACCOUNTS = {'매출액': {'ifrs-full_Revenue','ifrs_Revenue'},
            '영업이익': {'dart_OperatingIncomeLoss'},
            '당기순이익': {'ifrs-full_ProfitLoss','ifrs_ProfitLoss'}}


def annual_timeline(store, reports, basis='CFS'):
    result=[]
    for report in sorted(reports.values(),key=lambda r:r.get('year','')):
        if report.get('status')!='ready' or report.get('basis')!=basis or report.get('report_code')!='11011': continue
        snapshot=store.get(report['snapshot_key'],{}) if report.get('snapshot_key') else report
        for name,accounts in ACCOUNTS.items():
            rows=[r for r in snapshot.get('rows',[]) if r.get('statement') in ('IS','CIS') and r.get('unit')=='KRW' and r.get('account_id') in accounts]
            values={numeric(r.get('current')) for r in rows if numeric(r.get('current')) is not None}
            if len(values)!=1: continue
            value=values.pop()
            result.append({'name':name,'period':report['year'],'frequency':'annual','basis':basis,
                           'estimate':False,'value':float(value/100000000),'unit':'억원',
                           'source_trace':rows[0].get('source_trace')})
    return {'rows':result,'basis':basis,'status':'ready' if result else 'pending'}
