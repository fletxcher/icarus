"""What the credentials API sends and accepts. Secrets only ever go in; none come back."""

from datetime import datetime

from pydantic import BaseModel, Field, SecretStr

from ..agent.models import Provider


class ModelKeyIn(BaseModel):
    api_key: SecretStr = Field(min_length=8, max_length=512)


class ModelKeyInfo(BaseModel):
    provider: Provider
    # The last characters, so a person can tell which key is saved.
    last4: str
    # Ollama only: the server's address (it's not secret, and there's no key to end in).
    url: str | None = None
    updated_at: datetime


class OllamaIn(BaseModel):
    base_url: str = Field(min_length=8, max_length=512)


class Overview(BaseModel):
    model_keys: list[ModelKeyInfo]
    # Providers the server supplies a key for, used when the person hasn't added their own.
    default_providers: list[Provider]
    # False when the server has no CREDENTIALS_KEY, so nothing can be saved.
    storage_available: bool
