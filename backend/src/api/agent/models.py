"""The chat models the app can offer, grouped by the provider whose API key enables them."""

from collections.abc import Mapping
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel, SecretStr

from ..config import Settings

Provider = Literal["anthropic", "openai", "openrouter", "ollama"]

# Tokens the model may think with, and the room left for its answer, when reasoning is on.
THINKING_BUDGET_TOKENS = 2048
ANSWER_MAX_TOKENS = 8192


class ModelOption(BaseModel):
    id: str
    label: str
    description: str
    provider: Provider


MODELS: list[ModelOption] = [
    # Claude models, with the user's own Anthropic key. With reasoning on they think before
    # answering (extended thinking).
    ModelOption(
        id="claude-fable-5-1",
        label="Claude Fable 5.1",
        description="Anthropic's most capable model",
        provider="anthropic",
    ),
    ModelOption(
        id="claude-opus-5-5",
        label="Claude Opus 5.5",
        description="Deep reasoning on hard problems",
        provider="anthropic",
    ),
    ModelOption(
        id="claude-sonnet-5-5",
        label="Claude Sonnet 5.5",
        description="Balances speed and intelligence",
        provider="anthropic",
    ),
    ModelOption(
        id="claude-haiku-5-5",
        label="Claude Haiku 5.5",
        description="Fastest and cheapest",
        provider="anthropic",
    ),
    # OpenAI models, with the user's own OpenAI key.
    ModelOption(
        id="gpt-5",
        label="GPT-5",
        description="OpenAI's flagship reasoning model",
        provider="openai",
    ),
    ModelOption(
        id="gpt-5-mini",
        label="GPT-5 mini",
        description="Faster and cheaper",
        provider="openai",
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
    if option.provider == "anthropic":
        thinking = reasoning_effort == "high"
        return ChatAnthropic(
            model_name=option.id,
            api_key=keys["anthropic"],
            max_tokens=ANSWER_MAX_TOKENS,
            # Extended thinking only runs at the default temperature.
            **(
                {"thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS}}
                if thinking
                else {"temperature": 0.3}
            ),
        )
    if option.provider == "openai":
        # The reasoning models only run at their default temperature.
        return ChatOpenAI(
            model=option.id,
            api_key=keys["openai"],
            reasoning_effort="high" if reasoning_effort == "high" else "minimal",
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
