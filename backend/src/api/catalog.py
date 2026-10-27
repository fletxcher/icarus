"""Every MongoDB collection the app uses: its schema, indexes and whether it's enforced.

This is the single source for `db.ensure_schema` (applied at startup) and for the JSON
files in `database/` (see `schema_export.py`).
"""

from dataclasses import dataclass
from typing import Any

from pymongo import IndexModel

from .auth.definitions import (
    SESSIONS_INDEXES,
    SESSIONS_VALIDATOR,
    USERS_INDEXES,
    USERS_VALIDATOR,
)
from .chats.definitions import (
    MESSAGES_INDEXES,
    MESSAGES_VALIDATOR,
    THREADS_INDEXES,
    THREADS_VALIDATOR,
)
from .compute.definitions import SIMULATIONS_INDEXES, SIMULATIONS_VALIDATOR
from .credentials.definitions import CREDENTIALS_INDEXES, CREDENTIALS_VALIDATOR
from .files.definitions import FILES_INDEXES, FILES_VALIDATOR

THREADS = "threads"
MESSAGES = "messages"
SIMULATIONS = "simulations"
FILES = "files"
USERS = "users"
SESSIONS = "sessions"
CREDENTIALS = "credentials"


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    description: str
    validator: dict[str, Any]
    indexes: list[IndexModel]
    # Whether MongoDB itself rejects documents that don't match `validator`. The others
    # document the shape the app writes and are checked by tests, not by the database,
    # because they hold data from earlier versions of the app.
    enforced: bool


COLLECTIONS = [
    CollectionSpec(
        THREADS,
        "Chat sessions. One document per conversation; its messages are in `messages`.",
        THREADS_VALIDATOR,
        THREADS_INDEXES,
        enforced=False,
    ),
    CollectionSpec(
        MESSAGES,
        "Messages in a chat: the user's, and the assistant's replies with their reasoning, "
        "tool calls and text in order.",
        MESSAGES_VALIDATOR,
        MESSAGES_INDEXES,
        enforced=False,
    ),
    CollectionSpec(
        FILES,
        "Text files uploaded to be sent with a message.",
        FILES_VALIDATOR,
        FILES_INDEXES,
        enforced=False,
    ),
    CollectionSpec(
        SIMULATIONS,
        "CFD jobs submitted to the simulation work station, with their status and results.",
        SIMULATIONS_VALIDATOR,
        SIMULATIONS_INDEXES,
        enforced=False,
    ),
    CollectionSpec(
        USERS,
        "Accounts: email, credentials (password hash and/or OAuth identities), role and "
        "preferences.",
        USERS_VALIDATOR,
        USERS_INDEXES,
        enforced=True,
    ),
    CollectionSpec(
        SESSIONS,
        "Signed-in browsers. Stores a hash of the cookie token; expired sessions are "
        "deleted automatically.",
        SESSIONS_VALIDATOR,
        SESSIONS_INDEXES,
        enforced=True,
    ),
    CollectionSpec(
        CREDENTIALS,
        "Each user's own model API keys and work station. The secrets are encrypted with "
        "CREDENTIALS_KEY; `details` holds what can be shown without decrypting.",
        CREDENTIALS_VALIDATOR,
        CREDENTIALS_INDEXES,
        enforced=True,
    ),
]
