import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .agent.models import ModelOption
from .auth import router as auth_router
from .auth.router import CurrentUser
from .chats import router as chats_router
from .compute import router as simulations_router
from .config import get_settings
from .credentials import router as credentials_router
from .db import create_client, ensure_schema
from .dependencies import ServicesDep
from .files import router as files_router
from .log_config import configure_logging
from .middleware import RequestLoggingMiddleware
from .services import build_services
from .status import router as status_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "starting: mongo %s db=%s, default model=%s, xfoil runner=%s",
        settings.mongodb_target,
        settings.mongodb_db,
        settings.default_model,
        settings.xfoil_runner,
    )
    client = create_client(settings)
    db = client[settings.mongodb_db]
    await ensure_schema(db)

    services = build_services(settings, db)
    app.state.services = services
    app.state.db = db
    logger.info("startup complete")
    try:
        yield
    finally:
        logger.info("shutting down")
        await services.close()
        await client.close()


api = APIRouter(prefix="/api")
api.include_router(auth_router.router)
api.include_router(chats_router.router)
api.include_router(simulations_router.router)
api.include_router(files_router.router)
api.include_router(credentials_router.router)
api.include_router(status_router)


@api.get("/health", tags=["meta"])
async def health(request: Request, services: ServicesDep) -> dict[str, bool]:
    await request.app.state.db.command("ping")
    return {"ok": True, "agent": bool(services.server_providers)}


@api.get("/models", tags=["meta"])
async def list_models(services: ServicesDep, user: CurrentUser) -> list[ModelOption]:
    """Models usable with the signed-in user's own keys and the server's."""
    models, _ = await services.agents.models_for(user.id)
    return models


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="Icarus API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # the session cookie, when the frontend is on another origin
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # Added last so it's outermost: it also sees CORS preflights and CORS errors.
    app.add_middleware(RequestLoggingMiddleware, body_max_chars=settings.log_body_max_chars)
    app.include_router(api)
    return app


app = create_app()
