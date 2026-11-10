"""Documents for the `users` and `sessions` collections.

These models are the app-side source of truth. `definitions.py` mirrors them as MongoDB
`$jsonSchema` validators, so the database also rejects malformed documents; keep the two
in step (tests insert models' documents into a real validated collection).
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, EmailStr, Field

from ..mongo import MongoModel

Role = Literal["user", "admin"]
UserStatus = Literal["active", "disabled"]
# OAuth providers a user can sign in with; add one here and in definitions.py to support it.
IdentityProvider = Literal["github", "google"]
Theme = Literal["light", "dark", "system"]


def _clean_email(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


# Stored lowercase, so uniqueness doesn't depend on how someone capitalizes their address.
Email = Annotated[
    EmailStr, BeforeValidator(_clean_email), AfterValidator(lambda email: email.lower())
]


class Identity(BaseModel):
    """A sign-in through an OAuth provider, linked to the user."""

    provider: IdentityProvider
    # The provider's stable id for the person (not their email, which can change).
    subject: str = Field(min_length=1, max_length=255)
    linked_at: datetime


class Preferences(BaseModel):
    default_model: str | None = None
    theme: Theme = "system"


class User(MongoModel):
    email: Email
    email_verified_at: datetime | None = None
    name: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=2048)
    # Argon2id hash. None for users who only sign in through an OAuth provider.
    password_hash: str | None = Field(default=None, max_length=512)
    identities: list[Identity] = Field(default_factory=list, max_length=10)
    role: Role = "user"
    status: UserStatus = "active"
    preferences: Preferences = Field(default_factory=Preferences)
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None

    def public(self) -> "UserPublic":
        return UserPublic.model_validate(self.model_dump())


class UserPublic(BaseModel):
    """What the API returns about a user: never the password hash or linked identities."""

    id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None
    role: Role
    preferences: Preferences
    created_at: datetime


class Session(MongoModel):
    """A signed-in browser. The cookie holds a random token; only its hash is stored."""

    user_id: str
    # SHA-256 (hex) of the cookie token, so a database leak doesn't leak live sessions.
    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    last_seen_at: datetime
    # Absolute expiry; MongoDB deletes the document shortly after (TTL index).
    expires_at: datetime
    # Set when the user signs out or is signed out; the document stays until expiry.
    revoked_at: datetime | None = None
    user_agent: str | None = Field(default=None, max_length=512)
    ip: str | None = Field(default=None, max_length=45)
