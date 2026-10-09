"""One revision reader per API process; subscribers share the same normalized rows."""
import asyncio
import contextlib
from uuid import uuid4


class QuoteHub:
    def __init__(self, read_rows, read_trading=None):
        self.read_rows = read_rows
        self.read_trading = read_trading
        self.trading = {}
        self.epoch = uuid4().hex
        self.version = 0
        self.rows = {}
        self.sequences = {}
        self.changed = asyncio.Condition()
        self.task = None

    def publish(self, rows):
        current = {row['code']: row for row in rows}
        for code, row in current.items():
            if self.rows.get(code) != row:
                self.sequences[code] = self.sequences.get(code, 0) + 1
        if current == self.rows:
            return False
        self.rows = current
        self.version += 1
        return True

    async def run(self):
        while True:
            try:
                rows, _ = await asyncio.to_thread(self.read_rows, check_revision=True)
                changed = self.publish(rows)
                if self.read_trading:
                    incoming = await asyncio.to_thread(self.read_trading)
                    if incoming != self.trading:
                        self.trading = incoming
                        self.version += 1
                        changed = True
                if changed:
                    async with self.changed:
                        self.changed.notify_all()
            except Exception:
                # Keep last good rows. Clients still use independent REST recovery.
                pass
            await asyncio.sleep(.25)

    async def wait(self, version):
        async with self.changed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait_for(lambda: self.version != version), 10)
        return self.version

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
