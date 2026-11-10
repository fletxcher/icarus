from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from ..catalog import SESSIONS, USERS
from .passwords import UNMATCHED_HASH, hash_password, verify_password
from .schemas import Email, Identity, Session, User
from .tokens import hash_token, new_token

SESSION_LIFETIME = timedelta(days=30)


class EmailTaken(Exception):
    """An account with this email address already exists."""


class UserRepository:
    """Persistence for user accounts."""

    def __init__(self, db: AsyncDatabase) -> None:
        self._users = db[USERS]

    async def create(self, *, email: Email, password: str, name: str | None = None) -> User:
        """Registers a user with a password. Raises EmailTaken if the address is in use."""
        now = datetime.now(UTC)
        user = User(
            id=str(uuid4()),
            email=email,
            name=name,
            password_hash=await hash_password(password),
            created_at=now,
            updated_at=now,
        )
        try:
            await self._users.insert_one(user.to_mongo())
        except DuplicateKeyError as error:
            raise EmailTaken(email) from error
        return user

    async def get(self, user_id: str) -> User | None:
        doc = await self._users.find_one({"_id": user_id})
        return User.model_validate(doc) if doc else None

    async def get_by_email(self, email: Email) -> User | None:
        doc = await self._users.find_one({"email": email})
        return User.model_validate(doc) if doc else None

    async def authenticate(self, email: Email, password: str) -> User | None:
        """The active user with this email and password, or None. Records the login."""
        user = await self.get_by_email(email)
        # An unknown email, an OAuth-only account and a wrong password all cost one hash check.
        hash_to_check = user.password_hash if user and user.password_hash else UNMATCHED_HASH
        matches = await verify_password(password, hash_to_check)
        if user is None or not user.password_hash or not matches or user.status != "active":
            return None
        now = datetime.now(UTC)
        await self._users.update_one({"_id": user.id}, {"$set": {"last_login_at": now}})
        return user.model_copy(update={"last_login_at": now})

    async def sign_in_with_google(
        self, *, subject: str, email: Email, name: str | None, avatar_url: str | None
    ) -> User | None:
        """The active user for a verified Google account, or None if theirs is disabled.

        A returning Google user is found by Google's id for them. Otherwise an account with the
        same email gets the Google sign-in added to it (Google has verified the address), and an
        unknown email gets a new account with no password."""
        now = datetime.now(UTC)
        identity = {"provider": "google", "subject": subject}
        for _ in range(2):  # twice, in case a simultaneous first sign-in creates the account
            doc = await self._users.find_one({"identities": {"$elemMatch": identity}})
            if doc is None:
                doc = await self._users.find_one({"email": email})
                if doc is not None:
                    fields = {"last_login_at": now, "updated_at": now}
                    if doc.get("email_verified_at") is None:
                        fields["email_verified_at"] = now
                    if not doc.get("avatar_url") and avatar_url:
                        fields["avatar_url"] = avatar_url
                    await self._users.update_one(
                        {"_id": doc["_id"]},
                        {
                            "$set": fields,
                            "$push": {
                                "identities": Identity(**identity, linked_at=now).model_dump()
                            },
                        },
                    )
                    doc = await self._users.find_one({"_id": doc["_id"]})
            else:
                await self._users.update_one({"_id": doc["_id"]}, {"$set": {"last_login_at": now}})
                doc["last_login_at"] = now
            if doc is not None:
                user = User.model_validate(doc)
                return user if user.status == "active" else None
            user = User(
                id=str(uuid4()),
                email=email,
                email_verified_at=now,
                name=name[:100] if name else None,
                avatar_url=avatar_url[:2048] if avatar_url else None,
                identities=[Identity(**identity, linked_at=now)],
                created_at=now,
                updated_at=now,
                last_login_at=now,
            )
            try:
                await self._users.insert_one(user.to_mongo())
            except DuplicateKeyError:
                continue
            return user
        return None


class SessionRepository:
    """Persistence for signed-in browsers."""

    def __init__(self, db: AsyncDatabase) -> None:
        self._sessions = db[SESSIONS]

    async def create(self, user_id: str, *, user_agent: str | None, ip: str | None) -> str:
        """Starts a session and returns its token for the cookie; only a hash is stored."""
        token = new_token()
        now = datetime.now(UTC)
        session = Session(
            id=str(uuid4()),
            user_id=user_id,
            token_hash=hash_token(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + SESSION_LIFETIME,
            user_agent=user_agent[:512] if user_agent else None,
            ip=ip,
        )
        await self._sessions.insert_one(session.to_mongo())
        return token

    async def user_for(self, token: str, users: "UserRepository") -> User | None:
        """The active user a session cookie belongs to, or None if it's unknown, expired or
        signed out."""
        session = await self._sessions.find_one(
            {
                "token_hash": hash_token(token),
                "revoked_at": None,
                "expires_at": {"$gt": datetime.now(UTC)},
            }
        )
        if session is None:
            return None
        user = await users.get(session["user_id"])
        return user if user and user.status == "active" else None

    async def revoke(self, token: str) -> None:
        """Signs the session out. It stays in the collection until it expires."""
        await self._sessions.update_one(
            {"token_hash": hash_token(token), "revoked_at": None},
            {"$set": {"revoked_at": datetime.now(UTC)}},
        )
