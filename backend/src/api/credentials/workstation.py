"""Connecting to a user's own work station: host keys, checks and the runner scripts."""

import asyncio
import hashlib
import ipaddress
import logging
import shlex
import socket
from dataclasses import replace
from pathlib import Path

import asyncssh

from ..compute.runner import CommandRunner, SSHRunner, WorkstationTarget, connect, via_exec
from .schemas import Check, HostKey, MachineInfo

logger = logging.getLogger(__name__)

HOST_KEY_TIMEOUT_S = 10.0
COMMAND_TIMEOUT_S = 30.0
# The runner scripts shipped in the repository, put on the machine at connection time.
WS_SOURCE = Path(__file__).resolve().parents[4] / "workstation"
SCRIPTS = [
    "icarus-exec",
    "run_cfd2d.sh",
    "cfd2d_case.py",
    "render_video.py",
    "run_calculix.sh",
    "run_fea.sh",
    "fea_case.py",
    "run_cfd3d.sh",
    "cfd3d_case.py",
    "cad_build.py",
]
SETUP_SCRIPT = "setup.sh"
# Copied next to setup.sh only: setup.sh builds the solver image from it.
DOCKERFILE = "Dockerfile"
IMAGE_REPO = "icarus-solvers"


def image_tag() -> str:
    """The solver image the shipped Dockerfile builds: named after its content, like setup.sh."""
    digest = hashlib.sha256((WS_SOURCE / DOCKERFILE).read_bytes()).hexdigest()
    return f"{IMAGE_REPO}:{digest[:12]}"


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


PROBE_SCRIPT = r"""
echo "hostname=$(hostname 2>/dev/null)"
echo "kernel=$(uname -s 2>/dev/null)"
echo "arch=$(uname -m 2>/dev/null)"
echo "cpus=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null)"
echo "cpu_model=$(lscpu 2>/dev/null | sed -n 's/^Model name:[[:space:]]*//p' | head -n 1)"
echo "mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null)"
echo "disk_free_kb=$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  echo "os_id=${ID:-}"
  echo "os_name=${PRETTY_NAME:-}"
fi
echo "gpu=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1)"
if command -v docker > /dev/null 2>&1; then
  if docker info > /dev/null 2>&1; then echo docker=usable; else echo docker=installed; fi
else
  echo docker=missing
fi
if [ "$(id -u)" = 0 ]; then
  echo privilege=root
elif ! command -v sudo > /dev/null 2>&1; then
  echo privilege=none
elif sudo -n true > /dev/null 2>&1; then
  echo privilege=passwordless_sudo
else
  echo privilege=password_sudo
fi
"""

# What the solver image builds on: the OpenFOAM, CalculiX and Python packages inside it come in
# x86-64 and ARM64 builds.
SUPPORTED_ARCHES = ("x86_64", "amd64", "aarch64", "arm64")
MIN_MEMORY_GB = 4.0
MIN_DISK_GB = 15.0


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


CONNECT_TIMEOUT_S = 15.0


# Adds one public key to ~/.ssh/authorized_keys without disturbing the others, and makes sure
# sshd will accept the files (it ignores keys when the permissions are too open).
def install_key_command(public_key: str) -> str:
    blob = public_key.split()[1]
    file = '"$HOME/.ssh/authorized_keys"'
    return (
        'umask 077; mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" '
        f"&& touch {file} && chmod 600 {file} "
        # The last line may lack its newline; the new key must not be glued onto it.
        f'&& {{ [ ! -s {file} ] || [ -z "$(tail -c1 {file})" ] || echo >> {file}; }} '
        f"&& {{ grep -qF -- {shlex.quote(blob)} {file} "
        f"|| printf '%s\\n' {shlex.quote(public_key)} >> {file}; }}"
    )


