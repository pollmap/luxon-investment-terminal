"""Toss market data only. One worker owns authentication and shared subscriptions."""
import asyncio
import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from .collection import record_trading_collection, trading_collection_receipt
from .providers import trace
from .store import now
from .time_series import put_series
from .toss_contract import contract_path, request_group, source_scope
from .request_fairness import FairRequestGate, current_workload, request_workload

BASE = 'https://openapi.tossinvest.com'
STREAM_URL = 'wss://openapi-ws.tossinvest.com/ws/v1'


def history_terminal_receipt(evidence, field, *, requested, rows, received_at, context):
    """An explicit null is terminal only with a durable original reference."""
    digest = evidence.get('sha256') or evidence.get('source_document_id')
    if (evidence.get('source') != 'toss' or not isinstance(digest, str)
            or not re.fullmatch('[0-9a-f]{64}', digest)
            or any(evidence.get(alias) not in (None, digest)
                   for alias in ('sha256', 'source_document_id'))):
        return None
    return {**context, field: True, 'received_at': received_at, 'request_cursor': requested,
            'rows': len(rows), 'source_document_id': digest, 'sha256': digest, 'source': 'toss'}


def proven_history_terminal(receipt, field, context):
    """Legacy flags or another symbol's receipt cannot certify this series."""
    return (isinstance(receipt, dict) and receipt.get(field) is True
            and all(receipt.get(key) == value for key, value in context.items())
            and history_terminal_receipt(receipt, field, requested=None, rows=(),
                                         received_at=None, context=context) is not None)


def calendar_previous_close(store, keys, trade_day, prior_day):
    """Confirm adjacent integrated sessions from archived official calendar slots."""
    from .raw_archive import read_original_verified

    def open_day(day):
        periods = day.get('integrated')
        if not isinstance(periods, dict):
            return False
        found = False
        for name in ('preMarket', 'regularMarket', 'afterMarket'):
            if name not in periods:
                return False
            period = periods[name]
            if period is None:
                continue
            start = datetime.fromisoformat(period['startTime'])
            end = datetime.fromisoformat(period['endTime'])
            if (start.tzinfo is None or end.tzinfo is None or start >= end
                    or start.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat() != day['date']
                    or end.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat() != day['date']):
                return False
            found = True
        return found

    proven = False
    for key in keys:
        try:
            snapshot = store.metadata(key, {})
            # Legacy calendar snapshots have no status. An explicit review or
            # collection failure must not keep certifying its retained raw proof.
            if snapshot.get('status') not in (None, 'ready', 'verified'):
                continue
            evidence = snapshot.get('source_trace', {})
            digest = evidence.get('source_document_id')
            if (evidence.get('source') != 'toss' or not isinstance(digest, str)
                    or not re.fullmatch('[0-9a-f]{64}', digest)
                    or any(evidence.get(alias, digest) != digest
                           for alias in ('sha256', 'filing_id'))):
                continue
            directory = store.root / 'raw/toss'
            envelope = directory / (digest + '.json')
            if envelope.is_symlink() or directory.is_symlink() or envelope.stat().st_size > 262144:
                continue
            metadata = json.loads(envelope.read_bytes())
            if type(metadata.get('bytes')) is not int or not 0 < metadata['bytes'] <= 262144:
                continue
            calendar = json.loads(read_original_verified(directory, digest))['result']
            today, before, after = (calendar[name] for name in
                                    ('today', 'previousBusinessDay', 'nextBusinessDay'))
            today_day, before_day, after_day = (date.fromisoformat(day['date'])
                                                for day in (today, before, after))
            if (not before_day < today_day < after_day or any(day_date.isoformat() != day['date']
                    for day, day_date in ((today, today_day), (before, before_day),
                                          (after, after_day)))):
                continue
            url = metadata.get('url')
            if (url not in (BASE + '/api/v1/market-calendar/KR',
                           BASE + '/api/v1/market-calendar/KR?date=' + today['date'])
                    or evidence.get('source_url') != url):
                continue
            if key.startswith('market-session:'):
                session = snapshot.get('session')
                if (not isinstance(session, dict) or key != 'market-session:' + session['date']
                        or session not in (today, before, after)):
                    continue
            elif (calendar != snapshot.get('calendar') or (key != 'toss-calendar:KR'
                    and key != 'toss-calendar:KR:' + today['date'])):
                continue
            if not open_day(before) or not open_day(after):
                continue
            # A later official session inside the gap contradicts an older
            # previous/next-business relation, even when that older raw is valid.
            for day, day_date in ((before, before_day), (today, today_day), (after, after_day)):
                if prior_day < day_date < trade_day and open_day(day):
                    return False
            if today_day == trade_day:
                if before_day == prior_day and open_day(today):
                    proven = True
            elif after_day == trade_day:
                if today_day == prior_day and open_day(today):
                    proven = True
                if (before_day == prior_day and 'integrated' in today
                        and today['integrated'] is None):
                    proven = True
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # A missing/corrupt original cannot erase the observed current price.
            continue
    return proven


TRADING_PROJECTION_FIELDS = {
    'investor-trading': {'toss-normalized:flows:': ('rows', 'status'), 'detail:': ('flows',)},
    'credit-trades': {'toss-normalized:financing:': ('credit', 'stock_loan'),
                      'financing:': ('credit', 'stock_loan')},
    'securities-lending': {'toss-normalized:financing:': ('lending',), 'financing:': ('lending',)},
    'short-selling': {'toss-normalized:short-selling:': ('trades',), 'short-selling:': ('trades',)},
    'program-trades': {'trading:': ('program', 'program_status', 'venue', 'program_trace')},
}


def projection_field_hash(payload, fields):
    """Fingerprint only owned fields; preserve row order, values and evidence."""
    if not isinstance(payload, dict) or any(field not in payload for field in fields):
        return None
    content = {field: payload[field] for field in fields}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def valid_stream_data(kind, data):
    """Unknown currencies remain unsupported, never converted into KRW quotes."""
    if kind not in ('trade','orderbook') or not isinstance(data, dict) or data.get('currency') != 'KRW':
        return False
    try:
        stamp = data.get('timestamp')
        if stamp is not None and datetime.fromisoformat(stamp).tzinfo is None:
            return False
        if kind == 'trade':
            price, volume = numeric(data.get('price')), numeric(data.get('volume'))
            return bool(stamp and price is not None and price > 0 and volume is not None and volume >= 0)
        for side in ('asks','bids'):
            if not isinstance(data.get(side), list):
                return False
            for level in data[side]:
                price, volume = numeric(level.get('price')), numeric(level.get('volume'))
                if price is None or price <= 0 or volume is None or volume < 0:
                    return False
        return True
    except (ValueError, TypeError, AttributeError):
        return False

def retry_after_seconds(value):
    try:
        seconds=float(value)
        if not Decimal(str(seconds)).is_finite():
            return 5.0
        return max(1.0,seconds)
    except (ValueError,TypeError):
        try:
            return max(1.0,parsedate_to_datetime(value).timestamp()-time.time())
        except (ValueError,TypeError,OverflowError):
            return 5.0


def numeric(value):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError('Non-finite market value')
        return int(number) if number == number.to_integral_value() else float(number)
    except InvalidOperation as exc:
        raise ValueError('Invalid market value') from exc


