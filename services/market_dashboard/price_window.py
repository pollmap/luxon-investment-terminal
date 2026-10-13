"""Bounded chart reads. Raw observations/provenance stay in the source store."""
import calendar
from datetime import date, timedelta

from .bars import aggregate_bars, normalized_prices
from .price_history import history_coverage, price_bounds, read_prices, select_price_history

RANGES = {21:1,63:3,252:12,756:36,1260:60,2600:120,100000:None}


def months_before(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return day.replace(year=year,month=month+1,day=min(day.day,calendar.monthrange(year,month+1)[1]))


def price_window(store, code, *, range_id=252, interval='day', view='chart', venue='auto', adjustment='source'):
    if range_id not in RANGES or interval not in ('day','week','month') or view not in ('chart','returns'):
        raise ValueError('Invalid price window')
    snapshot, selection = select_price_history(store, code, venue=venue, adjustment=adjustment)
    first,last=price_bounds(store,snapshot)
    meta={k:snapshot.get(k) for k in ('venue','adjustment','source_trace','received_at','series_revision')}
    meta.update(code=code,range=range_id,interval=interval,coverage={'start':first,'end':last},unit='원')
    if not last:
        return {**meta,'rows':[],'status':'not_collected',
                'history_coverage':history_coverage(store,snapshot,selection=selection)}
    last_day=date.fromisoformat(last)

    def read(start=None,end=None,limit=None,reverse=False):
        return read_prices(store,snapshot,start=start,end=end,limit=limit,reverse=reverse)

    if view=='returns':
        # Five indexed reads replace a full history download for four returns.
        anchors={}
        for days in (0,7,30,90,365):
            rows=read(end=(last_day-timedelta(days=days)).isoformat(),limit=1,reverse=True)
            for row in normalized_prices(rows):
                anchors[row['date']]={'date':row['date'],'close':row['close']}
        rows=sorted(anchors.values(),key=lambda r:r['date'])
        return {**meta,'rows':rows,'status':'ready', 'history_coverage':history_coverage(
            store,snapshot,requested_start=(last_day-timedelta(days=365)).isoformat(),
            requested_end=last,displayed=rows,selection=selection)}
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
    return {**meta,**result,'history_coverage':history_coverage(
        store,snapshot,requested_start=start,requested_end=last,
        displayed=result['rows'],selection=selection)}