async def install_public_key(
    host: str,
    port: int,
    username: str,
    password: str,
    host_key: asyncssh.SSHKey,
    public_key: str,
) -> None:
    """Logs in with the person's password, on the machine they approved, and adds Icarus's key
    to it. The password is used for this one login and not kept."""
    entry = host if port == 22 else f"[{host}]:{port}"
    try:
        connection = await asyncssh.connect(
            host,
            port=port,
            username=username,
            password=password,
            known_hosts=asyncssh.import_known_hosts(f"{entry} {public_key_line(host_key)}\n"),
            client_keys=None,
            config=None,
            agent_path=None,
            agent_forwarding=False,
            preferred_auth="password,keyboard-interactive",
            connect_timeout=CONNECT_TIMEOUT_S,
            login_timeout=CONNECT_TIMEOUT_S,
        )
    except asyncssh.PermissionDenied:
        raise ConnectionProblem(
            "The machine rejected that password or user name. If it only allows key logins, "
            "use your own key under Advanced."
        ) from None
    except (OSError, TimeoutError, asyncssh.Error) as error:
        raise ConnectionProblem(describe(error, host, port)) from error
    try:
        result = await connection.run(
            install_key_command(public_key), check=False, timeout=COMMAND_TIMEOUT_S
        )
    except (OSError, TimeoutError, asyncssh.Error) as error:
        raise ConnectionProblem(describe(error, host, port)) from error
    finally:
        connection.close()
    if result.exit_status != 0:
        detail = str(result.stderr or "").strip() or "failed"
        raise ConnectionProblem(f"Couldn't add Icarus's key on the machine: {detail}")


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
) -> tuple[WorkstationTarget, list[Check], MachineInfo]:
    """Connects, puts the runner scripts on the machine and reports what's installed.

    Returns the target with its working directory set (the requested one, or `~/icarus`)."""
    assert target.own_key is not None
    public_key = public_key_line(target.own_key)
    try:
        workdir = requested_workdir or f"{await home_directory(target)}/icarus"
        ready = replace(target, workdir=workdir, cfd_script=f"{workdir}/bin/run_cfd2d.sh")
        runner = SSHRunner(ready)
        try:
            checks, machine = await run_checks(runner)
            return ready, checks, machine
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
    await runner.write_bytes(f"{workdir}/setup/{DOCKERFILE}", (WS_SOURCE / DOCKERFILE).read_bytes())
    installed = await runner.run(
        f"cd {quoted}/setup && install -m 755 {' '.join(SCRIPTS)} ../bin/ "
        f"&& chmod 755 {SETUP_SCRIPT}",
        timeout_s=COMMAND_TIMEOUT_S,
    )
    if not installed.ok:
        raise ConnectionProblem(
            f"Couldn't install the runner scripts: {installed.stderr.strip() or 'failed'}"
        )


def parse_runtime(output: str) -> dict[str, str]:
    """`icarus-exec --status`: runtime (docker or native), image and image_present."""
    return {
        name.strip(): value.strip()
        for name, _, value in (line.partition("=") for line in output.splitlines())
    }


def runtime_check(runtime: dict[str, str]) -> Check:
    """Whether the solvers run in the current solver image, an old one, or directly."""
    if runtime.get("runtime") != "docker":
        return Check(
            id="runtime",
            label="Where the solvers run",
            ok=True,
            detail="Directly on the machine (no Docker)",
        )
    expected, image = image_tag(), runtime.get("image", "")
    if runtime.get("image_present") != "1":
        detail = f"The solver image {image} isn't on the machine. Set it up again."
    elif image != expected:
        detail = f"The solver image {image} is out of date (expected {expected}). Set it up again."
    else:
        return Check(
            id="runtime",
            label="Where the solvers run",
            ok=True,
            detail=f"In Docker, image {image}",
        )
    return Check(id="runtime", label="Where the solvers run", ok=False, detail=detail)


