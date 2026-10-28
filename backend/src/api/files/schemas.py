from datetime import datetime

from pydantic import BaseModel

from ..mongo import MongoModel


class Attachment(BaseModel):
    """A file as shown on a message; the content stays in the files collection."""

    id: str
    name: str
    size: int
    content_type: str


class StoredFile(MongoModel):
    name: str
    size: int
    content_type: str
    # text, csv, stl, obj, step or pdf (see extract.py); files from before this was kept are text.
    kind: str = "text"
    # Set once the file is sent with a message; the file is deleted with its thread.
    thread_id: str | None = None
    created_at: datetime

    def as_attachment(self) -> Attachment:
        return Attachment(
            id=self.id, name=self.name, size=self.size, content_type=self.content_type
        )