def head_age_seconds(snapshot, clock):
    """Latest-page receipt age; an old-page receipt is not a head refresh.

    Only legacy snapshots without either split clock use received_at. An
    explicit missing/invalid head stays unknown even when history arrived now.
    This is a retry clock: data status/as-of remain independent (including an
    empty head response that retained stale values).
    """
    stamp = snapshot.get('head_received_at')
    if 'head_received_at' not in snapshot and not snapshot.get('history_received_at'):
        stamp = snapshot.get('received_at')
    try:
        start, end = datetime.fromisoformat(stamp), datetime.fromisoformat(clock)
        if start.tzinfo is None or end.tzinfo is None or start > end:
            return float('inf')
        return (end - start).total_seconds()
    except (TypeError, ValueError):
        return float('inf')


def normalize_candles(items):
    if not isinstance(items, list):
        raise ValueError('Candle records must be an array')
    rows = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Candle observation must be an object')
        stamp = datetime.fromisoformat(item['timestamp'])
        if stamp.tzinfo is None or item.get('currency', 'KRW') != 'KRW':
            raise ValueError('Unknown candle timezone or currency')
        stamp = stamp.astimezone(ZoneInfo('Asia/Seoul'))
        row = {key: numeric(item.get(field)) for key, field in (
            ('open', 'openPrice'), ('high', 'highPrice'), ('low', 'lowPrice'),
            ('close', 'closePrice'), ('volume', 'volume'))}
        if any(row[k] is None or row[k] <= 0 for k in ('open', 'high', 'low', 'close')):
            raise ValueError('Incomplete OHLC')
        if row['low'] > min(row['open'], row['close']) or row['high'] < max(row['open'], row['close']):
            raise ValueError('Invalid OHLC bounds')
        if row['volume'] is not None and row['volume'] < 0:
            raise ValueError('Negative volume')
        row.update(time=stamp.isoformat(), date=stamp.date().isoformat(), ohlc_valid=True)
        if row['time'] in rows and rows[row['time']] != row:
            raise ValueError('Conflicting candle observations')
        rows[row['time']] = row
    return sorted(rows.values(), key=lambda r: r['time'])


def normalize_book(data):
    asks = sorted(data.get('asks', []), key=lambda r: Decimal(r['price']))
    bids = sorted(data.get('bids', []), key=lambda r: Decimal(r['price']), reverse=True)
    result = []
    for i in range(max(len(asks), len(bids))):
        row = {'level': i+1}
        for side, values in (('ask', asks), ('bid', bids)):
            item = values[i] if i < len(values) else {}
            row[side], row[side+'_quantity'] = numeric(item.get('price')), numeric(item.get('volume'))
            if any(v is not None and v < 0 for v in (row[side], row[side+'_quantity'])):
                raise ValueError('Negative orderbook value')
        result.append(row)
    return result


