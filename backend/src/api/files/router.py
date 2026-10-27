from fastapi import APIRouter, HTTPException, UploadFile, status

from ..auth.router import CurrentUser
from ..config import get_settings
from ..dependencies import ServicesDep
from .schemas import Attachment

router = APIRouter(prefix="/files", tags=["files"])


def decode_text(data: bytes) -> str | None:
    """The file as text, or None for binary files (images, PDFs, ...)."""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(file: UploadFile, services: ServicesDep, user: CurrentUser) -> Attachment:
    """Stores a text file to attach to a message (send its id in `attachments`)."""
    max_bytes = get_settings().file_max_bytes
    # Read one byte past the limit to detect oversized files without loading them whole.
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"{file.filename} is larger than {max_bytes // 1024} KB",
        )
    text = decode_text(data)
    if text is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"{file.filename} isn't a text file; only text files (e.g. .dat, .csv, .txt, "
            ".json) are supported for now",
        )
    stored = await services.files.create(
        user.id,
        name=file.filename or "untitled",
        content_type=file.content_type or "text/plain",
        text=text,
        size=len(data),
    )
    return stored.as_attachment()


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: str, services: ServicesDep, user: CurrentUser) -> None:
    """Removes a file that hasn't been sent with a message yet."""
    if not await services.files.delete_unattached(file_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found or already sent")
