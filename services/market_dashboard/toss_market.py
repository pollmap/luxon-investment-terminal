"""Cached public market catalogue. Uses the worker's one Toss authentication owner."""
import asyncio
import hashlib
import logging
from sqlalchemy.exc import OperationalError
from datetime import date, datetime
from decimal import Decimal

from .store import now
from .toss import history_terminal_receipt, numeric, proven_history_terminal
from .candle_pages import candle_clock
from .toss_contract import INDICATORS, RANK_TYPES, validate_ranking
from .market_selection import calendar_selection,exchange_selection,listing_selection

WARNING_NAMES = {'LIQUIDATION_TRADING':'정리매매', 'OVERHEATED':'단기과열',
    'INVESTMENT_WARNING':'투자경고', 'INVESTMENT_RISK':'투자위험',
    'VI_STATIC':'정적 VI', 'VI_DYNAMIC':'동적 VI', 'VI_STATIC_AND_DYNAMIC':'정적·동적 VI',
    'STOCK_WARRANTS':'신주인수권'}

INSTITUTIONS=('financialInvestment','insurance','trust','privateEquityFund','bank','otherFinancialInstitution','pensionFund')
REQUEST_POLL_SECONDS = 2
JOB_TIMEOUT_SECONDS = 45
REQUEST_METHODS = frozenset(('ranking', 'index_candles', 'index_investors', 'warnings',
    'limits', 'stocks', 'calendar', 'exchange_rate', 'listings', 'sector_catalogue',
    'sector_ranking', 'sector_detail', 'sector_stocks'))

def institution_amounts(breakdown):
    result={}
    for group in INSTITUTIONS:
        source=(breakdown or {}).get(group) or {}
        buy,sell=(numeric(source.get(k)) for k in ('buyAmount','sellAmount'))
        result[group]={'buy':buy,'sell':sell,'net':numeric(Decimal(str(buy))-Decimal(str(sell))) if buy is not None and sell is not None else None}
    return result

def request_market(store, key, operation, **params):
    old = store.get('toss-market-request:'+key, {})
    if age(old.get('requested_at')) > 30:
        try:
            store.put('toss-market-request:'+key, {'key':key, 'kind':operation, 'params':params, 'requested_at':now()})
        except (OperationalError, OSError) as exc:
            logging.getLogger(__name__).warning('Market refresh deferred: %s', type(exc).__name__)
            return False
    return True

def age(stamp):
    try:
        return (datetime.fromisoformat(now())-datetime.fromisoformat(stamp)).total_seconds()
    except (ValueError, TypeError):
        return float('inf')

def public_snapshot(store, key):
    data = store.get(key, {})
    if not data and store.get('toss-market-job:'+key,{}).get('status') == 'error':
        return {'status':'error'}
    return {k:v for k,v in data.items() if k not in ('source_trace','error','retry_after')}

