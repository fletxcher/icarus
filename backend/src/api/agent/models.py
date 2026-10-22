"""The chat models the app can offer, grouped by the provider whose API key enables them."""

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_mistralai import ChatMistralAI
from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel

from api.config import Settings

Provider = Literal["mistral", "openrouter"]


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


def configured_providers(settings: Settings) -> set[Provider]:
    """Providers whose API key is set."""
    providers: set[Provider] = set()
    if settings.mistral_api_token:
        providers.add("mistral")
    if settings.openrouter_api_key:
        providers.add("openrouter")
    return providers


def available_models(settings: Settings) -> list[ModelOption]:
    """The models usable with the API keys present, in display order."""
    providers = configured_providers(settings)
    return [option for option in MODELS if option.provider in providers]


def create_chat_model(option: ModelOption, settings: Settings) -> BaseChatModel:
    if option.provider == "mistral":
        return ChatMistralAI(
            model_name=option.id,
            api_key=settings.mistral_api_token,
            temperature=0.3,
            model_kwargs={"reasoning_effort": settings.reasoning_effort},
        )
    return ChatOpenRouter(
        model=option.id,
        api_key=settings.openrouter_api_key,
        temperature=0.3,
        reasoning={"effort": settings.reasoning_effort},
    )
