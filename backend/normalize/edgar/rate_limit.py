from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    min_interval_seconds: float = 0.12
    _last_request: float = field(default=0.0, init=False)

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        self._last_request = time.monotonic()

