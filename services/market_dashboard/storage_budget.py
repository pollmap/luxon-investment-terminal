"""Hysteresis for bulk history work; serving stored data is independent."""

import os
import shutil
import tempfile
from pathlib import Path

PROFILE_ENV = 'MARKET_STORAGE_PROFILE'
# Fixed profiles, not independently tunable limits that could remove reserves.
PROFILES = {
    'default': (90, 80, 768 * 1024**2, 768 * 1024**2),
    'high-utilization': (97, 95, 3 * 1024**3, 4 * 1024**3),
}
POLICY_FIELDS = (
    'profile', 'stop_used_percent', 'resume_below_used_percent',
    'minimum_free_bytes', 'resume_minimum_free_bytes', 'configuration_error',
)


def backfill_capacity(root, usage, previous_status):
    """Check shared filesystem limits; a paused queue also needs a durable write probe."""
    ratio = (usage.total - usage.free) / usage.total
    profile = os.environ.get(PROFILE_ENV, 'default')
    limits = PROFILES.get(profile)
    if limits is None:
        # Do not silently fall back or echo arbitrary configuration text.
        return {
            'limited': True, 'used_percent': round(ratio * 100, 2),
            'free_bytes': usage.free, 'warning': True, 'write_verified': None,
            'profile': 'invalid', 'stop_used_percent': None,
            'resume_below_used_percent': None, 'minimum_free_bytes': None,
            'resume_minimum_free_bytes': None,
            'configuration_error': 'invalid_storage_profile',
        }
    stop, resume, minimum, resume_minimum = limits
    limited = ratio >= stop / 100 or usage.free < minimum
    if previous_status == 'capacity_limited':
        limited = limited or ratio >= resume / 100 or usage.free < resume_minimum
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
                try:
                    probe.unlink(missing_ok=True)
                except OSError:
                    limited, writable = True, False
    return {'limited': limited, 'used_percent': round(ratio * 100, 2),
            'free_bytes': usage.free, 'warning': ratio >= resume / 100,
            'write_verified': writable, 'profile': profile,
            'stop_used_percent': stop, 'resume_below_used_percent': resume,
            'minimum_free_bytes': minimum, 'resume_minimum_free_bytes': resume_minimum,
            'configuration_error': None}


def collection_capacity(store, queue):
    """Persist a per-queue pause so restarting cannot bypass the selected resume gate."""
    from .store import now

    key = 'storage-capacity:' + queue
    previous = store.get(key, {})
    capacity = backfill_capacity(store.root, shutil.disk_usage(store.root), previous.get('status'))
    state = {**capacity, 'status': 'capacity_limited' if capacity['limited'] else 'ready'}
    # Avoid a write on every loop tick. Diagnostics update when disk/state changes.
    fields = ('status', 'used_percent', 'write_verified', *POLICY_FIELDS)
    if any(previous.get(k) != state[k] for k in fields):
        store.put(key, {**state, 'updated_at': now()})
    return state
