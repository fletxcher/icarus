import hmac
import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, StringConstraints

from ..config import get_settings
from ..dependencies import ServicesDep
from . import google
from .repository import SESSION_LIFETIME, EmailTaken
from .schemas import Email, User, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128  # bounds the work hashing a request can cause
SESSION_COOKIE = "icarus_session"
# Holds the state and PKCE verifier of a Google sign-in between leaving and coming back.
OAUTH_COOKIE = "icarus_oauth"
OAUTH_COOKIE_PATH = "/api/auth/google"
OAUTH_LIFETIME_S = 600

logger = logging.getLogger(__name__)


async def current_user(request: Request, services: ServicesDep) -> User:
    """The signed-in user, from the session cookie; 401 if there isn't one."""
    token = request.cookies.get(SESSION_COOKIE)
    user = await services.sessions.user_for(token, services.users) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def start_session(
    user: User, request: Request, response: Response, services: ServicesDep
) -> None:
    """Signs the user in: starts a session and sets its cookie on the response."""
    token = await services.sessions.create(
        user.id,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


class SignupRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    email: Email
    password: Annotated[
        str, StringConstraints(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    ]


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, services: ServicesDep) -> UserPublic:
    """Creates an account. It doesn't sign the person in."""
    try:
        user = await services.users.create(email=body.email, password=body.password, name=body.name)
    except EmailTaken:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists"
        ) from None
    return user.public()


class LoginRequest(BaseModel):
    email: Email
    password: Annotated[str, StringConstraints(min_length=1, max_length=MAX_PASSWORD_LENGTH)]


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, response: Response, services: ServicesDep
) -> UserPublic:
    """Signs in: starts a session and sets its cookie."""
    user = await services.users.authenticate(body.email, body.password)
    if user is None:
        # The same answer whether the email or the password was wrong.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    await start_session(user, request, response, services)
    return user.public()


class Providers(BaseModel):
    google: bool


@router.get("/providers")
async def providers() -> Providers:
    """The ways to sign in besides a password that this server has set up."""
    return Providers(google=get_settings().google_enabled)


def _back_to_login(error: str) -> RedirectResponse:
    """Sends the browser to the login page with what went wrong, and drops the sign-in cookie."""
    response = RedirectResponse(
        f"{get_settings().frontend_url.rstrip('/')}/login?{urlencode({'error': error})}"
    )
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    return response


@router.get("/google/login", response_class=RedirectResponse)
async def google_login(request: Request) -> RedirectResponse:
    """Starts signing in with Google: sends the browser to Google's consent screen."""
    settings = get_settings()
    if not settings.google_enabled:
        return _back_to_login("google_unavailable")
    state, verifier = google.new_challenge()
    response = RedirectResponse(google.authorization_url(settings, state, verifier))
    response.set_cookie(
        OAUTH_COOKIE,
        f"{state}.{verifier}",
        max_age=OAUTH_LIFETIME_S,
        path=OAUTH_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/google/callback", response_class=RedirectResponse)
async def google_callback(
    request: Request,
    services: ServicesDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Where Google sends the browser back: signs the person in, then opens the app."""
    settings = get_settings()
    if not settings.google_enabled:
        return _back_to_login("google_unavailable")
    if error:  # they said no, or closed the consent screen
        return _back_to_login("google_denied")
    saved = request.cookies.get(OAUTH_COOKIE, "")
    expected_state, _, verifier = saved.partition(".")
    if not (code and state and verifier and hmac.compare_digest(state, expected_state)):
        return _back_to_login("google_failed")
    try:
        profile = await google.fetch_profile(settings, code, verifier)
    except google.GoogleError:
        logger.warning("Google sign-in failed", exc_info=True)
        return _back_to_login("google_failed")
    if not profile.email_verified:
        return _back_to_login("google_unverified")
    user = await services.users.sign_in_with_google(
        subject=profile.subject,
        email=profile.email.lower(),
        name=profile.name,
        avatar_url=profile.picture,
    )
    if user is None:
        return _back_to_login("google_disabled")
    response = RedirectResponse(settings.frontend_url.rstrip("/") + "/")
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    await start_session(user, request, response, services)
    return response


@router.get("/me")
async def me(user: CurrentUser) -> UserPublic:
    """Who is signed in."""
    return user.public()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, services: ServicesDep) -> None:
    """Signs out: ends the session and clears its cookie. Fine to call when not signed in."""
    if token := request.cookies.get(SESSION_COOKIE):
        await services.sessions.revoke(token)
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax")
