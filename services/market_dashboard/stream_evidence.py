"""Persist actual message evidence separately from subscription acknowledgements."""
import re
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def message_digest(evidence):
    """Accept the canonical trace ID, keeping the legacy SHA alias consistent."""
    values = [evidence[key] for key in ('source_document_id', 'sha256') if key in evidence]
    if not values or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                         for value in values) or len(set(values)) != 1:
        return None
    return values[0]


class StreamEvidence:
    def __init__(self, store):
        self.store = store
        self.pending = {}
        self.flushed_at = 0.0

    def record(self, *, kind, code, epoch, source_time, evidence, received_at=None):
        if kind not in ('trade', 'orderbook'):
            return
        received = datetime.fromisoformat(received_at) if received_at else datetime.now(UTC)
        if received.tzinfo is None:
            return
        day = received.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
        lag = None
        try:
            source = datetime.fromisoformat(source_time.replace('Z','+00:00'))
            if source.tzinfo is not None:
                lag = (received-source).total_seconds()
        except (ValueError, TypeError, AttributeError):
            pass
        live = (lag is not None and -5 <= lag <= 120
                and evidence.get('source') == 'toss_websocket' and bool(message_digest(evidence)))
        key = (day, code, kind)
        row = self.pending.setdefault(key, {'messages':0,'fresh_messages':0,'epochs':[],
            'first_received_at':received.isoformat()})
        row['messages'] += 1
        row['fresh_messages'] += int(live)
        row.update(last_received_at=received.isoformat(), last_source_time=source_time,
                   last_lag_seconds=lag, source_trace=evidence)
        if epoch not in row['epochs']:
            row['epochs'].append(epoch)
            row['epochs'] = row['epochs'][-20:]
        if time.monotonic()-self.flushed_at >= 5:
            self.flush()

    def flush(self):
        for (day, code, kind), row in list(self.pending.items()):
            key = f'toss-stream-evidence:{day}:{code}:{kind}'
            old = self.store.get(key,{})
            self.store.put(key, {**row,'security_code':code,'kind':kind,'day':day,
                'messages':old.get('messages',0)+row['messages'],
                'fresh_messages':old.get('fresh_messages',0)+row['fresh_messages'],
                'first_received_at':old.get('first_received_at',row['first_received_at']),
                'epochs':list(dict.fromkeys(old.get('epochs',[])+row['epochs']))[-20:],
                'verification':'source_messages_only',
                'limitations':['no_provider_sequence','no_proof_of_gapless_delivery','screen_verification_required']})
            self.pending.pop((day, code, kind))
        self.flushed_at = time.monotonic()
