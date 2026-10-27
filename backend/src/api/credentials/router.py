"""`/api/credentials`: the signed-in user's own model API keys and work station.

Secrets only go in. They're stored encrypted (see `vault.py`) and no endpoint returns them;
the user sees the last characters of a key, and a work station's host, user and fingerprint.
"""

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import SecretStr

from ..agent import ollama
from ..agent.models import Provider
from ..auth.router import CurrentUser
from ..compute.pool import target_for
from ..config import get_settings
from ..dependencies import ServicesDep
from . import providers, workstation
from .schemas import (
    HostKey,
    HostKeyRequest,
    ModelKeyIn,
    ModelKeyInfo,
    OllamaIn,
    Overview,
    WorkstationDetails,
    WorkstationIn,
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
        workstation=await services.credentials.workstation_info(user.id),
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
    try:
        await workstation.check_address(
            body.host, body.port, allow_private=get_settings().allow_private_workstations
        )
        approved = workstation.parse_host_key(body.host_key)
        key = workstation.load_private_key(
            body.private_key.get_secret_value(),
            body.passphrase.get_secret_value() if body.passphrase else None,
        )
        target, checks = await workstation.inspect(
            workstation.make_target(body.host, body.port, body.username, key, approved),
            body.workdir.rstrip("/") if body.workdir else None,
        )
    except ConnectionProblem as problem:
        raise _unprocessable(str(problem)) from None

    details = WorkstationDetails(
        host=target.host,
        port=target.port,
        username=target.username,
        workdir=target.workdir,
        host_key=workstation.public_key_line(approved),
        fingerprint=approved.get_fingerprint("sha256"),
        public_key=workstation.public_key_line(key),
    )
    # Saved without a passphrase: the encrypted copy in the database is what protects it.
    saved = await services.credentials.set_workstation(
        user.id, details, key.export_private_key("openssh").decode()
    )
    await services.compute.forget(user.id)
    logger.info("user %s saved their work station %s@%s", user.id, body.username, body.host)
    return WorkstationReport(workstation=saved, checks=checks)


@router.post("/workstation/check")
async def check_workstation(services: ServicesDep, user: CurrentUser) -> WorkstationReport:
    """Re-checks the saved machine (and refreshes the runner scripts on it)."""
    stored = await services.credentials.workstation(user.id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You haven't added a work station")
    details = stored.details
    target = target_for(stored)
    try:
        _, checks = await workstation.inspect(target, details.workdir)
    except ConnectionProblem as problem:
        raise _unprocessable(str(problem)) from None
    info = await services.credentials.workstation_info(user.id)
    assert info is not None
    return WorkstationReport(workstation=info, checks=checks)


@router.delete("/workstation", status_code=status.HTTP_204_NO_CONTENT)
async def remove_workstation(services: ServicesDep, user: CurrentUser) -> None:
    """Forgets the user's machine; their simulations go back to the shared work station."""
    if not await services.credentials.delete_workstation(user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You haven't added a work station")
    await services.compute.forget(user.id)
