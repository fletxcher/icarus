"""What the credentials API sends and accepts. Secrets only ever go in; none come back."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, SecretStr, StringConstraints

from ..agent.models import Provider

HOST_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.:-]*$"
USERNAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$"
# Absolute, plain path: it's put in shell commands (quoted) and in file paths on the machine.
WORKDIR_PATTERN = r"^/[A-Za-z0-9_./-]*$"
MAX_PRIVATE_KEY_CHARS = 16_384

Host = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=253, pattern=HOST_PATTERN)
]


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


class HostKeyRequest(BaseModel):
    host: Host
    port: int = 22


class HostKey(BaseModel):
    """A work station's SSH host key, shown so the person can confirm it's the right machine."""

    key_type: str
    fingerprint: str
    # The key in OpenSSH's one-line form, sent back with the connection to pin it.
    host_key: str


class WorkstationIn(BaseModel):
    host: Host
    port: int = 22
    username: Annotated[str, StringConstraints(strip_whitespace=True, pattern=USERNAME_PATTERN)]
    private_key: SecretStr = Field(min_length=32, max_length=MAX_PRIVATE_KEY_CHARS)
    passphrase: SecretStr | None = Field(default=None, max_length=1024)
    host_key: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)]
    # Where Icarus keeps its files on the machine; its home directory's `icarus` by default.
    workdir: Annotated[str, StringConstraints(max_length=256, pattern=WORKDIR_PATTERN)] | None = (
        None
    )


class WorkstationDetails(BaseModel):
    host: str
    port: int
    username: str
    workdir: str
    host_key: str
    fingerprint: str
    # The public half of the saved key, to put in the machine's ~/.ssh/authorized_keys.
    public_key: str


class WorkstationInfo(WorkstationDetails):
    updated_at: datetime


class Check(BaseModel):
    id: str
    label: str
    ok: bool
    detail: str


class WorkstationReport(BaseModel):
    workstation: WorkstationInfo
    checks: list[Check]


class Overview(BaseModel):
    model_keys: list[ModelKeyInfo]
    workstation: WorkstationInfo | None
    # Providers the server supplies a key for, used when the person hasn't added their own.
    default_providers: list[Provider]
    # Whether the server has a shared work station that is used until they add one.
    default_workstation: bool
    # False when the server has no CREDENTIALS_KEY, so nothing can be saved.
    storage_available: bool
