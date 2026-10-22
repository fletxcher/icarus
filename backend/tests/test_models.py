from langchain_core.messages import AIMessageChunk

from api.agent.models import available_models
from api.agent.stream import split_message
from api.config import Settings


def settings(**keys: str) -> Settings:
    return Settings(_env_file=None, **keys)


def test_models_are_offered_only_for_configured_providers():
    assert available_models(settings()) == []

    mistral_only = [m.id for m in available_models(settings(mistral_api_token="k"))]
    assert mistral_only == ["mistral-medium-3-5", "mistral-small-latest"]

    openrouter_only = [m.id for m in available_models(settings(openrouter_api_key="k"))]
    assert openrouter_only == ["openrouter/free"]

    both = available_models(settings(mistral_api_token="k", openrouter_api_key="k"))
    assert {m.provider for m in both} == {"mistral", "openrouter"}


def test_split_message_reads_mistral_thinking_blocks():
    chunk = AIMessageChunk(
        [
            {"type": "thinking", "thinking": [{"type": "text", "text": "Check Re."}]},
            {"type": "text", "text": "Use 2412."},
        ]
    )
    assert split_message(chunk) == ("Check Re.", "Use 2412.")


def test_split_message_reads_openrouter_reasoning():
    chunk = AIMessageChunk("Use 2412.", additional_kwargs={"reasoning_content": "Check Re."})
    assert split_message(chunk) == ("Check Re.", "Use 2412.")