def parse_machine(output: str) -> MachineInfo:
    """Turns the probe's `key=value` lines into what's known about the machine, and whether
    Icarus can run here."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip()

    def number(name: str, scale: float = 1.0) -> float | None:
        try:
            return round(float(values[name]) / scale, 1)
        except (KeyError, ValueError):
            return None

    cpus = number("cpus")
    kernel, arch = values.get("kernel", ""), values.get("arch", "")
    memory_gb, disk_free_gb = number("mem_kb", 1024 * 1024), number("disk_free_kb", 1024 * 1024)
    problems: list[str] = []
    if kernel and kernel != "Linux":
        problems.append(f"{kernel} isn't supported; the machine must run Linux.")
    elif arch and arch not in SUPPORTED_ARCHES:
        problems.append(f"{arch} processors aren't supported; the machine must be x86-64 or ARM64.")
    if memory_gb is not None and memory_gb < MIN_MEMORY_GB:
        problems.append(
            f"Only {memory_gb:g} GB of memory; at least {MIN_MEMORY_GB:g} GB is needed."
        )
    if disk_free_gb is not None and disk_free_gb < MIN_DISK_GB:
        problems.append(
            f"Only {disk_free_gb:g} GB of free disk space; at least {MIN_DISK_GB:g} GB is needed."
        )
    docker = values.get("docker", "missing")
    privilege = values.get("privilege", "none")
    return MachineInfo(
        hostname=values.get("hostname") or "unknown",
        os=values.get("os_name") or kernel or "unknown",
        arch=arch or "unknown",
        cpus=int(cpus) if cpus else None,
        cpu_model=values.get("cpu_model") or None,
        memory_gb=memory_gb,
        disk_free_gb=disk_free_gb,
        gpu=values.get("gpu") or None,
        docker=docker if docker in ("usable", "installed", "missing") else "missing",
        privilege=(
            privilege
            if privilege in ("root", "passwordless_sudo", "password_sudo", "none")
            else "none"
        ),
        supported=not problems,
        problems=problems,
    )


def describe_machine(machine: MachineInfo) -> str:
    parts = [machine.os, machine.arch]
    if machine.cpus:
        parts.append(f"{machine.cpus} cores")
    if machine.memory_gb:
        parts.append(f"{machine.memory_gb:g} GB memory")
    if machine.gpu:
        parts.append(machine.gpu)
    return ", ".join(parts)


async def probe_machine(runner: CommandRunner) -> MachineInfo:
    result = await runner.run(f"bash -c {shlex.quote(PROBE_SCRIPT)}", timeout_s=COMMAND_TIMEOUT_S)
    return parse_machine(result.stdout)


async def run_checks(runner: SSHRunner) -> tuple[list[Check], MachineInfo]:
    """Reads what the machine is, installs the runner scripts, then reports which of the tools
    they need are present."""
    target = runner.target
    machine = await probe_machine(runner)
    await install_scripts(runner, target.workdir)
    # The tools are looked for where jobs will run them: in the solver image on a Docker machine.
    inside = via_exec(target.workdir)
    result = await runner.run(
        f"{inside}bash -c {shlex.quote(TOOLS_SCRIPT)}", timeout_s=COMMAND_TIMEOUT_S
    )
    # The Python environment the FEA mesher and CAD builder run in.
    mesher = await runner.run(
        f"{inside}python3 -c 'import numpy, pyvista, pytetwild, scipy, build123d'",
        timeout_s=COMMAND_TIMEOUT_S,
    )
    runtime = parse_runtime(
        (
            await runner.run(
                f"{shlex.quote(f'{target.workdir}/bin/icarus-exec')} --status",
                timeout_s=COMMAND_TIMEOUT_S,
            )
        ).stdout
    )
    present: dict[str, bool] = {"fea_mesher": mesher.ok}
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
            id="machine",
            label="Machine",
            ok=machine.supported,
            detail=" ".join(machine.problems) or describe_machine(machine),
        ),
        Check(
            id="runner", label="Runner scripts", ok=True, detail=f"Installed in {target.workdir}"
        ),
        runtime_check(runtime),
    ]
    for check_id, label, names in [
        ("xfoil", "XFOIL", ("xfoil", "xvfb-run")),
        ("cfd", "OpenFOAM and gmsh", ("openfoam", "gmsh", "python3")),
        (
            "fea",
            "CalculiX, and the Python mesher and CAD builder for FEA, 3D CFD and design",
            ("ccx", "fea_mesher"),
        ),
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
    return checks, machine
