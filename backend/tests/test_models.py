from langchain_core.messages import AIMessageChunk
from pydantic import SecretStr

from api.agent.models import available_models, create_chat_model, server_keys
from api.agent.stream import split_message
from api.config import Settings


def test_models_are_offered_only_for_providers_with_a_key():
    assert available_models({}) == []

    mistral_only = [m.id for m in available_models({"mistral": SecretStr("k")})]
    assert mistral_only == ["mistral-medium-3-5", "mistral-small-latest"]

    openrouter_only = [m.id for m in available_models({"openrouter": SecretStr("k")})]
    assert openrouter_only == ["openrouter/free"]

    both = available_models({"mistral": SecretStr("k"), "openrouter": SecretStr("k")})
    assert {m.provider for m in both} == {"mistral", "openrouter"}


def test_the_server_supplies_the_keys_set_in_its_environment():
    settings = Settings(_env_file=None, mistral_api_token="m")
    assert set(server_keys(settings)) == {"mistral"}


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


def test_every_provider_builds_its_models_from_its_key():
    keys = {provider: SecretStr("k") for provider in ("mistral", "openrouter")}
    for option in available_models(keys):
        assert create_chat_model(option, keys, reasoning_effort="none") is not None


def test_an_ollama_model_is_built_from_the_servers_address():
    from api.agent.models import ModelOption

    option = ModelOption(
        id="ollama/llama3.1:8b", label="llama3.1:8b", description="", provider="ollama"
    )
    model = create_chat_model(
        option, {"ollama": SecretStr("http://localhost:11434")}, reasoning_effort="none"
    )

    assert model.model == "llama3.1:8b"
    assert model.base_url == "http://localhost:11434"
