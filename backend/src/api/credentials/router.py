"""`/api/credentials`: the signed-in user's own model API keys and work station.

Secrets only go in. They're stored encrypted (see `vault.py`) and no endpoint returns them;
the user sees the last characters of a key, and a work station's host, user and fingerprint.
"""

import logging
from urllib.parse import urlsplit

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import SecretStr

from ..agent import ollama
from ..agent.models import Provider
from ..auth.router import CurrentUser
from ..compute.pool import target_for
from ..compute.runner import SSHRunner
from ..config import get_settings
from ..dependencies import ServicesDep
from . import cleanup, providers, provision, workstation
from .schemas import (
    Hardware,
    HostKey,
    HostKeyRequest,
    ModelKeyIn,
    ModelKeyInfo,
    OllamaIn,
    Overview,
    SetupIn,
    SetupPlan,
    SetupProgress,
    WorkstationDetails,
    WorkstationIn,
    WorkstationInfo,
    WorkstationReport,
)
from .workstation import ConnectionProblem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/credentials", tags=["credentials"])


async def storage_ready(services: ServicesDep) -> None:
    if not services.credentials.vault.available:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "This server can't store credentials: CREDENTIALS_KEY isn't set.",
        )


Storage = Depends(storage_ready)


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


@router.get("")
async def overview(services: ServicesDep, user: CurrentUser) -> Overview:
    return Overview(
        model_keys=await services.credentials.list_model_keys(user.id),
        workstations=await services.credentials.list_workstations(user.id),
        default_providers=services.server_providers,
        default_workstation=services.compute.shared_available,
        storage_available=services.credentials.vault.available,
    )


@router.put("/model-keys/{provider}", dependencies=[Storage])
async def save_model_key(
    provider: Provider, body: ModelKeyIn, services: ServicesDep, user: CurrentUser
) -> ModelKeyInfo:
    """Saves the user's own API key for a provider, after the provider accepts it."""
    if provider == "ollama":
        raise _unprocessable("Ollama has no API key; give its address instead.")
    key = body.api_key.get_secret_value().strip()
    if len(key) < 8:
        raise _unprocessable("That doesn't look like an API key.")
    try:
        await providers.check_key(provider, SecretStr(key))
    except providers.KeyRejected:
        name = providers.PROVIDER_NAMES[provider]
        raise _unprocessable(f"{name} rejected that API key.") from None
    await services.credentials.set_model_key(user.id, provider, key)
    infos = await services.credentials.list_model_keys(user.id)
    return next(info for info in infos if info.provider == provider)


@router.put("/ollama", dependencies=[Storage])
async def save_ollama(body: OllamaIn, services: ServicesDep, user: CurrentUser) -> ModelKeyInfo:
    """Saves the address of the user's Ollama server, after listing its models works."""
    url = urlsplit(body.base_url.strip())
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise _unprocessable(
            "Use an address like http://localhost:11434 (no user name or password)."
        )
    base_url = f"{url.scheme}://{url.netloc}"
    try:
        await workstation.check_address(
            url.hostname,
            url.port or (443 if url.scheme == "https" else 80),
            allow_private=get_settings().allow_private_workstations,
        )
        await ollama.installed_models(base_url)
    except ConnectionProblem as problem:
        raise _unprocessable(str(problem)) from None
    except ollama.OllamaUnavailable:
        raise _unprocessable(f"{base_url} didn't answer like an Ollama server.") from None
    ollama.forget(base_url)
    await services.credentials.set_ollama(user.id, base_url)
    infos = await services.credentials.list_model_keys(user.id)
    return next(info for info in infos if info.provider == "ollama")


