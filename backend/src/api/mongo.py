from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MongoModel(BaseModel):
    """Reads Mongo's `_id` into `id`; responses still serialize it as `id`."""

    model_config = ConfigDict(validate_by_name=True)

    id: str = Field(validation_alias="_id")

    def to_mongo(self) -> dict[str, Any]:
        """The document to insert: `id` becomes Mongo's `_id`."""
        document = self.model_dump()
        document["_id"] = document.pop("id")
        return document
