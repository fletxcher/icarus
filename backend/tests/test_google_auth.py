"""Sign in with Google: the redirect out, the callback back, and the accounts it makes or links."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from api.auth import google
from api.auth.google import GoogleError, GoogleProfile
from api.config import get_settings
from tests.conftest import TEST_USER

PROFILE = GoogleProfile(
    subject="g-123",
    email="Pilot@Example.com",
    email_verified=True,
    name="Pilot",
    picture="http://p",
)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "client-id")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("client-secret"))
    monkeypatch.setattr(settings, "frontend_url", "http://app.test")
    monkeypatch.setattr(settings, "api_public_url", "http://api.test")
    return settings


def google_returns(monkeypatch: pytest.MonkeyPatch, profile: GoogleProfile | Exception) -> list:
    calls: list = []

    async def fake(settings, code, verifier):
        calls.append((code, verifier))
        if isinstance(profile, Exception):
            raise profile
        return profile

    monkeypatch.setattr(google, "fetch_profile", fake)
    return calls


def start(client: TestClient) -> str:
    """Begins a sign-in and returns its state."""
    response = client.get("/api/auth/google/login", follow_redirects=False)
    assert response.status_code == 307
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def test_the_button_is_off_until_google_is_set_up(anon_client: TestClient, configured, monkeypatch):
    assert anon_client.get("/api/auth/providers").json() == {"google": True}
    monkeypatch.setattr(configured, "google_client_id", None)
    assert anon_client.get("/api/auth/providers").json() == {"google": False}

    response = anon_client.get("/api/auth/google/login", follow_redirects=False)

    assert response.headers["location"] == "http://app.test/login?error=google_unavailable"


def test_login_sends_the_browser_to_google_with_state_and_pkce(anon_client: TestClient, configured):
    response = anon_client.get("/api/auth/google/login", follow_redirects=False)

    url = urlparse(response.headers["location"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://api.test/api/auth/google/callback"]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"][0]
    cookie = response.headers["set-cookie"]
    assert (
        query["state"][0] in cookie and "HttpOnly" in cookie and "Path=/api/auth/google" in cookie
    )


def test_a_new_google_user_gets_an_account_and_a_session(
    anon_client: TestClient, configured, monkeypatch
):
    calls = google_returns(monkeypatch, PROFILE)
    state = start(anon_client)

    response = anon_client.get(
        "/api/auth/google/callback", params={"code": "abc", "state": state}, follow_redirects=False
    )

    assert response.status_code == 307 and response.headers["location"] == "http://app.test/"
    assert calls and calls[0][0] == "abc" and calls[0][1]  # the code, and the PKCE verifier
    me = anon_client.get("/api/auth/me").json()
    assert me["email"] == "pilot@example.com" and me["name"] == "Pilot"
    assert me["avatar_url"] == "http://p"


def test_the_same_google_user_comes_back_to_the_same_account(
    anon_client: TestClient, configured, monkeypatch
):
    google_returns(monkeypatch, PROFILE)
    ids = []
    for _ in range(2):
        state = start(anon_client)
        anon_client.get("/api/auth/google/callback", params={"code": "c", "state": state})
        ids.append(anon_client.get("/api/auth/me").json()["id"])
        anon_client.post("/api/auth/logout")

    assert ids[0] == ids[1]
    # Even if their Google email changed, Google's id for them still finds the account.
    google_returns(
        monkeypatch, PROFILE.__class__(**{**PROFILE.__dict__, "email": "new@example.com"})
    )
    state = start(anon_client)
    anon_client.get("/api/auth/google/callback", params={"code": "c", "state": state})
    assert anon_client.get("/api/auth/me").json()["id"] == ids[0]


def test_google_is_added_to_an_account_with_the_same_email(
    client: TestClient, configured, monkeypatch
):
    account = client.get("/api/auth/me").json()  # the fixture already signed up and in
    client.post("/api/auth/logout")
    profile = GoogleProfile("g-9", TEST_USER["email"].upper(), True, "Someone", None)
    google_returns(monkeypatch, profile)
    state = start(client)

    client.get("/api/auth/google/callback", params={"code": "c", "state": state})

    assert client.get("/api/auth/me").json()["id"] == account["id"]
    client.post("/api/auth/logout")
    # The password still works too.
    assert client.post("/api/auth/login", json=TEST_USER).status_code == 200


@pytest.mark.parametrize(
    ("params", "profile", "error"),
    [
        ({"error": "access_denied"}, PROFILE, "google_denied"),
        ({"code": "c", "state": "forged"}, PROFILE, "google_failed"),
        ({"code": "c"}, PROFILE, "google_failed"),
        ({"code": "c", "state": "GOOD"}, GoogleError("nope"), "google_failed"),
        (
            {"code": "c", "state": "GOOD"},
            GoogleProfile("g", "a@b.co", False, None, None),
            "google_unverified",
        ),
    ],
)
def test_failed_sign_ins_go_back_to_the_login_page(
    anon_client: TestClient, configured, monkeypatch, params, profile, error
):
    google_returns(monkeypatch, profile)
    state = start(anon_client)
    params = {k: (state if v == "GOOD" else v) for k, v in params.items()}

    response = anon_client.get("/api/auth/google/callback", params=params, follow_redirects=False)

    assert response.headers["location"] == f"http://app.test/login?error={error}"
    assert anon_client.get("/api/auth/me").status_code == 401


def test_a_callback_without_a_sign_in_in_progress_is_refused(
    anon_client: TestClient, configured, monkeypatch
):
    google_returns(monkeypatch, PROFILE)

    response = anon_client.get(
        "/api/auth/google/callback", params={"code": "c", "state": "x"}, follow_redirects=False
    )

    assert response.headers["location"].endswith("error=google_failed")
