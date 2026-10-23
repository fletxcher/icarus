from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from pydantic import ValidationError
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, WriteError

from api.auth.schemas import Identity, Preferences, Session, User
from api.auth.tokens import hash_token, new_token
from api.db import SESSIONS, USERS, ensure_schema
from tests.conftest import MONGODB_URI

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def make_user(**changes: Any) -> User:
    values: dict[str, Any] = {
        "id": "user-1",
        "email": "Ada@Example.com",
        "created_at": NOW,
        "updated_at": NOW,
    } | changes
    return User(**values)


def make_session(**changes: Any) -> Session:
    values: dict[str, Any] = {
        "id": "session-1",
        "user_id": "user-1",
        "token_hash": hash_token(new_token()),
        "created_at": NOW,
        "last_seen_at": NOW,
        "expires_at": NOW + timedelta(days=30),
    } | changes
    return Session(**values)


@pytest_asyncio.fixture
async def db(mongo_db_name: str) -> AsyncIterator[AsyncDatabase]:
    client = AsyncMongoClient(MONGODB_URI, tz_aware=True)
    database = client[mongo_db_name]
    await ensure_schema(database)
    yield database
    await client.close()


# --- Pydantic models -----------------------------------------------------------------


def test_email_is_stored_lowercase_and_trimmed():
    assert make_user(email="  Ada@Example.COM ").email == "ada@example.com"
    with pytest.raises(ValidationError):
        make_user(email="not-an-email")


def test_user_defaults_are_a_regular_active_user_with_default_preferences():
    user = make_user()
    assert (user.role, user.status, user.identities) == ("user", "active", [])
    assert user.preferences == Preferences(default_model=None, theme="system")
    assert user.password_hash is None


def test_public_user_never_exposes_credentials():
    user = make_user(
        password_hash="$argon2id$secret",
        identities=[Identity(provider="github", subject="42", linked_at=NOW)],
    )
    public = user.public().model_dump()

    assert public["email"] == "ada@example.com"
    assert "password_hash" not in public and "identities" not in public


def test_session_token_hash_must_look_like_a_sha256_digest():
    with pytest.raises(ValidationError):
        make_session(token_hash="plain-token")


def test_tokens_are_random_and_only_their_hash_is_stored():
    first, second = new_token(), new_token()
    assert first != second and len(first) >= 43
    assert hash_token(first) == hash_token(first) != hash_token(second)
    assert len(hash_token(first)) == 64


def test_to_mongo_uses_underscore_id():
    document = make_user().to_mongo()
    assert document["_id"] == "user-1" and "id" not in document


# --- MongoDB validators and indexes (need a database) ----------------------------------


async def test_documents_from_the_models_pass_the_database_validators(db: AsyncDatabase):
    user = make_user(
        name="Ada",
        password_hash="$argon2id$v=19$m=65536$abc",
        identities=[Identity(provider="google", subject="g-1", linked_at=NOW)],
        email_verified_at=NOW,
        last_login_at=NOW,
        preferences=Preferences(default_model="mistral-medium-3-5", theme="dark"),
    )
    session = make_session(revoked_at=NOW, user_agent="Mozilla/5.0", ip="203.0.113.7")

    await db[USERS].insert_one(user.to_mongo())
    await db[SESSIONS].insert_one(session.to_mongo())

    # Documents read back into the models, including tz-aware dates.
    assert User.model_validate(await db[USERS].find_one({"_id": "user-1"})) == user
    assert Session.model_validate(await db[SESSIONS].find_one({"_id": "session-1"})) == session


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "superuser"},
        {"status": "deleted"},
        {"email": "Ada@Example.com"},  # uppercase would dodge the unique index
        {"email": "no-at-sign"},
        {"unexpected_field": 1},
        {"preferences": {"theme": "neon"}},
        {"identities": [{"provider": "myspace", "subject": "1", "linked_at": NOW}]},
        {"created_at": "2026-10-22"},  # a string, not a date
    ],
)
async def test_database_rejects_malformed_users(db: AsyncDatabase, changes: dict[str, Any]):
    document = make_user().to_mongo() | {"email": "ada@example.com"} | changes
    with pytest.raises(WriteError):
        await db[USERS].insert_one(document)


async def test_database_rejects_a_user_missing_required_fields(db: AsyncDatabase):
    document = make_user().to_mongo()
    del document["role"]
    with pytest.raises(WriteError):
        await db[USERS].insert_one(document)


@pytest.mark.parametrize(
    "changes",
    [
        {"token_hash": "not-a-digest"},
        {"expires_at": "tomorrow"},
        {"user_id": 7},
        {"unexpected_field": 1},
    ],
)
async def test_database_rejects_malformed_sessions(db: AsyncDatabase, changes: dict[str, Any]):
    with pytest.raises(WriteError):
        await db[SESSIONS].insert_one(make_session().to_mongo() | changes)


async def test_email_and_oauth_identity_are_unique_per_user(db: AsyncDatabase):
    github = Identity(provider="github", subject="42", linked_at=NOW)
    await db[USERS].insert_one(make_user(email="a@example.com", identities=[github]).to_mongo())

    with pytest.raises(DuplicateKeyError):  # same email
        await db[USERS].insert_one(make_user(id="user-2", email="a@example.com").to_mongo())
    with pytest.raises(DuplicateKeyError):  # same GitHub account on another user
        await db[USERS].insert_one(
            make_user(id="user-3", email="c@example.com", identities=[github]).to_mongo()
        )


async def test_users_without_identities_do_not_collide_with_each_other(db: AsyncDatabase):
    for number in range(3):
        user = make_user(id=f"user-{number}", email=f"u{number}@example.com")
        await db[USERS].insert_one(user.to_mongo())
    assert await db[USERS].count_documents({}) == 3


async def test_session_tokens_are_unique_and_expire_through_a_ttl_index(db: AsyncDatabase):
    session = make_session()
    await db[SESSIONS].insert_one(session.to_mongo())
    with pytest.raises(DuplicateKeyError):
        await db[SESSIONS].insert_one(
            make_session(id="session-2", user_id="user-2").to_mongo()
            | {"token_hash": session.token_hash}
        )

    indexes = {index["name"]: index async for index in await db[SESSIONS].list_indexes()}
    assert indexes["expires_at_ttl"]["expireAfterSeconds"] == 0
    assert indexes["expires_at_ttl"]["key"] == {"expires_at": 1}
    assert indexes["user_sessions"]["key"] == {"user_id": 1, "created_at": -1}


async def test_ensure_schema_is_repeatable_and_updates_an_existing_collection(
    db: AsyncDatabase,
):
    await ensure_schema(db)  # second run: nothing to create, nothing breaks

    # A collection created earlier without a validator gets one on the next startup.
    await db[USERS].drop()
    await db.create_collection(USERS)
    await db[USERS].insert_one({"_id": "loose", "anything": "goes"})
    await db[USERS].delete_many({})
    await ensure_schema(db)

    with pytest.raises(WriteError):
        await db[USERS].insert_one({"_id": "still-loose", "anything": "goes"})
