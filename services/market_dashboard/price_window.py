"""Bounded chart reads. Raw observations/provenance stay in the source store."""
import calendar
from datetime import date, timedelta

from .bars import aggregate_bars, normalized_prices

RANGES = {21:1,63:3,252:12,756:36,1260:60,2600:120,100000:None}


def months_before(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return day.replace(year=year,month=month+1,day=min(day.day,calendar.monthrange(year,month+1)[1]))


def price_window(store, code, *, range_id=252, interval='day', view='chart'):
    if range_id not in RANGES or interval not in ('day','week','month') or view not in ('chart','returns'):
        raise ValueError('Invalid price window')
    snapshot={}
    for key in (f'bars:{code}:통합:day','prices:'+code):
        candidate=store.metadata(key,{})
        if candidate.get('row_count') or candidate.get('rows'):
            snapshot=candidate
            break
    reference=snapshot.get('_series_ref')
    legacy=sorted(snapshot.get('rows',[]),key=lambda r:r['date'])
    first,last=store.time_series.bounds(reference) if reference else (legacy[0]['date'],legacy[-1]['date']) if legacy else (None,None)
    # Provider rows are indexed by the full KST candle timestamp, while older
    # public snapshots use dates. Keep both representations in the same window.
    first,last=(first[:10] if first else None),(last[:10] if last else None)
    meta={k:snapshot.get(k) for k in ('venue','adjustment','source_trace','received_at','series_revision')}
    meta.update(code=code,range=range_id,interval=interval,coverage={'start':first,'end':last},unit='원')
    if not last:
        return {**meta,'rows':[],'status':'not_collected'}
    last_day=date.fromisoformat(last)

    def read(start=None,end=None,limit=None,reverse=False):
        if reference:
            return store.time_series.rows(reference,reverse,start=start,end=end+'T99' if end else None,limit=limit)
        rows=[r for r in legacy if (not start or r['date']>=start) and (not end or r['date']<=end)]
        if reverse:
            rows.reverse()
        return rows[:limit] if limit is not None else rows

    if view=='returns':
        # Five indexed reads replace a full history download for four returns.
        anchors={}
        for days in (0,7,30,90,365):
            rows=read(end=(last_day-timedelta(days=days)).isoformat(),limit=1,reverse=True)
            for row in normalized_prices(rows):
                anchors[row['date']]={'date':row['date'],'close':row['close']}
        return {**meta,'rows':sorted(anchors.values(),key=lambda r:r['date']),'status':'ready'}
    start=months_before(last_day,RANGES[range_id]).isoformat() if RANGES[range_id] else None
    if not start:
        rows=read()
    elif interval=='day':
        rows=read(end=(date.fromisoformat(start)-timedelta(days=1)).isoformat(),limit=20,reverse=True)[::-1]+read(start=start)
    else:
        # Include the whole first displayed period, then 20 observed periods.
        # Calendar weeks alone are insufficient after a long trading suspension.
        boundary=date.fromisoformat(start)
        boundary=(boundary-timedelta(days=boundary.weekday())) if interval=='week' else boundary.replace(day=1)
        earlier=[];periods=set();before=(boundary-timedelta(days=1)).isoformat()
        while len(periods)<20:
            chunk=read(end=before,limit=600,reverse=True)
            if not chunk:
                break
            earlier.extend(chunk)
            for row in normalized_prices(chunk):
                d=date.fromisoformat(row['date'])
                periods.add((d-timedelta(days=d.weekday())).isoformat() if interval=='week' else row['date'][:7])
            before=(date.fromisoformat(chunk[-1]['date'])-timedelta(days=1)).isoformat()
        rows=earlier[::-1]+read(start=boundary.isoformat())
    result=aggregate_bars(rows,interval=interval,start=start)
    result.pop('warmup',None)  # ma20 is computed above from the pre-window inputs.
    return {**meta,**result}
