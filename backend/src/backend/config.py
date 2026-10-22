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

    # Mistral
    mistral_api_token: SecretStr | None = None
    default_model: str = "mistral-medium-latest"

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "icarus"

    # HTTP
    cors_origins: list[str] = ["http://localhost:3000"]

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
    xfoil_timeout_s: float = 60.0
    xfoil_max_concurrency: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
