"""Removing a saved machine: clean up what Icarus put on it, then forget it.

Deleting a machine starts this in the background. Over SSH, with the machine's own key, it
runs `setup.sh --uninstall --purge` (stops job containers, removes the solver image, the job
folders and Icarus's files) and then takes the key out of `~/.ssh/authorized_keys`. Only after
both does the machine's record, and with it the encrypted key, leave the database.

If the machine can't be reached or a step fails, the record stays with the reason, so the
person can try again or remove it anyway (which skips the cleanup).
"""

import asyncio
import logging
import shlex
from typing import TYPE_CHECKING

import asyncssh

from ..compute.pool import target_for
from ..compute.runner import SSHRunner
from . import workstation
from .workstation import ConnectionProblem

if TYPE_CHECKING:
    from ..services import Services

logger = logging.getLogger(__name__)

UNINSTALL_TIMEOUT_S = 900.0
KEY_TIMEOUT_S = 30.0

# Held so a running cleanup isn't garbage collected.
_running: set[asyncio.Task[None]] = set()


def authorized_keys_command(public_key: str) -> str:
    """Removes the lines of ~/.ssh/authorized_keys that hold this key. The key is matched by its
    base64 part, so a comment or options in front of it don't matter; other keys are untouched."""
    blob = public_key.split()[1] if len(public_key.split()) > 1 else public_key
    return (
        'f="$HOME/.ssh/authorized_keys"; '
        f'if [ -f "$f" ]; then grep -vF -- {shlex.quote(blob)} "$f" > "$f.icarus-new" || true; '
        'cat "$f.icarus-new" > "$f"; rm -f "$f.icarus-new"; fi'
    )


def uninstall_command(workdir: str) -> str:
    setup = shlex.quote(f"{workdir}/setup/setup.sh")
    home = shlex.quote(workdir)
    # Nothing to clean when setup.sh was never put there.
    return (
        f"if [ -f {setup} ]; then bash {setup} --uninstall --purge --yes --home {home}; "
        "else echo 'nothing to clean up'; fi"
    )


async def _clean(services: "Services", user_id: str, machine_id: str) -> None:
    stored = await services.credentials.stored_workstation(user_id, machine_id)
    if stored is None:
        raise ConnectionProblem("Couldn't open the saved key for this machine.")
    runner = SSHRunner(target_for(stored))
    try:
        await services.credentials.set_workstation_status(
            user_id, machine_id, "removing", "Cleaning up the machine"
        )
        result = await runner.run(
            uninstall_command(stored.details.workdir), timeout_s=UNINSTALL_TIMEOUT_S
        )
        if not result.ok:
            tail = result.stderr.strip().splitlines()[-1:] or ["the cleanup script failed"]
            raise ConnectionProblem(f"Cleaning up the machine failed: {tail[0]}")
        await services.credentials.set_workstation_status(
            user_id, machine_id, "removing", "Removing the SSH key from the machine"
        )
        removed = await runner.run(
            authorized_keys_command(stored.details.public_key), timeout_s=KEY_TIMEOUT_S
        )
        if not removed.ok:
            raise ConnectionProblem(
                f"Couldn't remove the key from the machine: {removed.stderr.strip() or 'failed'}"
            )
    finally:
        await runner.close()


async def _remove(services: "Services", user_id: str, machine_id: str) -> None:
    try:
        await _clean(services, user_id, machine_id)
    except ConnectionProblem as problem:
        reason = str(problem)
    except (OSError, TimeoutError, asyncssh.Error) as error:
        stored = await services.credentials.workstation_info(user_id, machine_id)
        host, port = (stored.host, stored.port) if stored else ("the machine", 22)
        reason = workstation.describe(error, host, port)
    except Exception:
        logger.exception("removing machine %s of user %s failed", machine_id, user_id)
        reason = "Something went wrong while cleaning up the machine."
    else:
        await services.credentials.delete_workstation(user_id, machine_id)
        ended = await services.compute.machine_removed(user_id, machine_id)
        logger.info("user %s removed machine %s (%d job(s) ended)", user_id, machine_id, ended)
        return
    logger.warning("machine %s of user %s wasn't cleaned up: %s", machine_id, user_id, reason)
    await services.credentials.set_workstation_status(user_id, machine_id, "removal_failed", reason)


async def start_removal(services: "Services", user_id: str, machine_id: str) -> bool:
    """Marks the machine as being removed and cleans it up in the background. False if the
    machine doesn't exist."""
    if not await services.credentials.set_workstation_status(
        user_id, machine_id, "removing", "Starting"
    ):
        return False
    await services.compute.forget(user_id)
    task = asyncio.create_task(_remove(services, user_id, machine_id))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return True
