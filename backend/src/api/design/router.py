from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from ..auth.router import CurrentUser
from ..dependencies import ServicesDep
from .service import FORMATS, DesignError

router = APIRouter(prefix="/designs", tags=["designs"])


@router.get("/formats")
async def formats() -> list[str]:
    """The formats a designed part can be exported in."""
    return list(FORMATS)


@router.get("/{file_id}/export")
async def export_design(
    file_id: str,
    format: Annotated[str, Query(description="One of the formats from /designs/formats")],
    services: ServicesDep,
    user: CurrentUser,
) -> Response:
    """A part the agent designed, built again from its code in the asked-for format, to download."""
    if format.lower() not in FORMATS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown format {format!r}")
    designs = await services.compute.designs_for(user.id)
    try:
        exported = await designs.export(file_id, format)
    except DesignError as problem:
        missing = str(problem).startswith("no designed part")
        raise HTTPException(
            status.HTTP_404_NOT_FOUND if missing else status.HTTP_502_BAD_GATEWAY, str(problem)
        ) from None
    return Response(
        exported.data,
        media_type=exported.media_type,
        headers={"Content-Disposition": f'attachment; filename="{exported.name}"'},
    )
