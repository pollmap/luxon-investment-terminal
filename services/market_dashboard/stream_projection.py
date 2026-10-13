"""Archive every execution, coalesce only visible quote/depth projections.

One shared worker owns this buffer. No simulated sequence, side or cumulative
volume is introduced. Receipt time is sampled before archive/database work.
"""
import json
import time
from datetime import datetime
from uuid import uuid4

from .providers import trace
from .stream_evidence import StreamEvidence


class StreamProjection:
    def __init__(self, source, *, interval=0.25, max_messages=256, clock=time.monotonic):
        if not 0.1 <= interval <= 0.25 or not 1 <= max_messages <= 256:
            raise ValueError('Invalid stream projection budget')
        self.source, self.store = source, source.store
        self.interval, self.max_messages = interval, max_messages
        self.clock = clock
        self.observed = StreamEvidence(self.store)
        self.pending, self.count, self.started, self.epoch = {}, 0, None, None
        self.prepared = None

    def wait_seconds(self):
        return 2 if self.started is None else max(0.001, self.interval - (self.clock() - self.started))

    def record(self, event, codes, epoch, received_at):
        from .toss import valid_stream_data
        topic = str(event.get('topic') or '').split(':')
        if len(topic) != 3:
            return
        kind, market, code = topic
        if market != 'kr' or code not in codes or kind not in ('trade', 'orderbook'):
            return
        if self.prepared is not None:
            self.flush()
        if self.pending and epoch != self.epoch:
            self.flush()
        digest = self.store.raw('toss-stream', event['topic'], json.dumps(event).encode())
        data = event.get('data')
        if not valid_stream_data(kind, data):
            self.store.put('toss-stream-rejected:' + code,
                           {'kind': kind, 'reason': 'unsupported_or_invalid_payload',
                            'raw_sha256': digest, 'received_at': received_at})
            return
        evidence = trace('toss_websocket', 'wss://openapi-ws.tossinvest.com/ws/v1',
                         digest, data.get('timestamp'), scope='KRX+NXT 통합')
        item = (kind, data, evidence, received_at)
        self.pending.setdefault(code, []).append(item)
        self.epoch = epoch
        self.started = self.clock() if self.started is None else self.started
        self.count += 1
        self.observed.record(kind=kind, code=code, epoch=epoch,
                             source_time=data.get('timestamp'), evidence=evidence,
                             received_at=received_at, auto_flush=False)
        if self.count >= self.max_messages or self.clock() - self.started >= self.interval:
            self.flush()

    def flush(self):
        from .toss import numeric, normalize_book
        if self.prepared is None:
            self.prepared = self._prepare(numeric, normalize_book)
        prepared = self.prepared
        for key, payload in prepared['depths'].items():
            # A commit may have succeeded before a transport error was raised.
            # The internal write ID is not a provider event sequence number.
            if self.store.get(key, {}).get('_projection_id') != prepared['id']:
                self.store.put(key, payload)
        if prepared['patches']:
            self.store.update_quotes(prepared['patches'])
        if prepared['last_event_at']:
            self.store.put('provider-toss-stream', {
                **self.store.get('provider-toss-stream', {}), 'status': 'receiving',
                'last_event_at': prepared['last_event_at'],
                'lossy': True, 'epoch': self.epoch})
        self.pending, self.count, self.started, self.prepared = {}, 0, None, None
        if time.monotonic() - self.observed.flushed_at >= 5:
            self.observed.flush()

    def _prepare(self, numeric, normalize_book):
        patches = {}
        depths, projection_id = {}, uuid4().hex
        for code, items in self.pending.items():
            depth = dict(self.store.get('depth:' + code, {}))
            executions, latest = [], None
            for kind, data, evidence, received in items:
                stamp = data.get('timestamp')
                if kind == 'trade':
                    executions.append({'time': stamp[11:19], 'timestamp': stamp,
                                       'price': numeric(data['price']), 'quantity': numeric(data['volume']),
                                       'cumulative_volume': None, 'source_trace': evidence})
                    if latest is None or datetime.fromisoformat(stamp) >= datetime.fromisoformat(latest[0]['timestamp']):
                        latest = (data, evidence, received)
                    depth.update(trades_status='ready', last_trade_received_at=received,
                                 trades_trace=evidence)
                else:
                    prior = depth.get('book_as_of')
                    if prior and (not stamp or datetime.fromisoformat(stamp) < datetime.fromisoformat(prior)):
                        continue
                    depth.update(book=normalize_book(data), book_status='ready', book_as_of=stamp,
                                 book_trace=evidence)
            if executions:
                depth['trades'] = sorted([*reversed(executions), *depth.get('trades', [])],
                                         key=lambda item: item.get('timestamp') or item.get('time') or '', reverse=True)[:100]
            if latest is not None:
                data, evidence, received = latest
                self.source.save_quote({'symbol': code, 'lastPrice': data['price'], **data},
                                       evidence, batch=patches)
                if patches.get(code):
                    patches[code]['received_at'] = received
            depth.update(received_at=items[-1][3], provider='toss', venue='통합', epoch=self.epoch,
                         gap=True, _projection_id=projection_id)
            depths['depth:' + code] = depth
        return {'id': projection_id, 'depths': depths, 'patches': patches,
                'last_event_at': max((items[-1][3] for items in self.pending.values()), default=None)}

    def finish(self):
        self.flush()
        self.observed.flush()

    def try_finish(self):
        """Retain prepared/raw evidence for the reconnect retry if storage fails."""
        try:
            self.finish()
            return True
        except Exception:
            return False
