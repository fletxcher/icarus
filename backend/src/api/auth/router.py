from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, StringConstraints

from ..dependencies import ServicesDep
from ..languages import Language
from .repository import SESSION_LIFETIME, EmailTaken
from .schemas import Email, User, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128  # bounds the work hashing a request can cause
SESSION_COOKIE = "icarus_session"


async def current_user(request: Request, services: ServicesDep) -> User:
    """The signed-in user, from the session cookie; 401 if there isn't one."""
    token = request.cookies.get(SESSION_COOKIE)
    user = await services.sessions.user_for(token, services.users) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


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
    return user.public()


@router.get("/me")
async def me(user: CurrentUser) -> UserPublic:
    """Who is signed in."""
    return user.public()


class PreferencesUpdate(BaseModel):
    language: Language | None


@router.patch("/me/preferences")
async def update_preferences(
    body: PreferencesUpdate, user: CurrentUser, services: ServicesDep
) -> UserPublic:
    """Saves the signed-in user's preferences."""
    updated = await services.users.set_language(user.id, body.language)
    if updated is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return updated.public()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, services: ServicesDep) -> None:
    """Signs out: ends the session and clears its cookie. Fine to call when not signed in."""
    if token := request.cookies.get(SESSION_COOKIE):
        await services.sessions.revoke(token)
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax")
