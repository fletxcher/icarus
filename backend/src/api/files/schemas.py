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
    # Set once the file is sent with a message; the file is deleted with its thread.
    thread_id: str | None = None
    created_at: datetime

    def as_attachment(self) -> Attachment:
        return Attachment(
            id=self.id, name=self.name, size=self.size, content_type=self.content_type
        )
