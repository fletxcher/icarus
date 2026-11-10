"""MongoDB-side definition for `message_usage`: validator and indexes."""

from typing import Any

from pymongo import IndexModel

MESSAGE_USAGE_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": ["_id", "claims"],
        "properties": {
            # The user's id: one document per user.
            "_id": {"bsonType": "string"},
            # The messages counted in the last 24 hours (older ones are dropped on the next claim).
            "claims": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "additionalProperties": False,
                    "required": ["id", "at"],
                    "properties": {"id": {"bsonType": "string"}, "at": {"bsonType": "date"}},
                },
            },
        },
    }
}

MESSAGE_USAGE_INDEXES: list[IndexModel] = []
