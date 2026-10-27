"""Connecting to a user's own work station: host keys, checks and the runner scripts."""

import asyncio
import ipaddress
import logging
import shlex
import socket
from dataclasses import replace
from pathlib import Path

import asyncssh

from ..compute.runner import CommandRunner, SSHRunner, WorkstationTarget, connect
from .schemas import Check, HostKey

logger = logging.getLogger(__name__)

HOST_KEY_TIMEOUT_S = 10.0
COMMAND_TIMEOUT_S = 30.0
# The runner scripts shipped in the repository, put on the machine at connection time.
WS_SOURCE = Path(__file__).resolve().parents[4] / "workstation"
SCRIPTS = ["run_case.sh", "cfd_case.py", "render_video.py", "run_calculix.sh"]
SETUP_SCRIPT = "setup.sh"

TOOLS_SCRIPT = r"""
for tool in xfoil xvfb-run gmsh ccx pvbatch ffmpeg python3; do
  if command -v "$tool" > /dev/null 2>&1; then echo "$tool=1"; else echo "$tool=0"; fi
done
if ls /usr/lib/openfoam/openfoam*/etc/bashrc /opt/openfoam*/etc/bashrc > /dev/null 2>&1; then
  echo openfoam=1
else
  echo openfoam=0
fi
"""


class ConnectionProblem(Exception):
    """Why a work station can't be used, worded for the person who entered it."""


async def check_address(host: str, port: int, *, allow_private: bool) -> None:
    """Refuses addresses that aren't a machine of the user's own when private ones are off."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ConnectionProblem(f"Couldn't find a machine called {host}.") from None
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if ip.is_unspecified or ip.is_multicast:
            raise ConnectionProblem(f"{host} isn't an address of a machine.")
        if not allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local):
            raise ConnectionProblem(
                f"{host} is on the server's own network. This server only connects to machines "
                "on the public internet."
            )


def public_key_line(key: asyncssh.SSHKey) -> str:
    """`ssh-ed25519 AAAA...`, without the comment."""
    return " ".join(key.export_public_key("openssh").decode().split()[:2])


async def fetch_host_key(host: str, port: int) -> HostKey:
    try:
        key = await asyncio.wait_for(
            asyncssh.get_server_host_key(host, port, config=None), HOST_KEY_TIMEOUT_S
        )
    except (OSError, TimeoutError, asyncssh.Error) as error:
        raise ConnectionProblem(describe(error, host, port)) from error
    if key is None:
        raise ConnectionProblem(f"{host}:{port} didn't present an SSH host key.")
    return HostKey(
        key_type=key.algorithm.decode(),
        fingerprint=key.get_fingerprint("sha256"),
        host_key=public_key_line(key),
    )


def parse_host_key(line: str) -> asyncssh.SSHKey:
    try:
        return asyncssh.import_public_key(line)
    except (asyncssh.KeyImportError, ValueError):
        raise ConnectionProblem("The host key isn't valid; check the machine again.") from None


def load_private_key(pem: str, passphrase: str | None) -> asyncssh.SSHKey:
    try:
        return asyncssh.import_private_key(pem, passphrase)
    except asyncssh.KeyEncryptionError:
        raise ConnectionProblem(
            "That key needs its passphrase, or the passphrase is wrong."
        ) from None
    except (asyncssh.KeyImportError, ValueError):
        raise ConnectionProblem(
            "Couldn't read that as a private key. Paste the whole file, including the "
            "-----BEGIN and -----END lines."
        ) from None


def describe(error: BaseException, host: str, port: int, public_key: str | None = None) -> str:
    """A plain-language reason for a failed connection."""
    if isinstance(error, asyncssh.HostKeyNotVerifiable):
        return (
            f"{host}'s host key isn't the one you approved. If the machine was reinstalled, "
            "check it again."
        )
    if isinstance(error, asyncssh.PermissionDenied):
        hint = (
            f" Add this line to ~/.ssh/authorized_keys on the machine: {public_key}"
            if public_key
            else ""
        )
        return f"The machine rejected the key or the user name.{hint}"
    if isinstance(error, TimeoutError | asyncio.TimeoutError):
        return f"Timed out connecting to {host}:{port}."
    if isinstance(error, ConnectionRefusedError):
        return f"{host}:{port} refused the connection. Is SSH running there?"
    if isinstance(error, OSError):
        return f"Couldn't reach {host}:{port} ({error.strerror or type(error).__name__})."
    return f"The SSH connection to {host}:{port} failed ({type(error).__name__})."


def make_target(
    host: str, port: int, username: str, key: asyncssh.SSHKey, host_key: asyncssh.SSHKey
) -> WorkstationTarget:
    """Reaches the machine with this key and no other host key than the approved one.
    The working directory is filled in once the home directory is known."""
    entry = host if port == 22 else f"[{host}]:{port}"
    return WorkstationTarget(
        host=host,
        port=port,
        username=username,
        workdir="",
        cfd_script="",
        own_key=key,
        host_keys=asyncssh.import_known_hosts(f"{entry} {public_key_line(host_key)}\n"),
    )


async def inspect(
    target: WorkstationTarget, requested_workdir: str | None
) -> tuple[WorkstationTarget, list[Check]]:
    """Connects, puts the runner scripts on the machine and reports what's installed.

    Returns the target with its working directory set (the requested one, or `~/icarus`)."""
    assert target.own_key is not None
    public_key = public_key_line(target.own_key)
    try:
        workdir = requested_workdir or f"{await home_directory(target)}/icarus"
        ready = replace(target, workdir=workdir, cfd_script=f"{workdir}/bin/run_case.sh")
        runner = SSHRunner(ready)
        try:
            return ready, await run_checks(runner)
        finally:
            await runner.close()
    except (OSError, TimeoutError, asyncssh.Error) as error:
        raise ConnectionProblem(describe(error, target.host, target.port, public_key)) from error


async def home_directory(target: WorkstationTarget) -> str:
    """The user's home on the machine, to put the default working directory in."""
    connection = await connect(target)
    try:
        result = await connection.run('printf %s "$HOME"', check=False, timeout=COMMAND_TIMEOUT_S)
    finally:
        connection.close()
    home = str(result.stdout or "").strip()
    if not home.startswith("/"):
        raise ConnectionProblem("Couldn't find the user's home directory on the machine.")
    return home


