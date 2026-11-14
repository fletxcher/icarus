from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The repo-root .env, so settings load no matter which directory the server starts in.
# Deployments without the file (e.g. Vercel) use real environment variables instead.
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or `.env`."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_ignore_empty=True, extra="ignore")

    # Model providers: each one's models are offered only when a key for it is set, by the server
    # (below) or by the user (Anthropic and OpenAI keys come only from users, in Settings).
    openrouter_api_key: SecretStr | None = None
    # An Ollama server (e.g. http://localhost:11434) whose installed models are offered.
    ollama_base_url: str | None = None
    # Used when a message doesn't pick a model; falls back to the first available one.
    default_model: str = "openrouter/free"
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

    # How many messages each user may send in any 24 hours (a prompt and its reply is one). Admins
    # and turns that use the user's own model key aren't counted. 0 means no limit.
    daily_message_limit: int = Field(default=5, ge=0)

    # HTTP
    cors_origins: list[str] = ["http://localhost:3000"]

    # Sign in with Google (an OAuth client of type "Web application" from the Google Cloud
    # console; its authorised redirect URI is `{api_public_url}/api/auth/google/callback`).
    # The button is off until both are set.
    google_client_id: str | None = Field(
        default=None, validation_alias=AliasChoices("GOOGLE_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID")
    )
    google_client_secret: SecretStr | None = None
    # Where browsers reach this API and the frontend, for the redirects Google's sign-in makes.
    api_public_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"

    @field_validator(
        "google_client_id",
        "google_client_secret",
        "ws_host",
        "ws_ssh_key",
        "ws_host_key",
        "ws_workdir",
        "tavily_api_key",
        "brave_api_key",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def web_search_configured(self) -> bool:
        return bool(self.tavily_api_key or self.brave_api_key)

    @property
    def compute_workdir(self) -> str:
        return self.ws_workdir or f"/home/{self.ws_user}/icarus"

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    # Attachments: text files, and meshes, CAD files and PDFs (kept whole, so they must fit in
    # one MongoDB document, which holds up to 16 MB).
    file_max_bytes: int = 1_000_000
    file_max_binary_bytes: int = 15_000_000
    # Each file's text is cut to this many characters in the model's context.
    file_context_max_chars: int = 20_000

    # Logging
    log_level: str = "INFO"
    # Request/response bodies and tool payloads longer than this are truncated in logs.
    log_body_max_chars: int = 2000

    # The compute host (where XFOIL and part builds run), reached over SSH. All three of
    # ws_host, ws_ssh_key and ws_host_key are needed. The key and the host key are the text
    # itself, not file paths, so they can be set as environment variables anywhere.
    ws_host: str | None = None
    # The private key Icarus logs in with (the file's contents; "\n" can stand for line breaks).
    ws_ssh_key: SecretStr | None = None
    # The host's public key, from `ssh-keyscan -t ed25519 <host>`: the connection is refused
    # unless the host presents exactly this key.
    ws_host_key: str | None = None
    ws_port: int = 22
    ws_user: str = "icarus"
    # Where Icarus's files are on the host; ~/icarus of ws_user when not set.
    ws_workdir: str | None = None
    # Whether a user may give an Ollama address on a loopback, private-network or link-local
    # address (a machine on their own network). Turn it off for a server on the public
    # internet, where those addresses are the server's own network.
    allow_private_workstations: bool = True

    # XFOIL: run on this server ("local") or on the compute host ("ws")
    xfoil_runner: Literal["local", "ws"] = "ws"
    xfoil_path: str = "xfoil"
    # "xvfb": run under a virtual display (needs the xvfb package); required by the
    # Debian/Ubuntu xfoil package, which crashes when its graphics are turned off.
    # "none": turn XFOIL's graphics off instead, for builds without that crash.
    xfoil_display: Literal["xvfb", "none"] = "xvfb"
    xfoil_timeout_s: float = 60.0
    xfoil_max_concurrency: int = 4

    # Web search for the agent: Tavily if its key is set, else Brave. Without either the agent has
    # no web tools. Pages it opens are fetched by this server, within these limits.
    tavily_api_key: SecretStr | None = None
    brave_api_key: SecretStr | None = None
    web_max_results: int = 6
    web_timeout_s: float = 8.0
    web_fetch_max_bytes: int = 2_000_000
    # PDFs (papers, reports) are bigger than web pages, and can't be read when cut off.
    web_fetch_max_pdf_bytes: int = 15_000_000
    web_fetch_max_chars: int = 12_000

    # Heavy jobs on the compute host (the Python the agent writes, and part builds) run this many
    # at a time, since the host is small; one that can't get a turn within the wait gives up.
    host_max_concurrency: int = 1
    host_queue_wait_s: float = 30.0

    # Python the agent writes, run in a sandbox on the compute host: a script's time limit, and
    # how much of its output the agent gets to see.
    python_timeout_s: float = 60.0
    python_output_max_chars: int = 8000

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
