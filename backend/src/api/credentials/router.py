"""`/api/credentials`: the signed-in user's own model API keys.

Secrets only go in. They're stored encrypted (see `vault.py`) and no endpoint returns them;
the user sees the last characters of a key.
"""

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import SecretStr

from ..agent import ollama
from ..agent.models import Provider
from ..auth.router import CurrentUser
from ..config import get_settings
from ..dependencies import ServicesDep
from . import providers
from .addresses import AddressProblem, check_address
from .schemas import ModelKeyIn, ModelKeyInfo, OllamaIn, Overview

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
        default_providers=services.server_providers,
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
        await check_address(
            url.hostname,
            url.port or (443 if url.scheme == "https" else 80),
            allow_private=get_settings().allow_private_workstations,
        )
        await ollama.installed_models(base_url)
    except AddressProblem as problem:
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
