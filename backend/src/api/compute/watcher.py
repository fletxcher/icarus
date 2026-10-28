"""Tells a chat when its simulation has finished, so the agent never has to poll for it.

Jobs run for minutes to hours on the work station. Every few seconds the backend (not the agent,
so no model tokens are spent) looks at each job still going, and when one has ended it adds a
notice to the chat that started it. The notice is part of that chat's history, so the agent knows
the moment the user next writes, and the chat shows it as it happens.
"""

import asyncio
import contextlib
import logging

from ..chats.repository import ChatRepository
from .describe import finished_notice
from .pool import ComputePool

logger = logging.getLogger(__name__)


async def watch_once(compute: ComputePool, chats: ChatRepository) -> int:
    """One pass: records ended jobs and leaves a notice in their chats. Returns how many notices."""
    told = 0
    for user_id in await compute.users_to_watch():
        try:
            simulations = await compute.simulations_for(user_id)
            await simulations.sync_active()
            for simulation in await simulations.claim_unnotified():
                assert simulation.thread_id is not None
                try:
                    await chats.add_notice(simulation.thread_id, finished_notice(simulation))
                    told += 1
                    logger.info(
                        "told thread %s that job %s ended", simulation.thread_id, simulation.id
                    )
                except LookupError:
                    logger.info("job %s ended, but its chat is gone", simulation.id)
        except Exception:
            # Typically the work station can't be reached right now: try again next pass.
            logger.warning("couldn't check the simulations of user %s", user_id, exc_info=True)
    return told


async def watch(compute: ComputePool, chats: ChatRepository, interval_s: float) -> None:
    """Runs `watch_once` forever, every `interval_s` seconds."""
    while True:
        try:
            await watch_once(compute, chats)
        except Exception:
            logger.exception("the simulation watcher failed; it will try again")
        await asyncio.sleep(interval_s)


@contextlib.asynccontextmanager
async def watching(compute: ComputePool, chats: ChatRepository, interval_s: float):
    """Keeps a watcher running for as long as the block does (not at all if the interval is 0)."""
    task = asyncio.create_task(watch(compute, chats, interval_s)) if interval_s > 0 else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
