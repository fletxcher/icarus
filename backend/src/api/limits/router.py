from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ..agent.models import Provider
from ..auth.router import CurrentUser
from ..dependencies import ServicesDep

router = APIRouter(prefix="/usage", tags=["usage"])


class MessageAllowance(BaseModel):
    # Everything below is only meaningful when `unlimited` is false.
    unlimited: bool
    limit: int
    used: int
    remaining: int
    # When the oldest counted message stops counting, so another can be sent.
    resets_at: datetime | None
    # Models of these providers use the user's own key, so they never count.
    own_key_providers: list[Provider]


@router.get("/messages")
async def message_allowance(user: CurrentUser, services: ServicesDep) -> MessageAllowance:
    """How many messages the signed-in user has left in the last 24 hours."""
    allowance = await services.limiter.allowance(user.id)
    keys = await services.credentials.list_model_keys(user.id)
    return MessageAllowance(
        unlimited=services.limiter.unlimited or user.role == "admin",
        limit=allowance.limit,
        used=allowance.used,
        remaining=allowance.remaining,
        resets_at=allowance.resets_at,
        own_key_providers=[key.provider for key in keys],
    )