class TossMarket:
    def __init__(self, collector):
        self.collector, self.client, self.store = collector, collector.client, collector.store
        self.job_locks = {}
        self.series_locks = {}

    def save(self, key, data, evidence, *, status='ready'):
        self.store.put(key, {**data, 'status':status, 'received_at':now(), 'source_trace':evidence})

    async def sector_catalogue(self):
        from .toss_sectors import collect_catalogue
        await collect_catalogue(self)

    async def sector_ranking(self, kind='MARKET_TRADING_AMOUNT', duration='1d'):
        from .toss_sectors import collect_ranking
        await collect_ranking(self, kind, duration)

    async def sector_detail(self, sector_id):
        from .toss_sectors import collect_detail
        await collect_detail(self, sector_id)

    async def sector_stocks(self, sector_id):
        from .toss_sectors import collect_stocks
        await collect_stocks(self, sector_id)

    async def ranking(self, kind='MARKET_TRADING_AMOUNT', duration='1d', exclude=False):
        validate_ranking(kind, duration)
        data, trace = await self.client.get('/api/v1/rankings', {'type':kind, 'duration':duration,
            'marketCountry':'KR', 'count':100, 'excludeInvestmentCaution':str(exclude).lower()})
        from .service_scope import universe_view
        identities = {r['code']:r for r in universe_view(self.store.get('universe',{})).get('rows',[])}
        rows = []
        for item in data['rankings']:
            code = item['symbol']
            if code not in identities or item.get('currency') != 'KRW':
                continue  # Filter ETF/ETN and unsupported instruments without relabelling ranks.
            price = item.get('price') or {}
            change = numeric(price.get('changeRate'))
            rows.append({'rank':item['rank'], 'code':code, 'name':identities[code]['name'],
                'market':identities[code].get('market'), 'price':numeric(price.get('lastPrice')),
                'change':change*100 if change is not None else None,
                'volume':numeric(item.get('tradingVolume')), 'value':numeric(item.get('tradingAmount'))})
        self.save(f'toss-ranking:{kind}:{duration}:{int(exclude)}', {'rows':rows,
            'type':kind, 'duration':duration, 'as_of':data.get('rankedAt'),
            'scope':'토스 고객 거래' if kind.startswith('TOSS_') else '시장 전체',
            'change_basis':'기간 시작' if kind in ('TOP_GAINERS','TOP_LOSERS') else '전일 종가'}, trace)

    async def indicators(self):
        data, trace = await self.client.get('/api/v1/market-indicators/prices', {'symbols':','.join(INDICATORS)})
        rows = []
        for row in data:
            if row['symbol'] not in INDICATORS:
                raise ValueError('Unknown market indicator')
            rows.append({'code':row['symbol'], 'price':numeric(row['lastPrice']), 'as_of':row.get('timestamp'),
                'unit':'%' if row['symbol'].startswith('KR_BOND_') else '포인트'})
        self.save('toss-market-indicators', {'rows':rows}, trace)

    async def index_candles(self, code, interval='1d', resume=False):
        if code not in INDICATORS or interval not in ('1m','1d') or (code.startswith('KR_BOND_') and interval != '1d'):
            raise ValueError('Unsupported indicator interval')
        key = f'toss-index:{code}:{interval}'
        previous = self.store.get(key,{})
        params = {'interval':interval, 'count':200}
        if resume and previous.get('next_before'):
            params['before'] = previous['next_before']
        data, trace = await self.client.get(f'/api/v1/market-indicators/{code}/candles',params)
        if not isinstance(data, dict) or not isinstance(data.get('candles'), list):
            raise ValueError('Index candles must be an array')
        if data.get('symbol') and data['symbol'] != code:
            raise ValueError('Index identity mismatch')
        merged = {r['time']:r for r in previous.get('rows',[])}
        observed = []
        for item in data['candles']:
            stamp = datetime.fromisoformat(item['timestamp'])
            if stamp.tzinfo is None:
                raise ValueError('Missing index timezone')
            values = {k:numeric(item.get(f)) for k,f in [('open','openPrice'),('high','highPrice'),('low','lowPrice'),('close','closePrice'),('volume','volume')]}
            if any(values[k] is None for k in ('open','high','low','close')) or values['low'] > min(values['open'],values['close']) or values['high'] < max(values['open'],values['close']):
                raise ValueError('Invalid index OHLC')
            # Bond yields can be zero or negative; do not use equity price validators.
            if params.get('before') and candle_clock(stamp.isoformat()) > candle_clock(params['before']):
                raise ValueError('Index observation exceeds requested cursor')
            row = {**values, 'time':stamp.isoformat(), 'date':stamp.date().isoformat()}
            observed.append(row)
            merged[row['time']] = row
        cursor_known = 'nextBefore' in data
        cursor = data.get('nextBefore')
        if cursor is not None:
            target = candle_clock(cursor)
            if params.get('before') and target >= candle_clock(params['before']):
                raise ValueError('Index cursor did not advance')
            if not observed or target > min(candle_clock(row['time']) for row in observed):
                raise ValueError('Index cursor skips observations')
        received_at = now()
        context = {'code': code, 'kind': 'index_candles', 'interval': interval, 'venue': 'KRX'}
        terminal = (history_terminal_receipt(trace, 'explicit_next_before_null',
                    requested=params.get('before'), rows=observed, received_at=received_at, context=context)
                    if cursor_known and cursor is None else None)
        prior_terminal = previous.get('history_terminal')
        prior_complete = bool(previous.get('backfill_complete') and
                              proven_history_terminal(prior_terminal, 'explicit_next_before_null', context))
        history_status = ('cursor_unknown' if not cursor_known else
                          'terminal_unverified' if cursor is None and terminal is None else 'ready')
        deep = resume or not previous
        self.save(key, {'rows':sorted(merged.values(),key=lambda r:r['time']),
            'next_before':cursor if deep and history_status == 'ready' else previous.get('next_before'),
            'backfill_complete':bool(terminal) if deep and history_status == 'ready' else prior_complete,
            'history_terminal':terminal if deep and history_status == 'ready' else prior_terminal,
            'history_status':history_status,
            'unit':'%' if code.startswith('KR_BOND_') else '포인트', 'venue':'KRX'}, trace,
            status=history_status)

    async def index_investors(self, code, interval='1d', resume=False):
        if code not in ('KOSPI','KOSDAQ') or interval not in ('1d','1w','1mo','1y'):
            raise ValueError('Unsupported index investor selection')
        key = f'toss-index-investors:{code}:{interval}'
        # Head refresh and history use different job keys but one stored series.
        async with self.series_locks.setdefault(key, asyncio.Lock()):
            await self._index_investors(code, interval, resume, key)

    async def _index_investors(self, code, interval, resume, key):
        previous = self.store.get(key,{})
        if resume and (not previous.get('next_until') or previous.get('backfill_complete')):
            return
        params = {'interval':interval,'count':100}
        if resume and previous.get('next_until'):
            params['until'] = previous['next_until']
        data, trace = await self.client.get(f'/api/v1/market-indicators/{code}/investor-trading',params)
        if not isinstance(data, dict) or not isinstance(data.get('records'), list):
            raise ValueError('Invalid market investor page')
        records = data['records']
        cursor_known = 'nextUntil' in data
        cursor = data.get('nextUntil')
        if cursor is not None:
            if not isinstance(cursor, str) or date.fromisoformat(cursor).isoformat() != cursor:
                raise ValueError('Invalid market investor cursor')
            if params.get('until') and cursor >= params['until']:
                raise ValueError('Market investor cursor did not advance')
            if not records:
                raise ValueError('Empty market investor page has another cursor')
        if not records and previous.get('rows') and (not resume or not cursor_known):
            # An unexpectedly empty head cannot erase history or certify its end.
            result = {**previous, 'status':'stale', 'last_response_rows':0, 'last_response_at':now()}
            if resume and not cursor_known:
                result['history_status'] = 'cursor_unconfirmed'
            self.store.put(key, result)
            return
        merged = {r['date']:r for r in previous.get('rows',[])}
        observed_dates = set()
        for row in records:
            stamp = row['date']
            if date.fromisoformat(stamp).isoformat() != stamp or stamp in observed_dates:
                raise ValueError('Invalid or duplicate market investor date')
            if params.get('until') and stamp > params['until']:
                raise ValueError('Market investor page exceeds requested date')
            observed_dates.add(stamp)
            item = {'date':row['date'], 'as_of':row.get('updatedAt')}
            for group in ('individual','foreigner','institution','otherCorporation'):
                values = row.get(group) or {}
                buy, sell = values.get('buyAmount'), values.get('sellAmount')
                item[group] = {'buy':numeric(buy),'sell':numeric(sell),
                    'net':numeric(Decimal(buy)-Decimal(sell)) if buy is not None and sell is not None else None}
            item['institution_breakdown'] = (row.get('institution') or {}).get('breakdown')
            merged[row['date']] = item
        deep = resume or not previous.get('rows')
        advance = deep and cursor_known
        self.save(key, {'rows':sorted(merged.values(),key=lambda r:r['date']), 'unit':'원','venue':'KRX',
            'last_response_rows':len(records),
            'next_until':cursor if advance else previous.get('next_until'),
            'backfill_complete':cursor is None if advance else previous.get('backfill_complete',False),
            'history_status':('exhausted' if cursor is None else 'collecting') if advance else
                'cursor_unconfirmed' if deep else previous.get('history_status'),
            'head_received_at':previous.get('head_received_at', previous.get('received_at')) if resume else now(),
            'history_received_at':now() if resume else previous.get('history_received_at')},
            trace, status='ready' if merged else 'no_data')

    async def calendar(self, country, day=None):
        key=calendar_selection(country,day)
        data, trace = await self.client.get('/api/v1/market-calendar/'+country, {'date':day} if day else None)
        self.save(key, {'calendar':data,'requested_date':day},trace)
        if country == 'KR':
            for day in data.values():
                if isinstance(day,dict) and day.get('date'):
                    self.save('market-session:'+day['date'], {'session':day},trace)

    async def exchange_rate(self, base='USD', quote='KRW', at=None):
        key,at=exchange_selection(base,quote,at)
        params={'baseCurrency':base,'quoteCurrency':quote}
        if at:
            params['dateTime']=at
        data, trace = await self.client.get('/api/v1/exchange-rate', params)
        self.save(key, {'rate':numeric(data.get('midRate')), 'buy_rate':numeric(data.get('rate')),
            'unit':'원/USD' if quote=='KRW' else 'USD/원','base':base,'quote':quote,'requested_at':at,
            'as_of':data.get('validFrom'), 'valid_until':data.get('validUntil')},trace)

    async def stocks(self, codes):
        for i in range(0,len(codes),200):
            data, trace = await self.client.get('/api/v1/stocks', {'symbols':','.join(codes[i:i+200])})
            for row in data:
                if row.get('currency') != 'KRW' or row.get('symbol') not in codes[i:i+200]:
                    continue
                self.save('toss-stock:'+row['symbol'], {'stock':row}, trace)

    async def listings(self, market, status='ACTIVE', security_type=None, common_share=None):
        key=listing_selection(market,status,security_type,common_share)
        params={'market':market,'status':status}
        if security_type:
            params['securityType']=security_type
        if common_share is not None:
            params['commonShare']=str(common_share).lower()
        data, trace = await self.client.get('/api/v1/stocks/all', params)
        known = set(self.store.listed_codes())
        self.save(key, {'rows':data,'market':market,'listing_status':status,'security_type':security_type,
            'common_share':common_share,'matched_count':sum(r['symbol'] in known for r in data),
            'unmatched':[r['symbol'] for r in data if r['symbol'] not in known]}, trace)

    async def warnings(self, code):
        data, trace = await self.client.get(f'/api/v1/stocks/{code}/warnings')
        rows = [{**r,'name':WARNING_NAMES.get(r.get('warningType'),'종목 유의사항')} for r in data]
        self.save('toss-warnings:'+code, {'rows':rows},trace)

    async def limits(self, code):
        data, trace = await self.client.get('/api/v1/price-limits', {'symbol':code})
        if data.get('currency') != 'KRW':
            raise ValueError('Unexpected price limit currency')
        self.save('toss-limits:'+code, {'upper':numeric(data.get('upperLimitPrice')),
            'lower':numeric(data.get('lowerLimitPrice')), 'as_of':data.get('timestamp'),'unit':'원'},trace)

    async def run_job(self, key, ttl, method, **params):
        # Scheduled and user-requested reads share one provider client and job.
        # Re-check freshness inside the lock after another task has completed.
        async with self.job_locks.setdefault(key, asyncio.Lock()):
            state = self.store.get('toss-market-job:'+key,{})
            if age(state.get('attempted_at')) < (ttl if state.get('status')=='ready' else min(ttl,300)):
                return
            try:
                async with asyncio.timeout(JOB_TIMEOUT_SECONDS):
                    await getattr(self,method)(**params)
                self.store.put('toss-market-job:'+key, {'status':'ready','attempted_at':now()})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.put('toss-market-job:'+key, {'status':'error','error':type(exc).__name__,'attempted_at':now()})
                # Keep last good observations, while making staleness visible.
                previous = self.store.get(key,{})
                if previous:
                    self.store.put(key,{**previous,'status':'stale'})

    def requested_jobs(self):
        jobs = []
        known = None
        requests = self.store.list_prefix('toss-market-request:').values()
        for request in requests:
            if not isinstance(request, dict) or age(request.get('requested_at')) >= 3600:
                continue
            method, params, key = request.get('kind'), request.get('params'), request.get('key')
            if not isinstance(method, str) or method not in REQUEST_METHODS or not isinstance(params, dict) or not isinstance(key, str):
                continue
            if method in ('warnings', 'limits', 'stocks'):
                if known is None:
                    known = set(self.store.listed_codes())
                if method in ('warnings', 'limits') and params.get('code') not in known:
                    continue
                if method == 'stocks':
                    if not isinstance(params.get('codes'), list):
                        continue
                    params = {**params, 'codes': [c for c in params['codes'] if isinstance(c, str) and c in known]}
                    if not params['codes']:
                        continue
            ttl = (86400 if method in ('listings', 'sector_catalogue') else
                   3600 if method in ('calendar', 'sector_detail') or
                   (method == 'exchange_rate' and params.get('at')) else 60)
            jobs.append((key, ttl, method, params))
        return jobs

    async def run_requests(self):
        # Do not wait behind the entire indicator/listing/history startup batch.
        # Provider budget locks still serialize requests within each API group.
        while True:
            try:
                for key, ttl, method, params in self.requested_jobs():
                    await self.run_job(key, ttl, method, **params)
            except (OperationalError, OSError) as exc:
                logging.getLogger(__name__).warning('Market request queue deferred: %s', type(exc).__name__)
            await asyncio.sleep(REQUEST_POLL_SECONDS)

    def pending_history_jobs(self):
        series = {f'toss-index:{code}:1d': ('index_candles', {'code':code}, 'next_before')
                  for code in INDICATORS}
        series.update({f'toss-index-investors:{code}:1d':
                       ('index_investors', {'code':code}, 'next_until') for code in ('KOSPI','KOSDAQ')})
        # Persisted user intent survives a closed page or worker restart. Only
        # requested intervals enter this bounded queue; do not fan out all pairs.
        for request in self.store.list_prefix('toss-market-request:').values():
            if not isinstance(request, dict) or request.get('kind') != 'index_investors':
                continue
            params = request.get('params')
            if not isinstance(params, dict):
                continue
            code, interval = params.get('code'), params.get('interval', '1d')
            if code not in ('KOSPI','KOSDAQ') or interval not in ('1w','1mo','1y'):
                continue
            key = f'toss-index-investors:{code}:{interval}'
            if request.get('key') != key:
                continue
            series[key] = ('index_investors', {'code':code, 'interval':interval}, 'next_until')
        jobs = []
        for key, (method, params, cursor_field) in sorted(series.items()):
            previous = self.store.get(key, {})
            if previous.get(cursor_field) and not previous.get('backfill_complete'):
                jobs.append((key, method, params))
        return jobs

    async def run_scheduled(self):
        history_cursor = 0
        while True:
            jobs = [('toss-market-indicators',30,'indicators',{}), ('toss-exchange-rate',60,'exchange_rate',{})]
            jobs += [('toss-sectors',86400,'sector_catalogue',{}),
                     ('toss-sector-ranking:MARKET_TRADING_AMOUNT:1d',60,'sector_ranking',{})]
            jobs += [('toss-calendar:'+c,3600,'calendar',{'country':c}) for c in ('KR','US')]
            jobs += [('toss-listing:'+m,86400,'listings',{'market':m}) for m in ('KOSPI','KOSDAQ','KR_ETC')]
            jobs += [(f'toss-ranking:{kind}:1d:0',60,'ranking',{'kind':kind}) for kind in RANK_TYPES]
            jobs += [(f'toss-index:{code}:1d',3600,'index_candles',{'code':code}) for code in INDICATORS]
            jobs += [(f'toss-index:{code}:1m',60,'index_candles',{'code':code,'interval':'1m'}) for code in ('KOSPI','KOSDAQ')]
            jobs += [(f'toss-index-investors:{code}:1d',900,'index_investors',{'code':code}) for code in ('KOSPI','KOSDAQ')]
            codes = list(self.store.active())
            missing = [c for c in codes if age(self.store.get('toss-stock:'+c,{}).get('received_at'))>3600]
            if missing:
                identity=hashlib.sha256(','.join(sorted(missing)).encode()).hexdigest()[:24]
                jobs.append(('toss-active-identities:'+identity,3600,'stocks',{'codes':missing}))
            for code in codes:
                # A quote prefetch or summary benchmark can mark many stocks
                # active without rendering StockWarnings. Its endpoint queues
                # warnings explicitly; do not let invisible rows consume the
                # shared trading budget needed by investor/history requests.
                jobs.append((f'toss-limits:{code}',3600,'limits',{'code':code}))
            for key, ttl, method, params in jobs:
                await self.run_job(key,ttl,method,**params)
            # One older page per turn, independently from head refresh timestamps.
            series = self.pending_history_jobs()
            if series:
                key, method, params = series[history_cursor % len(series)]
                await self.run_job(key+':history',5,method,**params,resume=True)
            history_cursor += 1
            await asyncio.sleep(5)

    async def run(self):
        tasks = [asyncio.create_task(self.run_requests()), asyncio.create_task(self.run_scheduled())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
