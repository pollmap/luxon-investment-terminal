"""Resumable, bounded all-security collection using the live worker's Toss client.

Bounded history bursts per turn, shared authentication/rate budget, no new process.
Successful empty responses, request failures and unfinished pages remain distinct.
"""
import asyncio
import heapq
import logging
import os
import shutil
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .service_scope import REVIEWED_NON_SHARE_INSTRUMENTS
from .store import now
from .request_fairness import request_workload

KINDS = ('history', 'investor-trading', 'program-trades', 'short-selling',
         'credit-trades', 'securities-lending', 'raw-history')
MAX_BURST_PAGES = 16
DEFAULT_BURST_PAGES = 16
log = logging.getLogger('toss-backfill')


def burst_pages(name):
    """Bound one resumable turn without changing provider request pacing."""
    raw = os.getenv(name)
    if raw is None:
        return DEFAULT_BURST_PAGES
    try:
        return max(1, min(MAX_BURST_PAGES, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_BURST_PAGES


def after(stamp, reference):
    """Compare instants, including checkpoints written by earlier KST workers."""
    try:
        value=datetime.fromisoformat(stamp)
        bound=datetime.fromisoformat(reference) if isinstance(reference,str) else reference
        return value.tzinfo is not None and bound.tzinfo is not None and value>bound
    except (ValueError,TypeError):
        return False


def request_history(store, code, kind):
    """Coalesce user interest into bounded background work; never fetch in HTTP."""
    if kind not in KINDS:
        raise ValueError('Unknown history kind')
    key = f'trading-history-request:{code}:{kind}'
    previous = store.get(key, {})
    if not previous.get('at') or (datetime.fromisoformat(now())-datetime.fromisoformat(previous['at'])).total_seconds()>60:
        store.put(key, {'code':code, 'kind':kind, 'at':now()})


def pause_seconds(status):
    # Per-security errors already have a persisted retry deadline. They must not
    # stop unrelated securities (e.g. one unsupported KONEX symbol costs 30s).
    if status in ('capacity_limited', 'backoff', 'waiting_universe', 'queue_error'):
        return 30
    return max(.05, float(os.getenv('MARKET_TOSS_BACKFILL_PAUSE', '.15')))


def cutoff_day(today):
    try:
        return today.replace(year=today.year - 10).isoformat()
    except ValueError:
        return today.replace(year=today.year - 10, day=28).isoformat()


def recent_head(snapshot, clock):
    """An older page cannot renew the latest-page clock; compare Korean dates."""
    stamp = snapshot.get('head_received_at', snapshot.get('received_at'))
    try:
        left, right = (datetime.fromisoformat(v) for v in (stamp, clock))
        if left.tzinfo is None or right.tzinfo is None:
            return False
        return left.astimezone(ZoneInfo('Asia/Seoul')).date() == right.astimezone(ZoneInfo('Asia/Seoul')).date()
    except (ValueError, TypeError):
        return False


def resume_history(snapshot, cursor, clock, attempts):
    # Four out of five backfill visits advance the durable old cursor even if
    # a full universe scan takes more than a day. The fifth refreshes the head.
    return bool(snapshot.get(cursor) and (recent_head(snapshot, clock) or attempts % 5 != 0))


def trading_collection_status(snapshot):
    """Readiness of a displayed page is not completion of provider history."""
    source = snapshot.get('status', 'error')
    if source not in ('ready', 'no_data'):
        return source
    if not snapshot.get('backfill_complete') or snapshot.get('next_until'):
        return 'collecting'
    return 'ready' if snapshot.get('row_count') or snapshot.get('rows') else 'no_data'


def price_collection_status(snapshot):
    """A completed old cursor cannot make a failed latest refresh ready."""
    # Legacy price snapshots may omit status. Preserve their existing
    # cursor/count interpretation, but never override an explicit failure.
    source = snapshot.get('status', 'ready')
    if source not in ('ready', 'no_data'):
        return source
    if snapshot.get('gap_status') == 'source_gap':
        return 'source_gap'
    if not snapshot.get('backfill_complete') or snapshot.get('next_before'):
        return 'collecting'
    return 'ready' if snapshot.get('row_count') or snapshot.get('rows') else 'no_data'


async def repair_trading_job_statuses(store):
    """Correct old queue interpretations without editing data or cursors."""
    jobs = await asyncio.to_thread(store.list_prefix, 'toss-backfill:')
    pending = []
    for key, state in jobs.items():
        if key.rsplit(':', 1)[-1] in ('history', 'raw-history'):
            continue
        if state.get('status') != 'ready' or (state.get('backfill_complete') and not state.get('next_until')):
            continue
        pending.append((key,state))
    def repair_batch(items):
        # One bounded transaction preserves archive+correction atomically and
        # avoids thousands of separate commits delaying collection startup.
        with store.lock, store.engine.begin() as db:
            for key,state in items:
                store.put('backfill-status-review:' + key.split(':',1)[1], state, _connection=db)
                store.put(key, {**state, 'status':'collecting', 'source_status':'ready',
                    'status_version':2, 'status_repaired_at':now()}, _connection=db)
        # These are internal queue records. No public data response is changed;
        # readers bypass Redis to inspect the committed job state.
    for offset in range(0,len(pending),64):
        await asyncio.to_thread(repair_batch, pending[offset:offset+64])
        await asyncio.sleep(.05)
    return len(pending)


async def classify_provider_catalogue_omissions(store):
    """Persist fresh-catalogue 404 evidence and avoid repeating identical calls."""
    from .source_support import diagnose_not_found

    jobs = await asyncio.to_thread(store.list_prefix, 'toss-backfill:')
    known = set(await asyncio.to_thread(store.listed_codes))
    updates = []
    for key, state in jobs.items():
        if state.get('http_status') != 404:
            continue
        _, code, kind = key.split(':', 2)
        if code not in known:
            continue
        diagnosis = diagnose_not_found(store, code, kind)
        if diagnosis['status'] != 'provider_catalogue_omission':
            continue
        if (state.get('status') == 'not_in_provider_catalogue'
                and state.get('provider_catalogue_revision') == diagnosis['catalogue_revision']):
            continue
        updates.append((key, code, kind, state, diagnosis))

    def write_batch(items):
        with store.lock, store.engine.begin() as db:
            for key, code, kind, state, diagnosis in items:
                store.put('backfill-status-review:' + code + ':' + kind, state, _connection=db)
                store.put(key, {**state, 'status': 'not_in_provider_catalogue',
                    'diagnosis': diagnosis,
                    'provider_catalogue_revision': diagnosis['catalogue_revision'],
                    'classified_at': now(), 'retry_after': None}, _connection=db)

    for offset in range(0, len(updates), 64):
        await asyncio.to_thread(write_batch, updates[offset:offset + 64])
    return len(updates)


class TossBackfill:
    def __init__(self, collector, disk_usage=shutil.disk_usage):
        self.collector = collector
        self.store = collector.store
        self.disk_usage = disk_usage
        self._codes = None
        self._queued = []
        self._retry_queue = []

    async def reconcile(self, codes):
        """Register missing work without claiming any source data was received."""
        jobs = await asyncio.to_thread(self.store.list_prefix, 'toss-backfill:')
        listed = set(codes)
        excluded = [(key,state) for key,state in jobs.items()
                    if key.split(':')[1] in REVIEWED_NON_SHARE_INSTRUMENTS
                    and state.get('status')!='out_of_scope']
        def archive_batch(items):
            # Keep the former queue record and cursor for audit; only mark the
            # scheduler row out of scope so it cannot count as an unresolved error.
            with self.store.lock, self.store.engine.begin() as db:
                for key,state in items:
                    _,code,kind=key.split(':',2)
                    self.store.put('toss-backfill-out-of-scope:'+code+':'+kind,
                        {'state':state,'reason':'listed_fund_unit_excluded_from_share_universe',
                         'instrument':REVIEWED_NON_SHARE_INSTRUMENTS[code],
                         'scope_version':2,'archived_at':now()},_connection=db)
                    self.store.put(key,{**state,'status':'out_of_scope',
                        'source_status':state.get('status'),'scope_version':2,
                        'out_of_scope_at':now()},_connection=db)
        for offset in range(0,len(excluded),64):
            await asyncio.to_thread(archive_batch,excluded[offset:offset+64])

        # Old queue records can outlive delisted or otherwise out-of-scope
        # securities. Keep the exact job/cursor as an audit record, but do not
        # let stale 404s inflate the current-universe error count. This status
        # is deliberately not a delisting claim: it means only that the code is
        # absent from the current, already-scoped universe snapshot.
        inactive = []
        reactivated = []
        for key, state in jobs.items():
            parts = key.split(':', 2)
            if len(parts) != 3 or parts[0] != 'toss-backfill':
                continue
            _, code, kind = parts
            if code in REVIEWED_NON_SHARE_INSTRUMENTS or kind not in KINDS:
                continue
            if code not in listed and state.get('status') != 'out_of_universe':
                inactive.append((key, code, kind, state))
            elif code in listed and state.get('status') == 'out_of_universe':
                reactivated.append((key, code, kind, state))

        def reconcile_scope(items, *, archive):
            with self.store.lock, self.store.engine.begin() as db:
                for key, code, kind, state in items:
                    stamp = now()
                    if archive:
                        history_key = f'toss-backfill-scope-history:{code}:{kind}:{stamp.replace(":", "-")}'
                        self.store.put(history_key, {
                            'state': state,
                            'reason': 'not_present_in_current_scoped_security_universe',
                            'archived_at': stamp,
                        }, _connection=db)
                        self.store.put(key, {**state, 'status': 'out_of_universe',
                            'source_status': state.get('status'), 'scope_version': 1,
                            'out_of_universe_at': stamp}, _connection=db)
                    else:
                        # A newly listed/relisted security resumes from the
                        # preserved raw cursor; the next step validates the
                        # snapshot before deciding whether any request is due.
                        self.store.put(key, {**state, 'status': 'queued',
                            'reactivated_at': stamp}, _connection=db)

        for offset in range(0, len(inactive), 64):
            await asyncio.to_thread(reconcile_scope, inactive[offset:offset + 64], archive=True)
        for offset in range(0, len(reactivated), 64):
            await asyncio.to_thread(reconcile_scope, reactivated[offset:offset + 64], archive=False)

        missing = [(f'toss-backfill:{code}:{kind}', {'status':'queued', 'registered_at':now()})
                   for code in codes for kind in KINDS
                   if f'toss-backfill:{code}:{kind}' not in jobs]
        def register(items):
            with self.store.lock, self.store.engine.begin() as db:
                for key, state in items:
                    self.store.put(key, state, _connection=db)
        for offset in range(0, len(missing), 64):
            await asyncio.to_thread(register, missing[offset:offset+64])
        self._queued = [key for key, state in jobs.items()
                        if state.get('status') == 'queued' and key.split(':')[1] in listed]
        self._queued.extend(key for key, _ in missing)
        self._queued.sort()
        self._retry_queue = []
        for key, state in jobs.items():
            if state.get('status') != 'error' or key.split(':')[1] not in listed:
                continue
            try:
                retry_at = datetime.fromisoformat(state['retry_after'])
            except (KeyError, TypeError, ValueError):
                continue
            if retry_at.tzinfo is not None:
                heapq.heappush(self._retry_queue, (retry_at, key, state['retry_after']))
        self._codes = tuple(codes)

    async def step(self):
        progress = self.store.get('toss-backfill-progress', {})
        clock = now()
        disk = self.disk_usage(self.store.root)
        from .storage_budget import backfill_capacity
        capacity = backfill_capacity(self.store.root, disk, progress.get('status'))
        if capacity['limited']:
            self.store.put('toss-backfill-progress', {**progress, 'status': 'capacity_limited',
                'capacity': capacity, 'updated_at': clock})
            return 'capacity_limited'
        if after(progress.get('retry_after'),clock):
            return 'backoff'
        codes = sorted(self.store.listed_codes())
        if not codes:
            return 'waiting_universe'
        if tuple(codes) != self._codes:
            await self.reconcile(codes)
        # A numeric index moves to a different security when listings change.
        index = (bisect_left(codes, progress['next_code']) if progress.get('next_code')
                 else int(progress.get('cursor', 0))) % len(codes)
        phase = int(progress.get('phase', 0)) % len(KINDS)
        code, kind = codes[index], KINDS[phase]
        priority = False
        # Due failures get one bounded retry turn before user-requested history.
        # Both still use only one turn in five and cannot stall the normal scan.
        if int(progress.get('attempts', 0)) % 5 == 0:
            retry_at = datetime.now(UTC)
            while self._retry_queue:
                scheduled_at, retry_key, scheduled_stamp = self._retry_queue[0]
                retry_state = self.store.get(retry_key, {})
                if (retry_state.get('status') != 'error'
                        or retry_state.get('retry_after') != scheduled_stamp):
                    heapq.heappop(self._retry_queue)
                    continue
                if scheduled_at <= retry_at:
                    heapq.heappop(self._retry_queue)
                    _, code, kind = retry_key.split(':', 2)
                    priority = True
                break
            if not priority:
                active = set(self.store.active())
                requested = [v for v in self.store.list_prefix('trading-history-request:').values()
                             if v.get('code') in active and v.get('kind') in KINDS
                             and after(v.get('at'),datetime.now(UTC)-timedelta(minutes=30))]
                requested.sort(key=lambda v:(v['code'],v['kind']))
                if requested:
                    item = requested[int(progress.get('priority_cursor',0)) % len(requested)]
                    code, kind, priority = item['code'], item['kind'], True
            if not priority and int(progress.get('attempts', 0)) % 5 == 0 and int(progress.get('cycles', 0)) and self._queued:
                while self._queued:
                    pending = self._queued.pop(0)
                    if self.store.get(pending, {}).get('status') == 'queued':
                        _, code, kind = pending.split(':')
                        priority = True
                        break
        key = f'toss-backfill:{code}:{kind}'
        state = self.store.get(key, {})
        status = 'current'
        global_retry = None
        provider_omission_unchanged = False
        if state.get('status') == 'not_in_provider_catalogue':
            from .source_support import diagnose_not_found
            current_catalogue = diagnose_not_found(self.store, code, kind)
            provider_omission_unchanged = (
                current_catalogue['status'] == 'provider_catalogue_omission'
                and current_catalogue['catalogue_revision'] == state.get('provider_catalogue_revision')
            )
        try:
            if provider_omission_unchanged:
                status = 'not_in_provider_catalogue'
            elif after(state.get('retry_after'),clock):
                status = 'item_backoff'
            elif kind in ('history','raw-history'):
                adjusted = kind == 'history'
                snapshot = self.store.metadata(f'bars:{code}:통합:' + ('day' if adjusted else 'raw'), {})
                rows = snapshot.get('rows', [])
                target = 'provider_available_history'
                covered = snapshot.get('backfill_complete') and not snapshot.get('next_before')
                fresh = recent_head(snapshot, clock)
                blocked_gap = (snapshot.get('gap_status') == 'source_gap'
                               and snapshot.get('history_complete') and not snapshot.get('next_before'))
                if (not covered or not fresh) and not (blocked_gap and fresh):
                    # A recent head refresh never discards the older-page cursor.
                    resume = resume_history(snapshot, 'next_before', clock, int(state.get('visits', 0)))
                    # Bridge a newly discovered middle gap on alternating visits;
                    # the independent older cursor keeps its own opportunities.
                    visits = int(state.get('visits', 0))
                    repair = bool(snapshot.get('gap_next_before') and visits % 2 == 1
                                  and (fresh or visits % 5 != 0))
                    # Refreshing the head must not replace an older durable cursor.
                    # Old pages/new archives can use a bounded burst rather than
                    # waiting for another entire seven-kind universe sweep.
                    pages = burst_pages('MARKET_TOSS_HISTORY_BURST') if repair or resume or not (snapshot.get('row_count') or rows) else 1
                    with request_workload('history' if repair or resume or pages > 1 else 'latest'):
                        result = await self.collector.history(code, pages=pages, resume=resume and not repair,
                            compact=True, **({'repair':True} if repair else {}), **({} if adjusted else {'adjusted':False}))
                    rows = result.get('rows', [])
                    covered = result.get('backfill_complete') and not result.get('next_before')
                    count=result.get('row_count',len(rows))
                    status = price_collection_status(result)
                    self.store.put(key, {'status': status, 'rows': count,
                        'source_status': result.get('status', 'ready'),
                        'first_date':result.get('first_time',rows[0]['date'] if rows else '')[:10] or None,
                        'last_date':result.get('last_time',rows[-1]['date'] if rows else '')[:10] or None,
                        'next_before': result.get('next_before'), 'updated_at': clock,
                        'backfill_complete':bool(covered),
                        'history_complete':result.get('history_complete',result.get('backfill_complete',False)),
                        'gap_status':result.get('gap_status'),'gap_next_before':result.get('gap_next_before'),
                        'failures': 0, 'visits': int(state.get('visits', 0)) + 1, 'target_start': target})
                else:
                    count = snapshot.get('row_count', len(rows))
                    status = price_collection_status(snapshot)
                    if state.get('status') != status or state.get('rows') != count or not state.get('backfill_complete'):
                        # Another view's collection may have completed this series.
                        # Record evidence without issuing an unnecessary provider call.
                        self.store.put('backfill-status-review:'+code+':'+kind,state)
                        self.store.put(key, {'status':status, 'rows':count,
                            'source_status':snapshot.get('status', 'ready'),
                            'first_date':str(snapshot.get('first_time') or '')[:10] or None,
                            'last_date':str(snapshot.get('last_time') or '')[:10] or None,
                            'backfill_complete':bool(covered), 'next_before':snapshot.get('next_before'), 'updated_at':clock,
                            'history_complete':snapshot.get('history_complete',True),'gap_status':snapshot.get('gap_status'),
                            'target_start':target, 'visits':int(state.get('visits',0)), 'failures':0})
            else:
                snapshot = self.store.metadata(f'toss:{kind}:{code}', {})
                fresh = recent_head(snapshot, clock)
                rows = snapshot.get('rows', [])
                target = 'provider_available_history'
                covered = snapshot.get('backfill_complete') and not snapshot.get('next_until')
                if not fresh or not covered:
                    resume = resume_history(snapshot, 'next_until', clock, int(state.get('visits', 0)))
                    pages = burst_pages('MARKET_TOSS_TRADING_BURST') if resume or not (snapshot.get('row_count') or rows) else 1
                    with request_workload('history' if resume or pages > 1 else 'latest'):
                        await self.collector.fundamentals_of_trading(code, endpoints=(kind,), resume=resume, pages=pages)
                    latest = self.store.metadata(f'toss:{kind}:{code}', {})
                    status = trading_collection_status(latest)
                    self.store.put(key, {'status': status, 'rows': latest.get('row_count',len(latest.get('rows', []))),
                        'source_status':latest.get('status', 'error'), 'status_version':2,
                        'updated_at': clock, 'failures': 0, 'next_until':latest.get('next_until'),
                        'visits': int(state.get('visits', 0)) + 1,
                        'target_start':target,'backfill_complete':latest.get('backfill_complete',False)})
                else:
                    status = trading_collection_status(snapshot)
                    count=snapshot.get('row_count',len(rows))
                    if kind != 'program-trades':
                        # Completed pages may predate collection receipts. The
                        # existing projection verifies/reuses its small marker;
                        # never fetch a provider page or reset the old cursor.
                        self.collector._project_trading(
                            code, kind, {'status': snapshot.get('status', 'error')})
                    if state.get('status') != status or state.get('rows') != count or not state.get('backfill_complete'):
                        self.store.put('backfill-status-review:'+code+':'+kind,state)
                        self.store.put(key, {'status':status,
                            'rows':count, 'updated_at':clock,
                            'source_status':snapshot.get('status','error'), 'status_version':2,
                            'next_until':None,'failures':0,
                            'backfill_complete':True, 'target_start':target,
                            'visits':int(state.get('visits',0))})
        except Exception as exc:
            failures = int(state.get('failures', 0)) + 1
            delay = min(3600, 30 * 2 ** min(failures, 7))
            http_status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            diagnosis = None
            if http_status == 404:
                from .source_support import diagnose_not_found
                diagnosis = diagnose_not_found(self.store, code, kind)
                # A catalogue mismatch is rechecked, not deleted or declared
                # permanently unavailable. Preserve last good rows/cursors.
                delay = 6 * 3600 if diagnosis['status'] == 'catalogue_mismatch' else delay
            if http_status in (401, 403, 429):
                global_retry = (datetime.now(UTC) + timedelta(seconds=300 if http_status != 429 else 60)).isoformat()
            if diagnosis and diagnosis['status'] == 'provider_catalogue_omission':
                self.store.put('backfill-status-review:' + code + ':' + kind, state)
                self.store.put(key, {**state, 'status': 'not_in_provider_catalogue',
                    'error': type(exc).__name__, 'http_status': http_status,
                    'diagnosis': diagnosis, 'provider_catalogue_revision': diagnosis['catalogue_revision'],
                    'failures': failures, 'attempted_at': clock, 'retry_after': None,
                    'classified_at': clock})
                status = 'not_in_provider_catalogue'
            else:
                retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                retry_after = retry_at.isoformat()
                self.store.put(key, {**state, 'status': 'error', 'error': type(exc).__name__,
                    'http_status': http_status, 'diagnosis': diagnosis, 'failures': failures, 'attempted_at': clock,
                    'retry_after': retry_after})
                heapq.heappush(self._retry_queue, (retry_at, key, retry_after))
                status = 'error'
        # Visit all data kinds for one security before moving to the next.
        # Previously a lending/credit continuation waited behind thousands of
        # unrelated securities in each of the intervening whole-market phases.
        # Keep the current (index, phase) on migration; data cursors are separate.
        next_phase = phase if priority else (phase + 1) % len(KINDS)
        next_index = index if priority or next_phase != 0 else (index + 1) % len(codes)
        self.store.put('toss-backfill-progress', {
            'cursor': next_index, 'phase': next_phase, 'universe_total': len(codes),
            'next_code':codes[next_index],
            'schedule_version':3, 'schedule':'security_then_kind',
            'cycles': int(progress.get('cycles', 0)) + int(not priority and next_index == 0 and next_phase == 0),
            'priority_cursor': int(progress.get('priority_cursor',0)) + int(priority),
            'attempts': int(progress.get('attempts', 0)) + 1,
            'last_code': code, 'last_kind': kind, 'last_result': status,
            'status': 'collecting' if not global_retry else 'provider_backoff',
            'retry_after': global_retry, 'updated_at': now()})
        return status

    async def run(self):
        try:
            await repair_trading_job_statuses(self.store)
        except Exception as exc:
            # A status repair is not allowed to stop the collection worker.
            log.warning('Status repair deferred: %s', type(exc).__name__)
        try:
            classified = await classify_provider_catalogue_omissions(self.store)
            if classified:
                log.info('Classified %d Toss catalogue omissions from fresh catalogue evidence', classified)
        except Exception as exc:
            log.warning('Catalogue 404 classification deferred: %s', type(exc).__name__)
        while True:
            try:
                status = await self.step()
            except Exception as exc:
                log.warning('Queue failure: %s', type(exc).__name__)
                status = 'queue_error'
            await asyncio.sleep(pause_seconds(status))
