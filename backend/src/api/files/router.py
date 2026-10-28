import asyncio

from fastapi import APIRouter, HTTPException, Response, UploadFile, status

from ..auth.router import CurrentUser
from ..config import get_settings
from ..dependencies import ServicesDep
from .extract import BINARY_KINDS, Unreadable, extract, kind_of
from .schemas import Attachment

router = APIRouter(prefix="/files", tags=["files"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(file: UploadFile, services: ServicesDep, user: CurrentUser) -> Attachment:
    """Stores a file to attach to a message (send its id in `attachments`).

    Text files go to the model as they are; STL, OBJ and STEP files as a summary of their
    geometry, and PDFs as their extracted text.
    """
    settings = get_settings()
    name = file.filename or "untitled"
    kind = kind_of(name)
    max_bytes = settings.file_max_binary_bytes if kind in BINARY_KINDS else settings.file_max_bytes
    # Read one byte past the limit to detect oversized files without loading them whole.
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"{name} is larger than {max_bytes // 1_000_000 or 1} MB"
            if max_bytes >= 1_000_000
            else f"{name} is larger than {max_bytes // 1024} KB",
        )
    try:
        extracted = await asyncio.to_thread(extract, name, data)
    except Unreadable as problem:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(problem)) from None
    stored = await services.files.create(
        user.id,
        name=name,
        content_type=file.content_type or "application/octet-stream",
        text=extracted.text,
        size=len(data),
        kind=extracted.kind,
        data=data if extracted.kind in BINARY_KINDS else None,
    )
    return stored.as_attachment()


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: str, services: ServicesDep, user: CurrentUser) -> None:
    """Removes a file that hasn't been sent with a message yet."""
    if not await services.files.delete_unattached(file_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found or already sent")


MEDIA_TYPES = {
    "stl": "model/stl",
    "obj": "model/obj",
    "step": "model/step",
    "pdf": "application/pdf",
}


@router.get("/{file_id}/original")
async def download_original(file_id: str, services: ServicesDep, user: CurrentUser) -> Response:
    """The file as it was uploaded (meshes, CAD files and PDFs keep their original bytes), for
    the browser to draw."""
    found = await services.files.original(file_id, user.id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No original is kept for this file")
    stored, data = found
    return Response(
        data,
        media_type=MEDIA_TYPES.get(stored.kind, "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=3600"},
    )
