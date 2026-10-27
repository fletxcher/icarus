import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from api.auth.passwords import verify_password
from api.auth.tokens import hash_token
from api.catalog import USERS
from tests.conftest import MONGODB_URI


@pytest.fixture
def client(anon_client: TestClient) -> TestClient:
    """These tests start from nobody being signed in."""
    return anon_client


ADA = {"name": "Ada Lovelace", "email": "ada@example.com", "password": "correct horse"}


def signup(client: TestClient, **changes):
    return client.post("/api/auth/signup", json=ADA | changes)


def stored_users(client: TestClient) -> list[dict]:
    source = MongoClient(MONGODB_URI)
    try:
        return list(source[client.app.state.db.name][USERS].find())
    finally:
        source.close()


def test_signup_creates_a_user_and_returns_only_public_fields(client: TestClient):
    response = signup(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"
    assert body["name"] == "Ada Lovelace"
    assert body["role"] == "user"
    assert set(body) == {"id", "email", "name", "avatar_url", "role", "preferences", "created_at"}
    [stored] = stored_users(client)
    assert stored["_id"] == body["id"]


def test_the_password_is_stored_only_as_an_argon2id_hash(client: TestClient):
    signup(client)

    [stored] = stored_users(client)
    assert stored["password_hash"].startswith("$argon2id$")
    assert "correct horse" not in str(stored)
    assert client.portal.call(verify_password, "correct horse", stored["password_hash"])
    assert not client.portal.call(verify_password, "wrong horse", stored["password_hash"])


def test_the_email_is_trimmed_and_lowercased_and_cant_be_registered_twice(client: TestClient):
    assert signup(client, email="  Ada@Example.com ").status_code == 201

    again = signup(client, email="ADA@example.com")

    assert again.status_code == 409
    assert "already exists" in again.json()["detail"]
    assert [user["email"] for user in stored_users(client)] == ["ada@example.com"]


def test_signup_rejects_bad_input(client: TestClient):
    assert signup(client, email="not-an-email").status_code == 422
    assert signup(client, password="short").status_code == 422
    assert signup(client, password="x" * 129).status_code == 422
    assert signup(client, name="   ").status_code == 422
    assert stored_users(client) == []


def login(client: TestClient, **changes):
    return client.post(
        "/api/auth/login", json={"email": ADA["email"], "password": ADA["password"]} | changes
    )


def test_login_starts_a_session_and_sets_an_http_only_cookie(client: TestClient):
    signup(client)

    response = login(client, email="Ada@Example.com")

    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("icarus_session=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = response.cookies["icarus_session"]
    source = MongoClient(MONGODB_URI)
    try:
        database = source[client.app.state.db.name]
        [session] = list(database["sessions"].find())
        [user] = list(database[USERS].find())
    finally:
        source.close()
    assert session["token_hash"] == hash_token(token) and token not in str(session)
    assert session["user_id"] == user["_id"]
    assert user["last_login_at"] is not None


def test_login_fails_the_same_way_for_a_wrong_password_and_an_unknown_email(client: TestClient):
    signup(client)

    wrong = login(client, password="wrong horse")
    unknown = login(client, email="nobody@example.com")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert "set-cookie" not in wrong.headers
    assert stored_users(client)[0]["last_login_at"] is None


def test_a_disabled_account_cannot_log_in(client: TestClient):
    signup(client)
    source = MongoClient(MONGODB_URI)
    try:
        source[client.app.state.db.name][USERS].update_one({}, {"$set": {"status": "disabled"}})
    finally:
        source.close()

    assert login(client).status_code == 401


def test_me_returns_the_signed_in_user_and_401_otherwise(client: TestClient):
    assert client.get("/api/auth/me").status_code == 401
    signup(client)
    login(client)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["name"] == "Ada Lovelace"
    assert "password_hash" not in response.json()
    client.cookies.clear()
    client.cookies.set("icarus_session", "made-up")
    assert client.get("/api/auth/me").status_code == 401


def test_logout_ends_the_session_and_clears_the_cookie(client: TestClient):
    signup(client)
    login(client)
    token = client.cookies["icarus_session"]

    response = client.post("/api/auth/logout")

    assert response.status_code == 204
    assert "icarus_session=" in response.headers["set-cookie"]
    client.cookies.clear()
    client.cookies.set("icarus_session", token)  # even a copy of the old cookie is dead now
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 204  # and signing out again is fine


def test_language_is_saved_in_the_users_preferences(client: TestClient):
    signup(client)
    login(client)

    response = client.patch("/api/auth/me/preferences", json={"language": "es"})

    assert response.status_code == 200
    assert response.json()["preferences"]["language"] == "es"
    assert client.get("/api/auth/me").json()["preferences"]["language"] == "es"
    assert stored_users(client)[0]["preferences"]["language"] == "es"
    assert client.patch("/api/auth/me/preferences", json={"language": None}).status_code == 200
    assert client.patch("/api/auth/me/preferences", json={"language": "xx"}).status_code == 422


def test_preferences_need_a_signed_in_user(client: TestClient):
    assert client.patch("/api/auth/me/preferences", json={"language": "es"}).status_code == 401
