"""Hysteresis for bulk history work; serving stored data is independent."""
import os
import shutil
import tempfile
from pathlib import Path


def backfill_capacity(root, usage, previous_status):
    ratio = (usage.total - usage.free) / usage.total
    limited = ratio >= .90 or usage.free < 768 * 1024**2
    if previous_status == 'capacity_limited' and ratio >= .80:
        limited = True
    writable = None
    if not limited and previous_status == 'capacity_limited':
        probe = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, prefix='.capacity-', delete=False) as stream:
                probe = Path(stream.name)
                stream.write(b'market-history-write-check\n')
                stream.flush()
                os.fsync(stream.fileno())
            writable = True
        except OSError:
            limited, writable = True, False
        finally:
            if probe is not None:
                probe.unlink(missing_ok=True)
    return {'limited': limited, 'used_percent': round(ratio * 100, 2),
            'free_bytes': usage.free, 'warning': ratio >= .80, 'write_verified': writable}


def collection_capacity(store, queue):
    """Persist a per-queue pause so restarting cannot bypass the 80% resume gate."""
    from .store import now
    key = 'storage-capacity:' + queue
    previous = store.get(key, {})
    capacity = backfill_capacity(store.root, shutil.disk_usage(store.root), previous.get('status'))
    state = {**capacity, 'status': 'capacity_limited' if capacity['limited'] else 'ready'}
    # Avoid a write on every loop tick. Diagnostics update when disk/state changes.
    if any(previous.get(k) != state[k] for k in ('status', 'used_percent', 'write_verified')):
        store.put(key, {**state, 'updated_at': now()})
    return state
