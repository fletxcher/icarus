from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
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
    # Used when a message doesn't pick a model; falls back to the first available one.
    default_model: str = "mistral-medium-3-5"
    # "high" streams the model's thinking before its answer; "none" skips it.
    reasoning_effort: Literal["high", "none"] = "high"

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "icarus"

    # HTTP
    cors_origins: list[str] = ["http://localhost:3000"]

    # Logging
    log_level: str = "INFO"
    # Request/response bodies and tool payloads longer than this are truncated in logs.
    log_body_max_chars: int = 2000

    # Simulation VM (OCI), reached over SSH
    vm_host: str | None = None
    vm_port: int = 22
    vm_user: str = "ubuntu"
    vm_ssh_key_path: str | None = None
    vm_known_hosts_path: str | None = None
    vm_workdir: str = "/home/ubuntu/icarus"
    vm_cfd_script: str = "/home/ubuntu/icarus/bin/run_case.sh"

    # XFOIL: run on this server ("local") or on the simulation VM ("vm")
    xfoil_runner: Literal["local", "vm"] = "vm"
    xfoil_path: str = "xfoil"
    # "xvfb": run under a virtual display (needs the xvfb package); required by the
    # Debian/Ubuntu xfoil package, which crashes when its graphics are turned off.
    # "none": turn XFOIL's graphics off instead, for builds without that crash.
    xfoil_display: Literal["xvfb", "none"] = "xvfb"
    xfoil_timeout_s: float = 60.0
    xfoil_max_concurrency: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