class TossClient:
    def __init__(self, store, *, client=None):
        self.store = store
        self.client = client or httpx.AsyncClient(timeout=25)
        self.lock = asyncio.Lock()
        self.request_lock = asyncio.Lock()
        self.last_request = 0.0
        self.group_locks = {}
        self.group_deadlines = {}
        self.token_path = Path(os.getenv('TOSS_TOKEN_FILE', str(Path.home()/'.codex/private-runtime/korea-stocks/toss-token.json')))
        self.metrics_session = uuid4().hex
        self.metrics_started_at = now()
        self.request_metrics = {}
        self.last_success = {}

    def _metrics(self, template):
        return self.request_metrics.setdefault(template, {
            'request_count': 0, 'successful_responses': 0,
            'http_errors': 0, 'network_errors': 0, 'protocol_errors': 0,
            'rate_limit_responses': 0, 'auth_retries': 0,
            'rows_received': 0, 'response_bytes': 0,
            'upstream_ms_total': 0.0, 'budget_wait_ms_total': 0.0,
            'raw_archive_ms_total': 0.0, 'time_series_write_ms_total': 0.0,
            'time_series_inserted_rows': 0, 'time_series_updated_rows': 0,
            'http_status_counts': {},
        })

    def record_time_series_write(self, template, stats):
        metrics=self._metrics(template)
        metrics['time_series_write_ms_total']+=float(stats.get('write_path_ms',0) or 0)
        metrics['time_series_inserted_rows']+=int(stats.get('inserted_rows',0) or 0)
        metrics['time_series_updated_rows']+=int(stats.get('updated_rows',0) or 0)
        self._save_request_metrics(template,request_group(template),'received')

    @staticmethod
    def _result_row_count(template, result):
        if isinstance(result, list):
            return len(result)
        if not isinstance(result, dict):
            return 0
        if template == '/api/v1/orderbook':
            return sum(len(result.get(side, [])) for side in ('asks', 'bids')
                       if isinstance(result.get(side), list))
        for field in ('candles', 'records', 'stocks', 'trades', 'prices',
                      'rankings', 'items', 'rows'):
            if isinstance(result.get(field), list):
                return len(result[field])
        return 0

    def _save_request_metrics(self, template, group, status, *, last_error=None,
                              last_success=None, upstream_ms=None,
                              budget_wait_ms=None, response_rows=None,
                              response_bytes=None, digest=None):
        metrics = self._metrics(template)
        if last_success:
            success = {
                'received_at': last_success,
                'sha256': digest,
                'source_document_id': digest,
            }
            self.last_success[template] = success
        else:
            success = self.last_success.get(template)
            if success is None:
                previous = self.store.get('toss-api:' + template, {}, use_redis=False)
                success = previous.get('last_success') or {
                    'received_at': previous.get('received_at'),
                    'sha256': previous.get('sha256'),
                    'source_document_id': previous.get('source_document_id'),
                }
                self.last_success[template] = success
        payload = {
            'status': 'received' if status == 'received' else status,
            'request_group': group,
            'last_attempt_at': now(),
            'last_success': success,
            'metrics': {
                'schema_version': 1,
                'session_id': self.metrics_session,
                'session_started_at': self.metrics_started_at,
                **metrics,
                'request_scheduler': self.group_locks[group].snapshot() if group in self.group_locks else None,
                'last_upstream_ms': upstream_ms,
                'last_budget_wait_ms': budget_wait_ms,
                'last_response_rows': response_rows,
                'last_response_bytes': response_bytes,
            },
        }
        if status == 'received':
            payload.update({
                'received_at': last_success,
                'sha256': digest,
                'source_document_id': digest,
                'upstream_ms': upstream_ms,
                'budget_wait_ms': budget_wait_ms,
            })
        if last_error:
            payload['last_error'] = last_error
        self.store.put('toss-api:' + template, payload)

    async def token(self):
        async with self.lock:
            cached = json.loads(self.token_path.read_text()) if self.token_path.exists() else {}
            if cached.get('expires_at', 0) > time.time()+120:
                return cached['access_token']
            credentials = dict(os.environ)
            source = Path(os.getenv('TOSS_CREDENTIAL_FILE', '/srv/services/korea-stocks/shared/secrets/toss.env'))
            if source.exists():
                for line in source.read_text(encoding='utf-8-sig').splitlines():
                    if '=' in line:
                        key, value = line.split('=', 1)
                        if key.strip() in ('TOSS_CLIENT_ID', 'TOSS_CLIENT_SECRET') and not credentials.get(key.strip(), '').strip():
                            credentials[key.strip()] = value.strip()
            if not credentials.get('TOSS_CLIENT_ID') or not credentials.get('TOSS_CLIENT_SECRET'):
                raise ValueError('Toss credentials missing')
            response = await self.client.post(BASE+'/oauth2/token', data={
                'grant_type':'client_credentials', 'client_id':credentials['TOSS_CLIENT_ID'], 'client_secret':credentials['TOSS_CLIENT_SECRET']})
            response.raise_for_status()
            payload = response.json()
            payload['expires_at'] = time.time()+int(payload['expires_in'])
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.token_path.with_suffix('.partial')
            fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(payload, output)
            temporary.replace(self.token_path)
            return payload['access_token']

    async def invalidate_token(self, rejected_token):
        async with self.lock:
            if not self.token_path.exists():
                return
            cached = json.loads(self.token_path.read_text())
            if cached.get('access_token') != rejected_token:
                return  # Another coroutine already refreshed the token.
            cached['expires_at'] = 0
            temporary = self.token_path.with_suffix('.partial')
            fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(cached, output)
            temporary.replace(self.token_path)

    async def get(self, path, params=None):
        template = contract_path(path)
        group = request_group(path)
        lock = self.group_locks.setdefault(group, FairRequestGate())
        metrics = self._metrics(template)
        last_upstream_ms = None
        last_budget_wait_ms = None
        last_status = None
        for attempt in range(3):
            try:
                token = await self.token()
            except Exception as exc:
                lock.reset()
                self._save_request_metrics(template, group, 'auth_error',
                    last_error=type(exc).__name__)
                raise
            async with lock.slot():
                budget_wait = max(0, self.group_deadlines.get(group, 0)-time.monotonic())
                await asyncio.sleep(budget_wait)
                # Pace request starts, not response completions. Slow upstream
                # round trips already consume the interval; adding it again
                # unnecessarily delays every historical page.
                started = time.monotonic()
                by_workload = metrics.setdefault('requests_by_workload', {})
                kind = current_workload()
                by_workload[kind] = by_workload.get(kind, 0) + 1
                try:
                    response = await self.client.get(BASE+path, params=params, headers={'Authorization':'Bearer '+token})
                except httpx.HTTPError as exc:
                    elapsed = round((time.monotonic()-started)*1000, 2)
                    metrics['request_count'] += 1
                    metrics['network_errors'] += 1
                    metrics['upstream_ms_total'] += elapsed
                    metrics['budget_wait_ms_total'] += round(budget_wait*1000, 2)
                    self._save_request_metrics(template, group, 'network_error',
                        last_error=type(exc).__name__, upstream_ms=elapsed,
                        budget_wait_ms=round(budget_wait*1000, 2))
                    raise
                upstream_ms = round((time.monotonic()-started)*1000, 2)
                last_upstream_ms = upstream_ms
                last_budget_wait_ms = round(budget_wait*1000, 2)
                last_status = response.status_code
                metrics['request_count'] += 1
                metrics['upstream_ms_total'] += upstream_ms
                metrics['budget_wait_ms_total'] += last_budget_wait_ms
                metrics['response_bytes'] += len(response.content)
                status_key = str(response.status_code)
                metrics['http_status_counts'][status_key] = metrics['http_status_counts'].get(status_key, 0) + 1
                self.group_deadlines[group] = started+max(.1, float(os.getenv('TOSS_REQUEST_INTERVAL', '.5')))
                if response.status_code == 429:
                    lock.reset()
                    metrics['rate_limit_responses'] += 1
                    delay = retry_after_seconds(response.headers.get('Retry-After', '5'))
                    self.group_deadlines[group] = time.monotonic()+max(1, delay)
            if response.status_code == 429 and attempt < 2:
                continue
            if response.status_code == 401 and attempt == 0:
                lock.reset()
                metrics['auth_retries'] += 1
                await self.invalidate_token(token)
                continue
            try:
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or 'result' not in payload:
                    raise ValueError('Unexpected Toss envelope')
            except httpx.HTTPStatusError as exc:
                metrics['http_errors'] += 1
                self._save_request_metrics(template, group, 'http_error',
                    last_error=f'HTTP_{exc.response.status_code}',
                    upstream_ms=last_upstream_ms, budget_wait_ms=last_budget_wait_ms,
                    response_bytes=len(response.content))
                raise
            except (ValueError, TypeError) as exc:
                metrics['protocol_errors'] += 1
                self._save_request_metrics(template, group, 'protocol_error',
                    last_error=type(exc).__name__, upstream_ms=last_upstream_ms,
                    budget_wait_ms=last_budget_wait_ms, response_bytes=len(response.content))
                raise
            result = payload['result']
            response_rows = self._result_row_count(template, result)
            archived_at = now()
            archive_started = time.perf_counter()
            digest = self.store.raw('toss', str(response.url), response.content)
            metrics['raw_archive_ms_total'] += (time.perf_counter()-archive_started)*1000
            metrics['successful_responses'] += 1
            metrics['rows_received'] += response_rows
            metrics['last_success_at'] = archived_at
            metrics['last_request_status'] = response.status_code
            self._save_request_metrics(template, group, 'received',
                last_success=archived_at, upstream_ms=last_upstream_ms,
                budget_wait_ms=last_budget_wait_ms, response_rows=response_rows,
                response_bytes=len(response.content), digest=digest)
            return result, trace('toss', str(response.url), digest, archived_at, scope=source_scope(path))
        # Only reachable if retry policy is expanded without a return or raise.
        self._save_request_metrics(template, group, 'retry_exhausted',
            last_error='retry_exhausted', upstream_ms=last_upstream_ms,
            budget_wait_ms=last_budget_wait_ms, response_bytes=(len(response.content) if last_status else None))
        raise RuntimeError('Unreachable token retry')

    async def close(self):
        await self.client.aclose()


