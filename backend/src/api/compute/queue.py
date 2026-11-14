"""The compute host's turn-taking: heavy jobs (the agent's Python, part builds) run a few at a
time, and a job that waits too long gives up with a clear message instead of hanging."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class HostBusy(Exception):
    """No turn came up in time; the message says to try again."""


class HostQueue:
    def __init__(self, slots: int, wait_s: float) -> None:
        self._slots = asyncio.Semaphore(max(1, slots))
        self._wait_s = wait_s

    @asynccontextmanager
    async def turn(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._slots.acquire(), self._wait_s)
        except TimeoutError:
            raise HostBusy(
                "the compute host is busy with other jobs right now; try again in a minute"
            ) from None
        try:
            yield
        finally:
            self._slots.release()
