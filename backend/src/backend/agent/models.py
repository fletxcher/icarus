from pydantic import BaseModel


class ModelOption(BaseModel):
    id: str
    label: str
    description: str


# Mistral aliases that support tool calling. The `-latest` aliases track new releases.
MODELS: list[ModelOption] = [
    ModelOption(
        id="mistral-medium-latest",
        label="Mistral Medium",
        description="Frontier-class, optimized for agentic use",
    ),
    ModelOption(
        id="mistral-large-latest",
        label="Mistral Large",
        description="Largest open-weight multimodal model",
    ),
]

MODEL_IDS = {model.id for model in MODELS}
