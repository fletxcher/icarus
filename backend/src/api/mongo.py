from pydantic import BaseModel, ConfigDict, Field


class MongoModel(BaseModel):
    """Reads Mongo's `_id` into `id`; responses still serialize it as `id`."""

    model_config = ConfigDict(validate_by_name=True)

    id: str = Field(validation_alias="_id")
