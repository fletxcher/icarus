"""Sign in with Google: the authorization-code flow with PKCE, against Google's OAuth endpoints."""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from ..config import Settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = "openid email profile"
TIMEOUT_S = 10


class GoogleError(Exception):
    """Google refused the sign-in, or couldn't be reached."""


@dataclass(frozen=True)
class GoogleProfile:
    subject: str
    email: str
    email_verified: bool
    name: str | None
    picture: str | None


def redirect_uri(settings: Settings) -> str:
    return f"{settings.api_public_url.rstrip('/')}/api/auth/google/callback"


def new_challenge() -> tuple[str, str]:
    """(state, PKCE verifier): both kept in a cookie until Google sends the browser back."""
    return secrets.token_urlsafe(24), secrets.token_urlsafe(48)


def authorization_url(settings: Settings, state: str, verifier: str) -> str:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri(settings),
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return f"{AUTH_URL}?{query}"


async def fetch_profile(settings: Settings, code: str, verifier: str) -> GoogleProfile:
    """Trades the code for the person's Google profile. Raises GoogleError on any failure."""
    assert settings.google_client_secret is not None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            token = await client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret.get_secret_value(),
                    "redirect_uri": redirect_uri(settings),
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
            )
            if token.status_code != 200:
                raise GoogleError(f"Google refused the code ({token.status_code})")
            access_token = token.json().get("access_token")
            if not access_token:
                raise GoogleError("Google sent no access token")
            info = await client.get(
                USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            if info.status_code != 200:
                raise GoogleError(f"Google refused the profile request ({info.status_code})")
            body = info.json()
    except httpx.HTTPError as error:
        raise GoogleError(f"couldn't reach Google: {error}") from error
    if not body.get("sub") or not body.get("email"):
        raise GoogleError("Google's profile has no id or email")
    return GoogleProfile(
        subject=str(body["sub"]),
        email=str(body["email"]),
        email_verified=body.get("email_verified") is True,
        name=body.get("name"),
        picture=body.get("picture"),
    )
