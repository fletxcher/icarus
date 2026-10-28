from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The repo-root .env, so settings load no matter which directory the server starts in.
# Deployments without the file (e.g. Vercel) use real environment variables instead.
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or `.env`."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_ignore_empty=True, extra="ignore")

    # Model providers: each one's models are offered only when its key is set.
    mistral_api_token: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    # An Ollama server (e.g. http://localhost:11434) whose installed models are offered.
    ollama_base_url: str | None = None
    # Used when a message doesn't pick a model; falls back to the first available one.
    default_model: str = "mistral-medium-3-5"
    # Name new chats from their first message (one extra model request per chat).
    auto_title: bool = True
    # "high" streams the model's thinking before its answer; "none" skips it.
    reasoning_effort: Literal["high", "none"] = "high"

    # MongoDB: `mongodb_target` picks which of the two connections below is used.
    mongodb_target: Literal["local", "remote"] = "local"
    mongodb_local_uri: str = "mongodb://localhost:27017"
    mongodb_local_name: str = "icarus"
    # The remote one (e.g. MongoDB Atlas); the URI carries the credentials.
    mongodb_remote_uri: SecretStr | None = None
    mongodb_remote_name: str = "icarus"

    # Fernet key(s) that encrypt users' API keys and SSH keys in the database. Without it
    # those can't be stored; make one with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    # Comma-separated to rotate: the first seals new values, all of them open old ones.
    credentials_key: SecretStr | None = None

    # HTTP
    cors_origins: list[str] = ["http://localhost:3000"]

    # Attachments (text files only for now)
    file_max_bytes: int = 1_000_000
    # Each file's text is cut to this many characters in the model's context.
    file_context_max_chars: int = 20_000

    # Logging
    log_level: str = "INFO"
    # Request/response bodies and tool payloads longer than this are truncated in logs.
    log_body_max_chars: int = 2000

    # Simulation work station, reached over SSH
    ws_host: str | None = None
    ws_port: int = 22
    ws_user: str = "ubuntu"
    ws_ssh_key_path: str | None = None
    ws_known_hosts_path: str | None = None
    ws_workdir: str = "/home/ubuntu/icarus"
    ws_cfd_script: str = "/home/ubuntu/icarus/bin/run_openfoam.sh"
    # Whether a user may connect their own work station at a loopback, private-network or
    # link-local address (their own computer, a VM on their network). Turn it off for a server
    # on the public internet, where those addresses are the server's own network.
    allow_private_workstations: bool = True

    # XFOIL: run on this server ("local") or on the simulation work station ("ws")
    xfoil_runner: Literal["local", "ws"] = "ws"
    xfoil_path: str = "xfoil"
    # "xvfb": run under a virtual display (needs the xvfb package); required by the
    # Debian/Ubuntu xfoil package, which crashes when its graphics are turned off.
    # "none": turn XFOIL's graphics off instead, for builds without that crash.
    xfoil_display: Literal["xvfb", "none"] = "xvfb"
    xfoil_timeout_s: float = 60.0
    xfoil_max_concurrency: int = 4

    @model_validator(mode="after")
    def _remote_mongodb_needs_uri(self) -> "Settings":
        if self.mongodb_target == "remote" and self.mongodb_remote_uri is None:
            raise ValueError("MONGODB_TARGET=remote requires MONGODB_REMOTE_URI")
        return self

    @property
    def mongodb_uri(self) -> str:
        if self.mongodb_target == "remote":
            assert self.mongodb_remote_uri is not None  # checked by the validator
            return self.mongodb_remote_uri.get_secret_value()
        return self.mongodb_local_uri

    @property
    def mongodb_db(self) -> str:
        if self.mongodb_target == "remote":
            return self.mongodb_remote_name
        return self.mongodb_local_name


@lru_cache
def get_settings() -> Settings:
    return Settings()
