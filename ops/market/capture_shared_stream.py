"""Bounded observation of the existing worker; never connect to Toss or create Store.

Default: print a plan. --capture: read exact PostgreSQL keys twice, then emit a
sanitized report. No subscriptions, refresh requests, restart, or DB writes.
An operator may independently arrange a worker restart during the observation;
this tool can observe an epoch change, but cannot prove a forced disconnect or
browser rendering. Do not use collect_toss.py as a production probe.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import signal
import stat
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_BYTES = 262144
KINDS = ('trade', 'orderbook')
KST = ZoneInfo('Asia/Seoul')


class Refused(Exception):
    pass


def require(ok, reason):
    if not ok:
        raise Refused(reason)


def timestamp(value):
    require(isinstance(value, str) and len(value) <= 40, 'timestamp_missing')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Refused('timestamp_invalid') from None
    require(result.tzinfo is not None, 'timestamp_timezone_missing')
    return result


def digest_of(trace):
    require(isinstance(trace, dict), 'trace_missing')
    digest = trace.get('source_document_id') or trace.get('sha256')
    require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'digest_invalid')
    for field in ('source_document_id', 'sha256', 'filing_id'):
        require(field not in trace or trace[field] == digest, 'digest_alias_mismatch')
    return digest


def file_bytes(root, path, *, compressed=False):
    root = root.resolve(strict=True)
    require(path.is_relative_to(root), 'archive_path_outside_root')
    for ancestor in (path, *path.parents):
        if ancestor == root:
            break
        info = ancestor.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not (
            getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT),
            'archive_link_refused')
    require(path.resolve(strict=True).is_relative_to(root), 'archive_path_outside_root')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode), 'archive_not_regular')
    require(info.st_size <= MAX_BYTES, 'archive_size_limit')
    with (gzip.open(path, 'rb') if compressed else path.open('rb')) as stream:
        body = stream.read(MAX_BYTES + 1)
    require(len(body) <= MAX_BYTES, 'archive_expansion_limit')
    return body


def original(root, namespace, digest):
    require(namespace in ('toss', 'toss-stream'), 'archive_namespace')
    require(re.fullmatch('[0-9a-f]{64}', digest) is not None, 'digest_invalid')
    directory = root.resolve(strict=True) / 'raw' / namespace
    meta = json.loads(file_bytes(root, directory / (digest + '.json')))
    binary = directory / (digest + '.bin')
    compressed = directory / (digest + '.bin.gz')
    body = file_bytes(root, binary) if binary.exists() else file_bytes(
        root, compressed, compressed=True)
    require(hashlib.sha256(body).hexdigest() == digest, 'archive_hash_mismatch')
    require(meta.get('sha256') == digest and meta.get('bytes') == len(body),
            'archive_envelope_mismatch')
    if meta.get('payload') is not None:
        require(meta['payload'].encode('utf8') == body, 'archive_payload_mismatch')
    return json.loads(body), meta


def numeric(value, *, positive=False):
    require(not isinstance(value, bool), 'numeric_invalid')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Refused('numeric_invalid') from None
    require(number.is_finite() and (number > 0 if positive else number >= 0), 'numeric_invalid')
    return number


def provider_calendar(snapshots, root, day, captured):
    """Require the saved provider body, exact date and offset-aware session hours."""
    for key in (f'toss-calendar:KR:{day}', 'toss-calendar:KR'):
        value = snapshots.get(key, {})
        if not value:
            continue
        try:
            trace = value.get('source_trace', {})
            require(trace.get('source') == 'toss', 'calendar_source')
            digest = digest_of(trace)
            raw, meta = original(root, 'toss', digest)
            require(meta.get('url', '').split('?')[0] ==
                    'https://openapi.tossinvest.com/api/v1/market-calendar/KR', 'calendar_url')
            require(raw.get('result') == value.get('calendar'), 'calendar_body_mismatch')
            calendar = value['calendar']
            selected = next((item for item in calendar.values()
                             if isinstance(item, dict) and item.get('date') == day), None)
            require(selected is not None, 'calendar_day_missing')
            periods = selected.get('integrated')
            result = {'status': 'closed', 'day': day, 'source_sha256': digest,
                      'scope': 'KRX+NXT integrated', 'sessions': [], 'open_at_capture': False}
            following = calendar.get('nextBusinessDay')
            if isinstance(following, dict):
                candidate = following.get('date')
                if (isinstance(candidate, str)
                        and date.fromisoformat(candidate).isoformat() == candidate):
                    result['provider_next_business_day'] = candidate
            if periods is None and 'integrated' in selected:
                return result
            require(isinstance(periods, dict), 'calendar_sessions_missing')
            for session in ('preMarket', 'regularMarket', 'afterMarket'):
                require(session in periods, 'calendar_session_missing')
                if periods[session] is None:
                    continue
                start = timestamp(periods[session].get('startTime'))
                end = timestamp(periods[session].get('endTime'))
                require(start < end and start.astimezone(KST).date().isoformat() == day
                        and end.astimezone(KST).date().isoformat() == day,
                        'calendar_period_invalid')
                result['sessions'].append({'name': session, 'start': start.isoformat(),
                                           'end': end.isoformat()})
                result['open_at_capture'] |= start <= captured < end
            if result['sessions']:
                result['status'] = 'open_day'
            return result
        except (Refused, OSError, ValueError, TypeError, AttributeError):
            continue
    return {'status': 'unverified', 'day': day, 'open_at_capture': False, 'sessions': []}


def evidence(row, root, day, code, kind, captured, calendar):
    result = {'code': code, 'kind': kind, 'verified_raw': False, 'fresh_at_capture': False}
    try:
        require(row.get('day') == day and row.get('security_code') == code
                and row.get('kind') == kind, 'evidence_identity')
        trace = row.get('source_trace', {})
        require(trace.get('source') == 'toss_websocket', 'evidence_source')
        digest = digest_of(trace)
        raw, meta = original(root, 'toss-stream', digest)
        topic = f'{kind}:kr:{code}'
        require(raw.get('type') == 'message' and raw.get('topic') == topic
                and meta.get('url') == topic, 'message_topic')
        data = raw.get('data', {})
        require(data.get('currency') == 'KRW', 'message_currency')
        source = timestamp(data.get('timestamp'))
        received = timestamp(row.get('last_received_at'))
        require(timestamp(row.get('last_source_time')) == source, 'message_time_mismatch')
        require(source.astimezone(KST).date().isoformat() == day
                and received.astimezone(KST).date().isoformat() == day, 'message_day')
        if kind == 'trade':
            sample = {'price': str(numeric(data.get('price'), positive=True)),
                      'quantity': str(numeric(data.get('volume')))}
        else:
            for side in ('asks', 'bids'):
                require(isinstance(data.get(side), list) and 0 < len(data[side]) <= 100,
                        'orderbook_levels_missing')
                for level in data[side]:
                    numeric(level.get('price'), positive=True)
                    numeric(level.get('volume'))
            ask = min(data['asks'], key=lambda row: Decimal(row['price']))
            bid = max(data['bids'], key=lambda row: Decimal(row['price']))
            sample = {'ask': str(numeric(ask['price'])), 'bid': str(numeric(bid['price'])),
                      'ask_quantity': str(numeric(ask['volume'])),
                      'bid_quantity': str(numeric(bid['volume']))}
        count, fresh = row.get('messages'), row.get('fresh_messages')
        require(type(count) is int and count > 0 and type(fresh) is int and 0 <= fresh <= count,
                'message_counter')
        epochs = row.get('epochs')
        require(isinstance(epochs, list) and 0 < len(epochs) <= 20
                and all(isinstance(item, str) and re.fullmatch('[0-9a-f]{32}', item)
                        for item in epochs), 'message_epoch')
        lag, age = (received - source).total_seconds(), (captured - received).total_seconds()
        in_session = any(timestamp(item['start']) <= source < timestamp(item['end'])
                         for item in calendar['sessions'])
        result.update(verified_raw=True, source_sha256=digest, messages=count,
                      worker_fresh_counter=fresh, epoch=epochs[-1], sample=sample,
                      source_at=source.isoformat(), received_at=received.isoformat(),
                      source_lag_seconds=lag, receipt_age_seconds=age,
                      fresh_at_capture=(-5 <= lag <= 120 and 0 <= age <= 120 and in_session
                                        and calendar['open_at_capture']))
    except (Refused, OSError, ValueError, TypeError, AttributeError) as exc:
        result['reason'] = str(exc) if isinstance(exc, Refused) else 'evidence_unreadable'
    return result


def snapshot_keys(day, codes):
    require(date.fromisoformat(day).isoformat() == day, 'day_invalid')
    require(0 < len(codes) <= 3 and len(codes) == len(set(codes)) and all(
        re.fullmatch('[0-9A-Z]{6}', code) for code in codes), 'codes_invalid')
    return ('provider-toss-stream', 'toss-calendar:KR', f'toss-calendar:KR:{day}',
            *(f'toss-recovery:{code}' for code in codes),
            *(f'toss-stream-evidence:{day}:{code}:{kind}' for code in codes for kind in KINDS))


def read_snapshot(db, keys):
    """A new bounded read-only snapshot per observation; no Store or broker calls."""
    try:
        db.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        captured, readonly, isolation = db.execute('SELECT transaction_timestamp(), '
            "current_setting('transaction_read_only'),current_setting('transaction_isolation')"
        ).fetchone()
        require((readonly, isolation) == ('on', 'repeatable read'), 'readonly_required')
        rows = db.execute('SELECT key, CASE WHEN octet_length(payload)<=%s '
            'THEN payload ELSE NULL END FROM market_snapshots WHERE key=ANY(%s)',
            (MAX_BYTES, list(keys)))
        values = {}
        for key, body in rows:
            require(key in keys and key not in values, 'snapshot_identity')
            require(body is not None, 'snapshot_size_limit')
            value = json.loads(body)
            require(isinstance(value, dict), 'snapshot_format')
            values[key] = value
        return captured, values
    finally:
        db.execute('ROLLBACK')


def observation(snapshots, root, day, codes, captured):
    calendar = provider_calendar(snapshots, root, day, captured)
    raw_state = snapshots.get('provider-toss-stream', {})
    epoch = raw_state.get('epoch')
    state = {'epoch': epoch if isinstance(epoch, str) and re.fullmatch('[0-9a-f]{32}', epoch)
             else None, 'status': raw_state.get('status') if raw_state.get('status') in
             ('connecting', 'recovering', 'subscribing', 'subscribed', 'receiving',
              'reconnecting', 'idle') else 'unknown'}
    rows = [evidence(snapshots.get(f'toss-stream-evidence:{day}:{code}:{kind}', {}),
                     root, day, code, kind, captured, calendar) for code in codes for kind in KINDS]
    recovery = {}
    for code in codes:
        value = snapshots.get(f'toss-recovery:{code}', {})
        try:
            completed = timestamp(value.get('completed_at'))
            require(value.get('status') == 'ready' and value.get('epoch') == state['epoch']
                    and completed <= captured, 'recovery_unconfirmed')
            recovery[code] = {'epoch': state['epoch'], 'completed_at': completed.isoformat()}
        except Refused:
            recovery[code] = None
    return {'captured_at': captured.isoformat(), 'calendar': calendar, 'worker': state,
            'evidence': rows, 'recovery': recovery}


def compare(before, after):
    start, end = timestamp(before['captured_at']), timestamp(after['captured_at'])
    require(0 < (end-start).total_seconds() <= 180, 'observation_window')
    rows = []
    first = {(row['code'], row['kind']): row for row in before['evidence']}
    for row in after['evidence']:
        old = first[(row['code'], row['kind'])]
        delta = row.get('messages', 0) - old.get('messages', 0)
        received = timestamp(row['received_at']) if row.get('verified_raw') else None
        observed = bool(row.get('fresh_at_capture') and delta > 0 and received
                        and start < received <= end and row['epoch'] == after['worker']['epoch'])
        recovery = after['recovery'].get(row['code'])
        recovered = bool(observed and old.get('fresh_at_capture') and before['worker']['epoch']
                         and before['worker']['epoch'] != after['worker']['epoch'] and recovery
                         and start < timestamp(recovery['completed_at']) <= received)
        rows.append({'code': row['code'], 'kind': row['kind'], 'message_count_delta': delta,
                     'fresh_message_observed': observed, 'epoch_recovery_observed': recovered})
    return {'rows': rows, 'both_kinds_observed': bool(rows) and all(
        row['fresh_message_observed'] for row in rows),
        'epoch_recovery_observed': bool(rows) and all(
            row['epoch_recovery_observed'] for row in rows),
        'forced_disconnect_verified': False, 'browser_render_verified': False,
        'gapless_delivery_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--day', default=datetime.now(KST).date().isoformat())
    parser.add_argument('--codes', nargs='+', default=['005930'])
    parser.add_argument('--seconds', type=int, default=20)
    parser.add_argument('--expected-release')
    args = parser.parse_args()
    keys = snapshot_keys(args.day, args.codes)
    require(5 <= args.seconds <= 60, 'capture_duration_limit')
    report = {'mode': 'readonly_shared_worker', 'day': args.day, 'keys': keys,
              'samples': 2, 'interval_seconds': args.seconds, 'provider_requests': 0,
              'provider_connections': 0, 'database_writes': 0,
              'limitations': ['two_latest_raw_samples_not_every_tick', 'no_provider_sequence',
                              'separate_browser_and_forced_disconnect_verification_required']}
    if not args.capture:
        print(json.dumps(report, ensure_ascii=False))
        return
    def expired(*_):
        raise Refused('capture_deadline')
    # This runner is intended for the existing Linux API container, via stdin.
    require(hasattr(signal, 'SIGALRM'), 'capture_deadline_unavailable')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(90)
    release = os.environ.get('MARKET_RELEASE', '')
    require(args.expected_release and re.fullmatch('[A-Za-z0-9_-]{1,100}', release)
            and release == args.expected_release, 'release_mismatch')
    url = os.environ.get('MARKET_DATABASE_URL', '')
    require(url.startswith(('postgresql://', 'postgresql+psycopg://')), 'postgresql_required')
    root = Path(os.environ.get('MARKET_STORAGE_DIR', 'storage/public-market')).resolve(strict=True)
    import psycopg
    with psycopg.connect(url.replace('postgresql+psycopg://', 'postgresql://', 1),
                         connect_timeout=5, autocommit=True,
                         options='-c statement_timeout=3000 -c lock_timeout=1000 '
                                 '-c idle_in_transaction_session_timeout=5000') as db:
        captures = []
        for index in range(2):
            if index:
                time.sleep(args.seconds)
            captured, values = read_snapshot(db, keys)
            captures.append(observation(values, root, args.day, args.codes, captured))
    report.update(release=release, observations=captures, gates=compare(*captures))
    signal.alarm(0)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never print an exception containing a DSN, token, arbitrary row or path.
        print(json.dumps({'status': 'refused', 'reason': str(exc) if isinstance(exc, Refused)
                          else 'capture_failed'}))
        raise SystemExit(1) from None
