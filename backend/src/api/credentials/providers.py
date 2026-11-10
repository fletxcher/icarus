"""Checks a model API key with its provider before it's saved."""

import logging

import httpx
from pydantic import SecretStr

from ..agent.models import Provider

logger = logging.getLogger(__name__)

# A request that needs a valid key but costs nothing: listing models, or the key's own info.
CHECK_URLS: dict[Provider, str] = {
    "anthropic": "https://api.anthropic.com/v1/models",
    "openai": "https://api.openai.com/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/auth/key",
    "ollama": "",  # no key: the user gives a server address instead (see the router)
}
PROVIDER_NAMES: dict[Provider, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "ollama": "Ollama",
}
TIMEOUT_S = 10.0
REJECTED = (401, 403)


class KeyRejected(Exception):
    """The provider says this API key isn't valid."""


def _headers(provider: Provider, key: SecretStr) -> dict[str, str]:
    if provider == "anthropic":  # Anthropic takes the key in its own header
        return {"x-api-key": key.get_secret_value(), "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key.get_secret_value()}"}


async def check_key(provider: Provider, key: SecretStr) -> None:
    """Raises KeyRejected if the provider refuses the key. If the provider can't be reached
    or answers something else, the key is given the benefit of the doubt."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.get(
                CHECK_URLS[provider],
                headers=_headers(provider, key),
            )
    except httpx.HTTPError as error:
        logger.warning("couldn't check a %s key: %s", provider, type(error).__name__)
        return
    if response.status_code in REJECTED:
        raise KeyRejected(provider)
    if not response.is_success:
        logger.warning("%s key check answered %d", provider, response.status_code)