async def install_scripts(runner: CommandRunner, workdir: str) -> None:
    """Puts the runner scripts and setup.sh on the machine (bin/ gets the runner, setup/ all)."""
    quoted = shlex.quote(workdir)
    made = await runner.run(
        f"mkdir -p {quoted}/bin {quoted}/jobs {quoted}/setup", timeout_s=COMMAND_TIMEOUT_S
    )
    if not made.ok:
        raise ConnectionProblem(
            f"Couldn't create {workdir} on the machine: {made.stderr.strip() or 'failed'}"
        )
    for name in [*SCRIPTS, SETUP_SCRIPT]:
        await runner.write_file(f"{workdir}/setup/{name}", (WS_SOURCE / name).read_text())
    installed = await runner.run(
        f"cd {quoted}/setup && install -m 755 {' '.join(SCRIPTS)} ../bin/ "
        f"&& chmod 755 {SETUP_SCRIPT}",
        timeout_s=COMMAND_TIMEOUT_S,
    )
    if not installed.ok:
        raise ConnectionProblem(
            f"Couldn't install the runner scripts: {installed.stderr.strip() or 'failed'}"
        )


async def run_checks(runner: SSHRunner) -> list[Check]:
    """Installs the runner scripts, then reports which of the tools they need are present."""
    target = runner.target
    await install_scripts(runner, target.workdir)
    result = await runner.run(f"bash -c {shlex.quote(TOOLS_SCRIPT)}", timeout_s=COMMAND_TIMEOUT_S)
    present: dict[str, bool] = {}
    for line in result.stdout.split():
        name, _, value = line.partition("=")
        present[name] = value == "1"
    setup = f"bash {target.workdir}/setup/setup.sh --install"

    def tools(*names: str) -> tuple[bool, str]:
        missing = [name for name in names if not present.get(name)]
        return (not missing, f"Missing: {', '.join(missing)}." if missing else "Installed")

    checks = [
        Check(
            id="connection",
            label="Connection",
            ok=True,
            detail=f"Connected as {target.username}@{target.host}",
        ),
        Check(
            id="runner", label="Runner scripts", ok=True, detail=f"Installed in {target.workdir}"
        ),
    ]
    for check_id, label, names in [
        ("xfoil", "XFOIL", ("xfoil", "xvfb-run")),
        ("cfd", "OpenFOAM and gmsh", ("openfoam", "gmsh", "python3")),
        ("fea", "CalculiX", ("ccx",)),
        ("video", "Flow videos (ParaView, ffmpeg)", ("pvbatch", "ffmpeg", "xvfb-run")),
    ]:
        ok, detail = tools(*names)
        checks.append(Check(id=check_id, label=label, ok=ok, detail=detail))
    if not all(check.ok for check in checks):
        checks.append(
            Check(
                id="install",
                label="Install what's missing",
                ok=False,
                detail=f"On the machine, run: {setup}",
            )
        )
    return checks
