import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.agent.models import MODELS, ModelOption
from backend.chats import router as chats_router
from backend.compute import router as simulations_router
from backend.config import get_settings
from backend.db import create_client, ensure_indexes
from backend.dependencies import ServicesDep
from backend.services import build_services

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    client = create_client(settings)
    db = client[settings.mongodb_db]
    await ensure_indexes(db)

    services = build_services(settings, db)
    if services.agent is None:
        logger.warning("MISTRAL_API_TOKEN is not set; chat replies are disabled")
    app.state.services = services
    app.state.db = db
    try:
        yield
    finally:
        await services.close()
        await client.close()


api = APIRouter(prefix="/api")
api.include_router(chats_router.router)
api.include_router(simulations_router.router)


@api.get("/health", tags=["meta"])
async def health(request: Request, services: ServicesDep) -> dict[str, bool]:
    await request.app.state.db.command("ping")
    return {"ok": True, "agent": services.agent is not None}


@api.get("/models", tags=["meta"])
async def list_models() -> list[ModelOption]:
    return MODELS


def create_app() -> FastAPI:
    app = FastAPI(title="Icarus API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api)
    return app


app = create_app()