@router.delete("/model-keys/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_model_key(provider: Provider, services: ServicesDep, user: CurrentUser) -> None:
    if not await services.credentials.delete_model_key(user.id, provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No key saved for that provider")


@router.post("/workstation/host-key")
async def look_up_host_key(body: HostKeyRequest, user: CurrentUser) -> HostKey:
    """Fetches the machine's SSH host key so the user can confirm it's theirs."""
    _ = user  # signed in only; the lookup itself touches nothing of the user's
    try:
        await workstation.check_address(
            body.host, body.port, allow_private=get_settings().allow_private_workstations
        )
        return await workstation.fetch_host_key(body.host, body.port)
    except ConnectionProblem as problem:
        raise _unprocessable(str(problem)) from None


@router.put("/workstation", dependencies=[Storage])
async def save_workstation(
    body: WorkstationIn, services: ServicesDep, user: CurrentUser
) -> WorkstationReport:
    """Connects to the machine with the given key and the approved host key, installs the
    runner scripts there and, if that works, saves the machine."""
    if not 1 <= body.port <= 65535:
        raise _unprocessable("The port must be between 1 and 65535.")
    if body.id and await services.credentials.workstation_info(user.id, body.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such machine")
    try:
        await workstation.check_address(
            body.host, body.port, allow_private=get_settings().allow_private_workstations
        )
        approved = workstation.parse_host_key(body.host_key)
        key = workstation.load_private_key(
            body.private_key.get_secret_value(),
            body.passphrase.get_secret_value() if body.passphrase else None,
        )
        target, checks, machine = await workstation.inspect(
            workstation.make_target(body.host, body.port, body.username, key, approved),
            body.workdir.rstrip("/") if body.workdir else None,
        )
    except ConnectionProblem as problem:
        raise _unprocessable(str(problem)) from None

    details = WorkstationDetails(
        name=body.name or body.host,
        hardware=hardware_of(machine),
        host=target.host,
        port=target.port,
        username=target.username,
        workdir=target.workdir,
        host_key=workstation.public_key_line(approved),
        fingerprint=approved.get_fingerprint("sha256"),
        public_key=workstation.public_key_line(key),
    )
    # Saved without a passphrase: the encrypted copy in the database is what protects it.
    saved = await services.credentials.add_workstation(
        user.id, details, key.export_private_key("openssh").decode(), machine_id=body.id
    )
    await services.compute.forget(user.id)
    logger.info("user %s saved their work station %s@%s", user.id, body.username, body.host)
    return WorkstationReport(workstation=saved, checks=checks, machine=machine)


@router.post("/workstation/check")
async def check_workstation(services: ServicesDep, user: CurrentUser) -> WorkstationReport:
    """Re-checks the connected machine (and refreshes the runner scripts on it)."""
    stored = await services.credentials.workstation(user.id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No machine is connected")
    try:
        _, checks, machine = await workstation.inspect(target_for(stored), stored.details.workdir)
    except ConnectionProblem as problem:
        raise _unprocessable(str(problem)) from None
    await services.credentials.set_hardware(user.id, stored.id, hardware_of(machine))
    info = await services.credentials.workstation_info(user.id, stored.id)
    assert info is not None
    return WorkstationReport(workstation=info, checks=checks, machine=machine)


def hardware_of(machine: workstation.MachineInfo) -> Hardware:
    """What a machine's card shows of it."""
    return Hardware(cpus=machine.cpus, cpu_model=machine.cpu_model, gpu=machine.gpu)


async def _own_station(services: ServicesDep, user_id: str) -> SSHRunner:
    """The runner of the user's saved machine, or 404 if they haven't added one."""
    station = await services.compute.workstation(user_id)
    if station.source != "own" or not isinstance(station.runner, SSHRunner):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You haven't added a work station")
    return station.runner


def _unreachable(problem: Exception, runner: SSHRunner) -> HTTPException:
    target = runner.target
    if isinstance(problem, ConnectionProblem):
        return _unprocessable(str(problem))
    return _unprocessable(workstation.describe(problem, target.host, target.port))


@router.get("/workstation/setup/plan")
async def setup_plan(services: ServicesDep, user: CurrentUser) -> SetupPlan:
    """What the setup button would install on the saved machine, for the confirmation dialog."""
    runner = await _own_station(services, user.id)
    try:
        return provision.plan_for(await workstation.probe_machine(runner))
    except (OSError, TimeoutError, asyncssh.Error, ConnectionProblem) as problem:
        raise _unreachable(problem, runner) from None


@router.post("/workstation/setup", status_code=status.HTTP_202_ACCEPTED)
async def start_setup(body: SetupIn, services: ServicesDep, user: CurrentUser) -> SetupProgress:
    """Starts installing Docker and the solver image on the saved machine, in the background.

    The sudo password, when needed, is used for this one request and never stored or logged."""
    runner = await _own_station(services, user.id)
    password = body.password.get_secret_value() if body.password else None
    try:
        await provision.start(
            runner, await workstation.probe_machine(runner), runner.target.username, password
        )
        return await provision.progress(runner)
    except (OSError, TimeoutError, asyncssh.Error, ConnectionProblem) as problem:
        raise _unreachable(problem, runner) from None


@router.get("/workstation/setup")
async def setup_progress(services: ServicesDep, user: CurrentUser) -> SetupProgress:
    """How setup is going on the saved machine."""
    runner = await _own_station(services, user.id)
    try:
        return await provision.progress(runner)
    except (OSError, TimeoutError, asyncssh.Error) as problem:
        raise _unreachable(problem, runner) from None


async def _saved(services: ServicesDep, user_id: str, machine_id: str) -> WorkstationInfo:
    info = await services.credentials.workstation_info(user_id, machine_id)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such machine")
    return info


@router.post("/workstations/{machine_id}/connect")
async def connect_workstation(
    machine_id: str, services: ServicesDep, user: CurrentUser
) -> WorkstationInfo:
    """Makes this machine the one jobs run on, disconnecting any other."""
    await _saved(services, user.id, machine_id)
    if not await services.credentials.connect_workstation(user.id, machine_id):
        raise _unprocessable("That machine is being removed.")
    await services.compute.forget(user.id)
    return await _saved(services, user.id, machine_id)


@router.post("/workstations/{machine_id}/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_workstation(machine_id: str, services: ServicesDep, user: CurrentUser) -> None:
    """Disconnects the machine; it stays saved. Jobs already running stay on it."""
    await _saved(services, user.id, machine_id)
    await services.credentials.disconnect_workstations(user.id)
    await services.compute.forget(user.id)


@router.delete(
    "/workstations/{machine_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkstationInfo | None,
)
async def remove_workstation(
    machine_id: str, services: ServicesDep, user: CurrentUser, force: bool = False
) -> WorkstationInfo | Response:
    """Removes a machine. Its files and SSH key are cleaned off the machine in the background
    first, and only then does it leave the database. `force` skips the cleanup and forgets the
    machine at once, for one that can't be reached."""
    info = await _saved(services, user.id, machine_id)
    if force:
        await services.credentials.delete_workstation(user.id, machine_id)
        await services.compute.machine_removed(user.id, machine_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if info.status != "removing":
        await cleanup.start_removal(services, user.id, machine_id)
    return await _saved(services, user.id, machine_id)
