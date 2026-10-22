from pydantic import BaseModel


class ModelOption(BaseModel):
    id: str
    label: str
    description: str


# Mistral models with tool calling and adjustable reasoning (`reasoning_effort`). Mistral's
# reasoning docs name these exact ids; they don't say whether the `-latest` aliases reason.
# Mistral Large isn't offered: it isn't available to this project's API key.
MODELS: list[ModelOption] = [
    ModelOption(
        id="mistral-medium-3-5",
        label="Mistral Medium 3.5",
        description="Frontier-class, optimized for agentic use",
    ),
    ModelOption(
        id="mistral-small-latest",
        label="Mistral Small",
        description="Fast and efficient",
    ),
]

MODEL_IDS = {model.id for model in MODELS}
