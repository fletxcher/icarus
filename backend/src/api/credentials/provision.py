"""One-click setup of a connected machine: Docker and the solver image, run in the background.

The machine does the work (`setup.sh` in its `setup/` folder) detached from the SSH session, so
the person can close the page and come back. It reports through `setup.status` and `setup.log`
in the work directory, which `progress` reads.

Getting root for Docker: nothing if the user is root or already has Docker, `sudo -n` if sudo
needs no password, otherwise the person types their sudo password once. That password is
checked with `sudo -S -v`, sent over the SSH channel's standard input to the one `sudo` that runs
the root half of the install, and kept nowhere else: not in a command line, an environment
variable, the database or any log.
"""

import json
import logging
import shlex

from ..compute.runner import SSHRunner
from .schemas import MachineInfo, SetupPlan, SetupProgress
from .workstation import COMMAND_TIMEOUT_S, ConnectionProblem, image_tag

logger = logging.getLogger(__name__)

LOG_TAIL_LINES = 25
SOLVER_IMAGE_NOTE = (
    "The solver image: OpenFOAM, gmsh, CalculiX, ParaView, ffmpeg, XFOIL and the Python "
    "environment for meshing and CAD (built on the machine, about 1.5 GB to download)"
)


def plan_for(machine: MachineInfo) -> SetupPlan:
    """What setup would install on this machine, and what it needs from the person."""
    needs_root = machine.docker != "usable"
    installs = []
    if machine.docker == "missing":
        installs.append("Docker Engine (the docker.io package from the machine's apt repositories)")
    elif machine.docker == "installed":
        installs.append("Access to Docker for this user (adds them to the docker group)")
    installs.append(f"{SOLVER_IMAGE_NOTE} as {image_tag()}")
    reason = None
    if not machine.supported:
        reason = " ".join(machine.problems)
    elif needs_root and machine.privilege == "none":
        reason = (
            "Docker needs to be installed or opened up for this user, which takes root, and this "
            "user has neither root nor sudo. Ask an administrator, or install Docker yourself."
        )
    return SetupPlan(
        installs=installs,
        needs_root=needs_root,
        needs_password=needs_root and machine.privilege == "password_sudo",
        can_run=reason is None,
        reason=reason,
    )


def launch_command(workdir: str, username: str, machine: MachineInfo, *, password: bool) -> str:
    """The shell command that starts setup detached and returns at once. It holds no secret: a
    password arrives on the standard input of the SSH command and goes straight into `sudo -S`."""
    setup = shlex.quote(f"{workdir}/setup/setup.sh")
    home = shlex.quote(workdir)
    system = f"bash {setup} --install-system --home {home} --user {shlex.quote(username)}"
    user_part = f"bash {setup} --install-user --home {home}"
    if machine.docker == "usable":
        steps = user_part
    elif machine.privilege == "root":
        steps = f"{system} && {user_part}"
    else:
        sudo = "sudo -S -p ''" if password else "sudo -n"
        # The docker group is new to this process, so the second half starts under `sg docker`.
        steps = f"{sudo} {system} && sg docker -c {shlex.quote(user_part)}"
    # `read` takes the password off the channel; the pipe hands it to the detached script's
    # standard input, which only `sudo -S` reads.
    feed = "IFS= read -r secret; printf '%s\\n' \"$secret\" | " if password else ""
    detach = f"setsid nohup bash -c {shlex.quote(steps)} > {home}/setup.log 2>&1"
    tail = "" if password else " < /dev/null"
    return (
        f"cd {home} && rm -f setup.status && {feed}{detach}{tail} & "
        f"{'unset secret; ' if password else ''}sleep 1; echo started"
    )


async def start(
    runner: SSHRunner, machine: MachineInfo, username: str, password: str | None
) -> None:
    """Checks the machine and the password, then starts setup in the background."""
    plan = plan_for(machine)
    if not plan.can_run:
        raise ConnectionProblem(plan.reason or "Setup can't run on this machine.")
    workdir = runner.target.workdir
    state = (await progress(runner)).state
    if state == "running":
        raise ConnectionProblem("Setup is already running on this machine.")
    needs_password = plan.needs_password
    if needs_password:
        if not password:
            raise ConnectionProblem("Enter the machine's sudo password to continue.")
        checked = await runner.run(
            "sudo -S -p '' -v", stdin=f"{password}\n", timeout_s=COMMAND_TIMEOUT_S
        )
        if not checked.ok:
            raise ConnectionProblem("That sudo password was rejected.")
    elif plan.needs_root and machine.privilege == "passwordless_sudo":
        # Confirmed up front like the password, so a sudoers rule that changed shows here.
        checked = await runner.run("sudo -n true", timeout_s=COMMAND_TIMEOUT_S)
        if not checked.ok:
            raise ConnectionProblem("sudo now asks for a password; connect again.")
    command = launch_command(workdir, runner.target.username, machine, password=needs_password)
    started = await runner.run(
        command,
        timeout_s=COMMAND_TIMEOUT_S,
        stdin=f"{password}\n" if needs_password else None,
    )
    if not started.ok:
        raise ConnectionProblem(f"Couldn't start setup: {started.stderr.strip() or 'failed'}")
    logger.info("setup started on %s@%s", runner.target.username, runner.target.host)


async def progress(runner: SSHRunner) -> SetupProgress:
    """What setup is doing: the state file, and the end of the log."""
    workdir = shlex.quote(runner.target.workdir)
    result = await runner.run(
        f"cat {workdir}/setup.status 2>/dev/null; echo; echo ---; "
        f"tail -n {LOG_TAIL_LINES} {workdir}/setup.log 2>/dev/null",
        timeout_s=COMMAND_TIMEOUT_S,
    )
    status_text, _, log = result.stdout.partition("\n---\n")
    try:
        status = json.loads(status_text.strip() or "{}")
    except ValueError:
        status = {}
    if not isinstance(status, dict) or "state" not in status:
        # No state file: setup never ran, or it was just started and hasn't written one yet.
        return SetupProgress(
            state="running" if log.strip() else "idle", stage="", message="", log_tail=log.strip()
        )
    return SetupProgress(
        state=status.get("state", "idle"),
        stage=str(status.get("stage", "")),
        message=str(status.get("message", "")),
        log_tail=log.strip(),
        updated=status.get("updated"),
    )
