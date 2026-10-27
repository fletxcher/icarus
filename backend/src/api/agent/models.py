"""The chat models the app can offer, grouped by the provider whose API key enables them."""

from collections.abc import Mapping
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_mistralai import ChatMistralAI
from langchain_ollama import ChatOllama
from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel, SecretStr

from ..config import Settings

Provider = Literal["mistral", "openrouter", "ollama"]


class ModelOption(BaseModel):
    id: str
    label: str
    description: str
    provider: Provider


MODELS: list[ModelOption] = [
    # Mistral models with tool calling and adjustable reasoning (`reasoning_effort`).
    # Mistral's reasoning docs name these exact ids. Mistral Large isn't offered: the
    # project's subscription tier doesn't include it.
    ModelOption(
        id="mistral-medium-3-5",
        label="Mistral Medium 3.5",
        description="Frontier-class, optimized for agentic use",
        provider="mistral",
    ),
    ModelOption(
        id="mistral-small-latest",
        label="Mistral Small",
        description="Fast and efficient",
        provider="mistral",
    ),
    # Routes each request to a free model. Free models allow 50 requests a day
    # (1,000 after buying $10 of credits), and an agent turn makes several requests.
    ModelOption(
        id="openrouter/free",
        label="OpenRouter Free",
        description="A free model picked by OpenRouter; limited daily requests",
        provider="openrouter",
    ),
]


# The API key to use for each provider that has one.
ProviderKeys = Mapping[Provider, SecretStr]


def server_keys(settings: Settings) -> dict[Provider, SecretStr]:
    """The keys the server itself supplies (from the environment), used until a user adds
    their own."""
    keys: dict[Provider, SecretStr] = {}
    if settings.mistral_api_token:
        keys["mistral"] = settings.mistral_api_token
    if settings.openrouter_api_key:
        keys["openrouter"] = settings.openrouter_api_key
    if settings.ollama_base_url:
        keys["ollama"] = SecretStr(settings.ollama_base_url)
    return keys


def available_models(keys: ProviderKeys) -> list[ModelOption]:
    """The built-in models usable with these API keys, in display order (Ollama's models
    depend on the server; see `ollama.model_options`)."""
    return [option for option in MODELS if option.provider in keys]


def create_chat_model(
    option: ModelOption, keys: ProviderKeys, *, reasoning_effort: Literal["high", "none"]
) -> BaseChatModel:
    if option.provider == "mistral":
        return ChatMistralAI(
            model_name=option.id,
            api_key=keys["mistral"],
            temperature=0.3,
            model_kwargs={"reasoning_effort": reasoning_effort},
        )
    if option.provider == "openrouter":
        return ChatOpenRouter(
            model=option.id,
            api_key=keys["openrouter"],
            temperature=0.3,
            reasoning={"effort": reasoning_effort},
        )
    if option.provider == "ollama":
        # For Ollama the "key" is the server's address; the model's name follows the prefix.
        return ChatOllama(
            model=option.id.removeprefix("ollama/"),
            base_url=keys["ollama"].get_secret_value(),
            temperature=0.3,
        )