class TossCollector:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client or TossClient(store)
        self.epoch = uuid4().hex
        self.last_history = {}
        self.history_locks = {}
        self.trading_locks = {}
        self.snapshot_locks = {}
        self.previous_closes = {}

    def _put_history_series(self,key,payload):
        stats={}
        put_series(self.store,key,payload,stats=stats)
        record=getattr(self.client,'record_time_series_write',None)
        if callable(record):
            record('/api/v1/candles',stats)
        return stats

    def save_quote(self, item, evidence, batch=None):
        code = item['symbol']
        if not re.fullmatch(r'[0-9A-Z]{6}', code) or item.get('currency') != 'KRW':
            return
        price, stamp = numeric(item['lastPrice']), item.get('timestamp')
        if price is None or price <= 0:
            return
        if not isinstance(stamp, str):
            # The official contract allows null when no execution time exists.
            # A new collection clock cannot supply a missing market timestamp.
            self.store.put('quote-unavailable:'+code, {'status':'timestamp_missing','received_at':now()})
            return
        try:
            if datetime.fromisoformat(stamp).tzinfo is None:
                return
        except ValueError:
            return
        previous = self.store.get_quote(code)
        if previous.get('as_of'):
            try:
                if datetime.fromisoformat(previous['as_of']) > datetime.fromisoformat(stamp):
                    return
            except (ValueError, TypeError):
                if previous['as_of'][:10] > stamp[:10]:
                    return
        # Quote bursts must not deserialize decades of prices on every execution.
        trade_day = datetime.fromisoformat(stamp).astimezone(ZoneInfo('Asia/Seoul')).date()
        close_key = (code, trade_day.isoformat())
        bar_key = f'bars:{code}:통합:day'
        metadata = self.store.metadata(bar_key, {})
        revision = metadata.get('series_revision') or self.store.revisions((bar_key,)).get(bar_key)
        cached = self.previous_closes.get(close_key)
        calendar_keys = cached[3] if cached is not None else ()
        calendar_revision = (tuple(sorted(self.store.revisions(calendar_keys).items()))
                             if calendar_keys else None)
        if cached is None or cached[0] != revision or cached[2] != calendar_revision:
            if metadata.get('_series_ref'):
                end = (trade_day - timedelta(days=1)).isoformat()+'T99'
                past = self.store.time_series.rows(metadata['_series_ref'], reverse=True, end=end, limit=1)
                prior = past[0] if past else None
            else:
                past = [r for r in metadata.get('rows', []) if r['date'] < trade_day.isoformat()]
                prior = max(past, key=lambda row: row['date']) if past else None
            # A current head and receipt clock never establish a missing session.
            # Wider gaps require an exact official previous/next-business relation.
            previous_weekday = trade_day - timedelta(days=1)
            while previous_weekday.weekday() >= 5:
                previous_weekday -= timedelta(days=1)
            usable = prior and prior.get('date') == previous_weekday.isoformat()
            calendar_keys, calendar_revision = (), None
            if prior and not usable:
                calendar_keys = (f'toss-calendar:KR:{trade_day}', f'market-session:{trade_day}',
                                 f'market-session:{previous_weekday}',
                                 f"market-session:{prior['date']}",
                                 'toss-calendar:KR')
                calendar_revision = tuple(sorted(self.store.revisions(calendar_keys).items()))
                try:
                    usable = calendar_previous_close(self.store, calendar_keys, trade_day,
                                                     date.fromisoformat(prior['date']))
                except (ValueError, TypeError):
                    usable = False
                # Retry on a later event if the calendar changed during this read.
                if calendar_revision != tuple(sorted(self.store.revisions(calendar_keys).items())):
                    usable = False
            prior_close = prior['close'] if usable else None
            self.previous_closes[close_key] = (
                revision, prior_close, calendar_revision, calendar_keys)
        prior_close = self.previous_closes[close_key][1]
        change = float((Decimal(str(price))/Decimal(str(prior_close))-1)*100) if prior_close else None
        if (previous.get('quote_trace', {}).get('source') in ('toss', 'toss_websocket')
                and previous.get('price') == price and previous.get('change') == change
                and previous.get('as_of') == stamp and previous.get('venue') == '통합'):
            # Re-reading the same last trade is not a new quote revision. Request
            # health/raw evidence is already tracked independently by TossClient.
            # The collector may materialize a legacy quote once, preserving all
            # original clocks and evidence. Reads never perform this migration.
            if batch is None:
                self.store.update_quote(code, {})
            else:
                batch[code] = {}
            return
        # Do not carry KRX-only change into an integrated quote.
        patch = {'price':price, 'change':change, 'as_of':stamp,
            'received_at':now(), 'venue':'통합', 'quote_trace':{**evidence,'period':stamp}, 'market_status':None}
        if batch is None:
            self.store.update_quote(code, patch)
        else:
            batch[code] = patch

    async def history(self, code, interval='1d', pages=1, resume=False, *, adjusted=True, compact=False, cover_session=False, repair=False):
        if interval not in ('1d','1m'):
            raise ValueError('Unsupported candle interval')
        if type(pages) is not int or not 1 <= pages <= 16:
            raise ValueError('Candle page budget must be between 1 and 16')
        lock = self.history_locks.setdefault((code, interval, adjusted), asyncio.Lock())
        async with lock:
            return await self._history(code, interval, pages, resume, adjusted=adjusted, compact=compact, cover_session=cover_session, repair=repair)

    async def _history(self, code, interval='1d', pages=1, resume=False, *, adjusted=True, compact=False, cover_session=False, repair=False):
        from .candle_pages import archive_clock, gap_fields, pending_gaps, terminal_receipt, validate_page

        if repair and (resume or interval != '1d'):
            raise ValueError('Gap repair requires its independent daily cursor')
        key = f'bars:{code}:통합:'+('day' if adjusted else 'raw') if interval=='1d' else f'bars:{code}:통합:1min'
        previous = self.store.metadata(key, {}) if interval=='1d' else self.store.get(key, {})
        mode = 'gap' if repair else 'history' if resume else 'head'
        windows = pending_gaps(previous)
        active_gap = next((window for window in windows if window.get('status', 'collecting') == 'collecting'), None)
        if repair and not active_gap:
            return self.store.metadata(key, {}) if compact else self.store.get(key, {})
        clocks = {'head_received_at': previous.get('head_received_at', previous.get('received_at'))} if mode != 'head' else {'head_received_at': now()}
        if resume:
            clocks['history_received_at'] = now()
        if repair:
            clocks['gap_received_at'] = now()
        had_rows=bool(previous.get('row_count') or previous.get('rows'))
        merged = {r['time']:r for r in previous.get('rows', [])}
        before = active_gap['next_before'] if repair else previous.get('next_before') if resume else None
        next_cursor = before
        evidence = None
        observed_count=0
        session = None
        session_covered = False
        previous_last = previous.get('last_time') or max(merged, default=None)
        earliest = None
        gap_closed = False
        terminal = None
        cursor_unknown = False
        terminal_unverified = False
        context = {'code':code, 'kind':('history' if adjusted else 'raw-history') if interval=='1d' else 'intraday',
                   'venue':'통합', 'adjustment':'source' if interval=='1d' and adjusted else 'raw'}
        seen = {before} if before else set()

        def save(status, *, partial=False):
            pending = pending_gaps(previous)
            old_cursor = previous.get('next_before')
            old_complete = bool(previous.get('history_complete', previous.get('backfill_complete', False)))
            receipt = previous.get('history_terminal')
            if mode == 'history' or (mode == 'head' and not had_rows):
                old_cursor = next_cursor
                if not cursor_unknown and not terminal_unverified:
                    old_complete = not partial and terminal is not None
                if terminal is not None and not partial:
                    receipt = terminal
                elif not cursor_unknown and not terminal_unverified:
                    receipt = None
            elif mode == 'gap':
                item = next(window for window in pending if window.get('status', 'collecting') == 'collecting')
                if gap_closed:
                    pending.remove(item)
                else:
                    item['next_before'] = next_cursor
                    item['status'] = 'source_gap' if terminal is not None and not partial else 'collecting'
                    item['updated_at'] = now()
            elif interval == '1d' and had_rows and earliest and previous_last:
                if old_cursor is None and not old_complete:
                    # A migrated/partial archive without a durable continuation
                    # must start from an observed page cursor, not refresh its
                    # head forever. Existing completed cursors remain closed.
                    old_cursor = next_cursor
                    old_complete = terminal is not None and not partial
                    if old_complete:
                        receipt = terminal
                if archive_clock(earliest) > archive_clock(previous_last):
                    window = {'target_time': previous_last, 'next_before': next_cursor,
                              'status': 'source_gap' if terminal is not None and not partial else 'collecting',
                              'started_at': now()}
                    # Never overwrite a previously incomplete bridge.
                    if not any(w.get('target_time') == previous_last for w in pending):
                        pending.append(window)
            fields = gap_fields(pending) if interval == '1d' else {}
            complete = old_complete and not pending
            body = {'rows':sorted(merged.values(), key=lambda r:r['time']), 'venue':'통합',
                    'adjustment':'source' if interval=='1d' and adjusted else 'raw',
                    'adjusted_requested':interval=='1d' and adjusted,
                    'source_trace':evidence or previous.get('source_trace'), 'received_at':now(), **clocks,
                    'status':status, 'next_before':old_cursor, 'history_complete':old_complete,
                    'backfill_complete':complete, 'history_terminal':receipt, **fields,
                    'history_terminal_status':('cursor_unknown' if cursor_unknown else
                        'terminal_unverified' if terminal_unverified else
                        'verified' if proven_history_terminal(receipt, 'explicit_next_before_null', context) else 'unverified'),
                    'latest_next_before':next_cursor if mode=='head' else previous.get('latest_next_before'),
                    'covered_session':session if session_covered else previous.get('covered_session')}
            if cursor_unknown or terminal_unverified:
                body['status'] = 'cursor_unknown' if cursor_unknown else 'terminal_unverified'
            # Gap/history visits cannot renew a previously successful head clock.
            for field in ('history_received_at', 'gap_received_at'):
                if field not in body and previous.get(field):
                    body[field] = previous[field]
            self._put_history_series(key,body)
        # A new trading day can be more than one page ahead of the archive.
        # Walk back to the preceding day (or overlap with a verified session),
        # rather than repeatedly fetching only the last 200 minutes forever.
        cover_session = cover_session and interval == '1m' and not resume
        for _ in range(max(pages, 5) if cover_session else pages):
            params = {'symbol':code,'interval':interval,'count':200,'adjusted':'true' if interval=='1d' and adjusted else 'false'}
            if before:
                params['before'] = before
            try:
                data, page_evidence = await self.client.get('/api/v1/candles', params)
                rows, cursor = validate_page(data, code, before, seen, normalize_candles,
                                             allow_unknown_cursor=True)
            except (Exception, asyncio.CancelledError):
                if observed_count:
                    # Persist only validated pages. The rejected page cannot
                    # advance the cursor or replace the previous evidence.
                    save('partial',partial=True)
                raise
            evidence = page_evidence
            observed_count += len(rows)
            merged.update({r['time']:{**r,'source_trace':evidence} for r in rows})
            if rows:
                earliest = rows[0]['time']
            if 'nextBefore' not in data:
                cursor_unknown = True
                break
            if cursor is None:
                stamp = now()
                proof = history_terminal_receipt(evidence, 'explicit_next_before_null',
                    requested=before, rows=rows, received_at=stamp, context=context)
                if proof is None:
                    terminal_unverified = True
                    break
                terminal = terminal_receipt(evidence,before=before,rows=rows,received_at=stamp,context=context)
            else:
                seen.add(cursor)
            next_cursor = cursor
            if repair and rows and archive_clock(rows[0]['time']) <= archive_clock(active_gap['target_time']):
                gap_closed = True
                break
            if mode=='head' and interval=='1d' and had_rows and rows and previous_last:
                if archive_clock(rows[0]['time']) <= archive_clock(previous_last):
                    break
            if cover_session and rows:
                session = session or rows[-1]['date']
                session_covered = (rows[0]['date'] < session or not cursor or (
                    previous.get('covered_session') == session and previous_last
                    and rows[0]['time'] <= previous_last))
                if session_covered:
                    break
            if not cursor:
                break
            before = cursor
        ordered = sorted(merged.values(), key=lambda r:r['time'])
        if not observed_count and had_rows:
            # An empty latest response cannot erase a last good daily archive.
            # An exhausted backfill may finish its cursor without replacing rows.
            save('ready' if mode!='head' else 'stale')
            return self.store.metadata(key) if compact else self.store.get(key)
        if not ordered:
            save('no_data')
            return self.store.metadata(key) if compact else self.store.get(key)
        save('ready')
        if interval == '1d' and adjusted:
            self.previous_closes = {k:v for k,v in self.previous_closes.items() if k[0] != code}
            if mode=='head' and observed_count and ordered[-1].get('volume') is not None:
                latest = ordered[-1]
                self.store.update_quote(code, {'daily_facts': {
                    'volume': {'value': latest['volume'], 'period': latest['date'], 'unit': '주',
                               'venue': '통합', 'source_trace': {
                                   **latest.get('source_trace', evidence),
                                   'unit': '주', 'period': latest['date'],
                                   'venue': '통합', 'field': 'volume',
                               }},
                }})
        if interval=='1m':
            latest = ordered[-1]['date']
            rows = [r for r in ordered if r['date']==latest]
            points = [{'time':datetime.fromisoformat(r['time']).strftime('%Y%m%d%H%M%S'),'value':r['close']} for r in rows]
            self.store.put('toss-chart:'+code, {'points':points,'as_of':ordered[-1]['time'],'received_at':now(),
                'session_date':latest,'status':'ready','venue':'통합','source_trace':evidence})
            groups = {}
            # Keep a week of the observed minute history in the chart read model.
            # The immutable minute records and prior raw responses remain intact.
            window_start=(datetime.fromisoformat(latest)-timedelta(days=6)).date().isoformat()
            for row in ordered:
                if row['date'] < window_start:
                    continue
                stamp = datetime.fromisoformat(row['time'])
                bucket = stamp.replace(minute=stamp.minute//5*5,second=0,microsecond=0).isoformat()
                groups.setdefault(bucket, []).append(row)
            five = []
            for stamp, group in groups.items():
                five.append({'date':stamp[:10],'time':stamp,'open':group[0]['open'],'close':group[-1]['close'],
                    'high':max(r['high'] for r in group),'low':min(r['low'] for r in group),
                    'volume':sum(r['volume'] for r in group) if all(r['volume'] is not None for r in group) else None,
                    'ohlc_valid':True,'complete':len(group)==5})
            self.store.put(f'bars:{code}:통합:5min', {'rows':five,'session_date':latest,'venue':'통합','adjustment':'raw','source_trace':evidence,'status':'ready'})

        return self.store.metadata(key) if compact else self.store.get(key)

    async def prices(self, codes):
        codes = [code for code in dict.fromkeys(codes)
                 if self.store.get('toss-price-error:'+code,{}).get('retry_at_unix',0)<=time.time()]
        for offset in range(0, len(codes), 200):
            await self._prices_batch(codes[offset:offset+200])

    async def _prices_batch(self, selected):
        try:
            data, evidence = await self.client.get('/api/v1/prices', {'symbols':','.join(selected)})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400,404):
                raise
            if len(selected)>1:
                middle=len(selected)//2
                await self._prices_batch(selected[:middle])
                await self._prices_batch(selected[middle:])
            else:
                self.store.put('toss-price-error:'+selected[0],{'status':'error','http_status':exc.response.status_code,
                    'received_at':now(),'retry_at_unix':time.time()+300})
            return
        batch = {}
        for item in data:
            if item.get('symbol') in selected:
                try:
                    self.save_quote(item, evidence, batch=batch)
                except (ValueError,TypeError,KeyError) as exc:
                    self.store.put('toss-price-error:'+item['symbol'],{'status':'error','error':type(exc).__name__,
                        'received_at':now(),'retry_at_unix':time.time()+30})
        self.store.update_quotes(batch)

    async def snapshot(self, code, *, include_price=True):
        async with self.snapshot_locks.setdefault(code, asyncio.Lock()):
            return await self._snapshot(code, include_price=include_price)

    async def _snapshot(self, code, *, include_price=True):
        started = now()
        if include_price:
            await self.prices([code])
        book, book_trace = await self.client.get('/api/v1/orderbook', {'symbol':code})
        trades, trade_trace = await self.client.get('/api/v1/trades', {'symbol':code,'count':50})
        fresh = {'book':normalize_book(book),'trades':[
            {'time':r['timestamp'][11:19],'timestamp':r['timestamp'],'price':numeric(r['price']),'quantity':numeric(r['volume']),'cumulative_volume':None,'source_trace':trade_trace} for r in trades],
            'provider':'toss','venue':'통합','book_status':'ready' if book.get('asks') or book.get('bids') else 'no_data',
            'trades_status':'ready' if trades else 'no_data','received_at':now(),'book_trace':book_trace,'trades_trace':trade_trace,
            'epoch':self.epoch,'gap':True, 'book_as_of':book.get('timestamp')}
        previous = self.store.get('depth:'+code, {})
        # A slow snapshot must never erase executions received while it was in flight.
        if previous.get('last_trade_received_at', '') > started:
            for field in ('trades','trades_status','last_trade_received_at','trades_trace'):
                if field in previous:
                    fresh[field] = previous[field]
        if previous.get('book_as_of') and (not fresh['book_as_of'] or datetime.fromisoformat(previous['book_as_of']) > datetime.fromisoformat(fresh['book_as_of'])):
            for field in ('book','book_status','book_as_of','book_trace'):
                if field in previous:
                    fresh[field] = previous[field]
        self.store.put('depth:'+code, {**previous, **fresh})

    async def fundamentals_of_trading(self, code, endpoints=None, resume=False, *, pages=1):
        supported = ('investor-trading','program-trades','short-selling','credit-trades','securities-lending')
        for endpoint in endpoints or supported:
            if endpoint not in supported:
                raise ValueError('Unknown trading data kind')
            lock = self.trading_locks.setdefault((code, endpoint), asyncio.Lock())
            async with lock:
                await self._trading_endpoint(code, endpoint, resume, pages=pages)

    async def _trading_endpoint(self, code, endpoint, resume, *, pages=1):
        state = None
        failure = None
        try:
            for index in range(max(1, min(16, int(pages)))):
                state = await self._trading_page(code, endpoint, resume or index > 0)
                if (not state.get('next_until') or state.get('backfill_complete')
                        or state.get('history_status') in ('cursor_unknown', 'terminal_unverified')):
                    break
        except Exception as exc:
            failure = exc
            raise
        finally:
            # Project once per burst, including validated pages preceding a
            # later failure. Each page already commits its rows and cursor.
            try:
                if state is not None:
                    self._project_trading(code, endpoint, state)
            except Exception as exc:
                failure = exc
                raise
            finally:
                if failure is not None:
                    record_trading_collection(self.store, code, endpoint, failure=failure)

    async def _trading_page(self, code, endpoint, resume):
        key = f'toss:{endpoint}:{code}'
        previous = self.store.metadata(key, {})
        params = {'count':100}
        if resume and previous.get('next_until'):
            params['until'] = previous['next_until']
        data, evidence = await self.client.get(f'/api/v1/stocks/{code}/{endpoint}', params)
        # Preserve a valid optional-cursor page without calling it exhausted.
        # A malformed response must not erase the durable continuation cursor.
        if not isinstance(data, dict) or not isinstance(data.get('records'), list):
            raise ValueError('Trading records must be an array')
        evidence = {**evidence,'scope':'통합' if endpoint=='investor-trading' else 'KRX' if endpoint=='program-trades' else '국내','unit':'주'}
        if data.get('symbol') and data['symbol'] != code:
            raise ValueError('Trading security mismatch')
        observed = data['records']
        cursor_known = 'nextUntil' in data
        cursor = data.get('nextUntil')
        if cursor is not None:
            if not isinstance(cursor, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', cursor):
                raise ValueError('Invalid trading history cursor')
            date.fromisoformat(cursor)
            if params.get('until') and cursor >= params['until']:
                raise ValueError('Trading history cursor did not advance')
            if not observed:
                raise ValueError('Empty trading page needs explicit terminal cursor')
        # Only the new page is upserted. Legacy JSON rows migrate on their first
        # write; old trace maps remain as fallback for already-normalized rows.
        legacy_traces = previous.get('record_traces', {})
        merged = {r['date']:{**r,'source_trace':r.get('source_trace') or legacy_traces.get(r['date'],previous.get('source_trace',{}))}
                  for r in previous.get('rows', [])}
        merged.update({r['date']:{**r,'source_trace':evidence} for r in observed})
        had_rows = bool(previous.get('row_count') or previous.get('rows'))
        deep = resume or not had_rows or not previous.get('next_until')
        received_at = now()
        context = {'code': code, 'kind': endpoint, 'venue': evidence['scope']}
        terminal = (history_terminal_receipt(evidence, 'explicit_next_until_null',
                    requested=params.get('until'), rows=observed, received_at=received_at, context=context)
                    if cursor_known and cursor is None else None)
        prior_terminal = previous.get('history_terminal')
        prior_complete = bool(previous.get('backfill_complete') and
                              proven_history_terminal(prior_terminal, 'explicit_next_until_null', context))
        history_status = ('cursor_unknown' if not cursor_known else
                          'terminal_unverified' if cursor is None and terminal is None else 'ready')
        state = {'rows':list(merged.values()),'source_trace':evidence,'record_traces':legacy_traces,'status':'ready' if observed or (resume and had_rows) else 'stale' if had_rows else 'no_data',
                 'received_at':received_at, 'last_response_rows':len(observed),
                 'head_received_at': previous.get('head_received_at', previous.get('received_at')) if resume else now(),
                 'history_received_at': now() if resume else previous.get('history_received_at'),
                 'history_status':history_status,
                 'next_until':cursor if deep and history_status == 'ready' else previous.get('next_until'),
                 'backfill_complete':bool(terminal) if deep and history_status == 'ready' else prior_complete,
                 'history_terminal':terminal if deep and history_status == 'ready' else prior_terminal}
        if history_status != 'ready':
            state['status'] = history_status
        # Refreshing today's page must not reopen an exhausted old cursor.
        if prior_complete and not resume:
            state.update(next_until=None, backfill_complete=True)
        put_series(self.store, key, state, reverse=True)
        return state

    def _project_trading(self, code, endpoint, state):
        if os.getenv('MARKET_COMPACT_TRADING_PROJECTIONS', '0') == '1':
            from .trading_projection import compact_projection
            if compact_projection(self.store, code, endpoint, state):
                return
        # A head refresh often returns identical observations. Keep its receipt
        # clock, but do not deserialize and rebuild years of unchanged history.
        fields = {
            prefix + code: owned
            for prefix, owned in TRADING_PROJECTION_FIELDS.get(endpoint, {}).items()
        }
        keys = tuple(fields)
        source = self.store.metadata(f'toss:{endpoint}:{code}', {})
        signature = {'version': 2, 'revision': source.get('series_revision'),
                     'rows': source.get('row_count'), 'status': state.get('status')}
        marker_key = f'toss-projection:{endpoint}:{code}'
        marker = self.store.metadata(marker_key, {})
        revisions = self.store.revisions(keys)
        expected = marker.get('output_field_hashes', {})
        reusable = (bool(source) and bool(keys) and len(revisions) == len(keys)
                    and marker.get('input') == signature and all(expected.get(key) for key in keys))
        if reusable and marker.get('outputs') != revisions:
            # A company refresh or the other financing endpoint can change the
            # shared snapshot without changing our fields. Check just those
            # fields in changed outputs, not years of normalized source rows.
            revisions = self._projection_output_revisions(
                fields, expected, marker.get('outputs', {})
            )
            reusable = bool(revisions)
        if reusable:
            receipt = marker.get('collection_receipt')
            if receipt is None:
                # One-time repair of older markers, using their verified output.
                # New markers carry this small receipt and never re-read history.
                payload = self.store.metadata(keys[0], {})
                receipt = trading_collection_receipt(endpoint, payload)
            self.store.put(marker_key, {**marker, 'outputs': revisions,
                'collection_receipt': receipt,
                'skipped': marker.get('skipped', 0) + 1, 'checked_at': now()})
            record_trading_collection(self.store, code, endpoint, receipt,
                                      source={**source, 'status': state['status']})
            return
        started = time.perf_counter()
        written = self._write_trading_projection(code, endpoint, state)
        expected = {key: projection_field_hash(written.get(key), fields[key]) for key in keys}
        # Bind hashes to the actual written values, then validate the persisted
        # outputs. An intervening writer must not be certified by a newer clock.
        revisions = self._projection_output_revisions(fields, expected)
        # Record only a fully successful projection. Partial write failures must
        # retry even when the next provider response contains identical values.
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        receipt = trading_collection_receipt(endpoint, written.get(keys[0], {})) if keys else {}
        self.store.put(marker_key, {'input': signature,
            'outputs': revisions, 'output_field_hashes': expected, 'checked_at': now(),
            'collection_receipt': receipt,
            'rebuilt': marker.get('rebuilt', 0) + 1,
            'skipped': marker.get('skipped', 0), 'last_build_ms': elapsed,
            'total_build_ms': round(marker.get('total_build_ms', 0) + elapsed, 3)})
        if revisions:
            record_trading_collection(self.store, code, endpoint, receipt,
                                      source={**source, 'status': state['status']})

    def _projection_output_revisions(self, fields, expected, trusted_revisions=None):
        keys = tuple(fields)
        before = self.store.revisions(keys)
        if not keys or len(before) != len(keys) or not all(expected.get(key) for key in keys):
            return {}
        trusted_revisions = trusted_revisions or {}
        for key in keys:
            if trusted_revisions.get(key) == before[key]:
                continue
            payload = self.store.get(key, {}, use_redis=False)
            if projection_field_hash(payload, fields[key]) != expected[key]:
                return {}
        return before if self.store.revisions(keys) == before else {}

    def _write_trading_projection(self, code, endpoint, state):
        written = {}
        def publish(key, payload):
            self.store.put(key, payload)
            written[key] = payload
        key = f'toss:{endpoint}:{code}'
        previous = self.store.metadata(key, {})
        legacy_traces = previous.get('record_traces', {})
        rows = self.store.time_series.rows(key, reverse=True, limit=1 if endpoint=='program-trades' else None)
        if endpoint != 'program-trades':
            from .trading_projection import normalize_rows
            normalized = normalize_rows(endpoint, rows, previous, state['status'])
            if endpoint == 'investor-trading':
                details = dict(self.store.get('detail:' + code, {}))
                details['flows'] = normalized['rows']
                publish('toss-normalized:flows:' + code, {**normalized, 'received_at': now()})
                publish('detail:' + code, details)
            elif endpoint in ('credit-trades', 'securities-lending'):
                result = {**self.store.get('financing:' + code, {'code': code}), **normalized}
                publish('toss-normalized:financing:' + code, result)
                publish('financing:' + code, result)
            elif endpoint == 'short-selling':
                result = {**self.store.get('short-selling:' + code, {'code': code}), **normalized}
                publish('toss-normalized:short-selling:' + code, normalized)
                publish('short-selling:' + code, result)
        elif endpoint=='program-trades' and rows:
            r = rows[0]
            evidence = r.get('source_trace') or legacy_traces.get(
                r['date'], previous.get('source_trace', {}))
            result = self.store.get('trading:'+code,{})
            totals = {}
            for target, field in [('buy_quantity','buyVolume'),('sell_quantity','sellVolume'),('net_quantity','netBuyVolume')]:
                values = [numeric((r.get(part) or {}).get(field)) for part in ('arbitrage','nonArbitrage')]
                totals[target] = sum(values) if all(v is not None for v in values) else None
            result.update(program={**totals,'as_of':r['date']},program_status=state['status'],venue='KRX',received_at=now(),program_trace={**evidence,'period':r['date']})
            publish('trading:'+code,result)
        return written

    async def stream(self):
        import websockets

        from .stream_projection import StreamProjection
        projection = StreamProjection(self)
        failures = 0
        # A rejected target stays excluded until this worker is restarted after
        # the cause is corrected. REST demand and last good data remain intact.
        denied = set()
        rejected_targets = {}
        while True:
            token = None
            try:
                self.store.put('provider-toss-stream', {'status':'connecting','accepted':[], 'rejected':[], 'received_at':now()})
                token = await self.client.token()
                async with websockets.connect(STREAM_URL, additional_headers={'Authorization':'Bearer '+token}, ping_interval=None) as socket:
                    previous, previous_codes, accepted = None, [], set()
                    last_ping, active_checked, codes = time.monotonic(), 0.0, []
                    self.epoch = uuid4().hex
                    self.store.put('provider-toss-stream', {'status':'recovering','accepted':[], 'rejected':[], 'received_at':now(),'epoch':self.epoch})
                    while True:
                        # Demand changes are sampled once per second; a busy feed
                        # must not query the same active-job index on every tick.
                        if time.monotonic() - active_checked >= 1:
                            codes = list(dict.fromkeys(self.store.active()))[:50]
                            active_checked = time.monotonic()
                        declarations = [{'type': kind, 'codes': [code for code in codes
                                         if kind + ':' + code not in denied]}
                                        for kind in ('trade:kr', 'orderbook:kr')]
                        declarations = [item for item in declarations if item['codes']]
                        targets = {item['type'] + ':' + code for item in declarations for code in item['codes']}
                        signature = tuple(sorted(targets))
                        if signature != previous:
                            await socket.send(json.dumps(declarations))
                            accepted.intersection_update(targets)
                            self.store.put('provider-toss-stream', {'status':'subscribing' if targets else 'idle','accepted':sorted(accepted), 'rejected':list(rejected_targets.values()), 'received_at':now(),'epoch':self.epoch})
                            for code in set(codes)-set(previous_codes):
                                self.store.put('toss-recovery:'+code, {'epoch':self.epoch,'requested_at':now()})
                            previous, previous_codes = signature, list(codes)
                        if time.monotonic()-last_ping > 60:
                            await socket.send('PING')
                            last_ping = time.monotonic()
                        try:
                            frame = await asyncio.wait_for(socket.recv(), timeout=projection.wait_seconds())
                        except TimeoutError:
                            projection.flush()
                            continue
                        received_at = now()
                        frame_bytes = frame.encode('utf-8') if isinstance(frame, str) else bytes(frame)
                        digest = self.store.raw('toss-stream', STREAM_URL, frame_bytes)
                        # The official keepalive reply is JSON {"type":"pong"}.
                        # Parse only after the exact received frame is archived.
                        event = json.loads(frame)
                        if event.get('type')=='subscriptions':
                            failures = 0
                            for rejection in event.get('rejected', []):
                                if isinstance(rejection, dict) and rejection.get('target') in targets:
                                    denied.add(rejection['target'])
                                    rejected_targets[rejection['target']] = rejection
                            accepted = (set(event.get('subscribed', [])) & targets) - denied
                            self.store.put('provider-toss-stream', {'status':'subscribed','accepted':sorted(accepted),'rejected':list(rejected_targets.values()),'received_at':now(),'lossy':True,'epoch':self.epoch})
                        elif event.get('type')=='message':
                            if event.get('topic') in accepted:
                                projection.record(event, codes, self.epoch, received_at, archived_digest=digest)
                            else:
                                self.store.put('toss-stream-unaccepted', {'topic':event.get('topic'),
                                    'reason':'unaccepted_subscription','raw_sha256':digest,
                                    'received_at':received_at,'epoch':self.epoch})
                        elif event.get('type')=='error':
                            raise ValueError('Provider stream error')
            except asyncio.CancelledError:
                projection.try_finish()
                raise
            except Exception as exc:
                projected = projection.try_finish()
                if token and getattr(getattr(exc, 'response', None), 'status_code', None) == 401:
                    await self.client.invalidate_token(token)
                failures += 1
                try:
                    self.store.put('provider-toss-stream',{'status':'reconnecting','error':type(exc).__name__,
                        'projection_pending':not projected,'received_at':now()})
                except Exception:
                    # Keep reconnecting if the storage failure also blocks status.
                    pass
                await asyncio.sleep(min(60,2**min(failures,6)))

    async def recover_snapshots(self):
        while True:
            for code in self.store.active():
                key = 'toss-recovery:'+code
                state = self.store.get(key, {})
                if not state.get('requested_at') or state.get('status') == 'ready':
                    continue
                if time.time() < state.get('retry_at_unix', 0):
                    continue
                try:
                    await self.snapshot(code)
                    current = self.store.get(key, {})
                    if current.get('epoch') == state.get('epoch'):
                        self.store.put(key, {**state, 'status':'ready', 'completed_at':now()})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.store.put(key, {**state, 'status':'error', 'error':type(exc).__name__,
                        'retry_at_unix':time.time()+30})
            await asyncio.sleep(1)

    async def refresh_prices(self):
        """REST fallback does not wait for history, filings or individual snapshots."""
        while True:
            try:
                await self.prices(self.store.active())
            except Exception as exc:
                self.store.put('toss-price-error', {'error':type(exc).__name__,'received_at':now()})
            await asyncio.sleep(5)

    async def refresh_active_once(self):
        with request_workload('interactive'):
            return await self._refresh_active_once()

    async def _refresh_active_once(self):
        """Refresh each due endpoint independently within the existing active set."""
        from .quote_metrics import trading_day
        provider_failures = 0
        halt = False
        for code in self.store.active():
            errors = []

            async def attempt(part, operation, errors=errors):
                nonlocal provider_failures, halt
                try:
                    performed = await operation()
                    # A fresh/no-op section is not evidence of provider recovery.
                    if performed is not False:
                        provider_failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    errors.append((part, type(exc).__name__))
                    status = (exc.response.status_code
                              if isinstance(exc, httpx.HTTPStatusError) else None)
                    if status in (401, 403, 429):
                        halt = True  # Client already applied token/Retry-After retries.
                    elif isinstance(exc, httpx.RequestError) or (status and status >= 500):
                        provider_failures += 1
                        halt = provider_failures >= 2

            async def history(code=code):
                if time.monotonic()-self.last_history.get(code, 0) <= 60:
                    return False
                daily = self.store.metadata(f'bars:{code}:통합:day', {})
                quote = self.store.get_quote(code)
                quote_day = trading_day(quote.get('as_of'))
                current_day = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
                volume = quote.get('daily_facts', {}).get('volume', {})
                daily_age = head_age_seconds(daily, now())
                needs_volume = quote_day and trading_day(volume.get('period')) != quote_day
                if (daily_age > (300 if quote_day == current_day else 3600)
                        or (needs_volume and daily_age > 60)):
                    await self.history(code, '1d', pages=1, compact=True)
                await self.history(code, '1m', pages=1, cover_session=True)
                raw = self.store.metadata(f'bars:{code}:통합:raw', {})
                # Active refresh owns the head only. The whole-market queue owns
                # all older pages and bridge cursors, including history >10 years.
                if head_age_seconds(raw, now()) > 3600:
                    await self.history(code, '1d', adjusted=False, compact=True)
                self.last_history[code] = time.monotonic()

            await attempt('history', history)
            if not halt:
                await attempt('snapshot',
                              lambda code=code: self.snapshot(code, include_price=False))
            if not halt:
                for endpoint in ('investor-trading', 'program-trades', 'short-selling',
                                 'credit-trades', 'securities-lending'):
                    async def trading(endpoint=endpoint, code=code):
                        previous = self.store.metadata(f'toss:{endpoint}:{code}', {})
                        if head_age_seconds(previous, now()) > 900:
                            await self.fundamentals_of_trading(code, endpoints=(endpoint,))
                            return True
                        return False
                    await attempt(endpoint, trading)
                    if halt:
                        break
            self.store.put('provider-toss', {
                'status': 'error' if errors else 'ready', 'received_at': now(),
                **({'error': errors[-1][1], 'failed_parts': [part for part, _ in errors]}
                   if errors else {})})
            if halt:
                return

    async def run(self):
        async def refresh():
            while True:
                await self.refresh_active_once()
                await asyncio.sleep(15)
        from .toss_backfill import TossBackfill
        from .toss_market import TossMarket
        await asyncio.gather(self.refresh_prices(), refresh(), self.stream(),
                             self.recover_snapshots(), TossBackfill(self).run(),
                             TossMarket(self).run())
