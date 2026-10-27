"""MongoDB-side definitions for `files`: validator and indexes."""

from typing import Any

from pymongo import ASCENDING, IndexModel

FILES_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": ["_id", "name", "size", "content_type", "text", "thread_id", "created_at"],
        "properties": {
            "_id": {"bsonType": "string"},
            # The owner. Missing only on data from before accounts existed; `claim-data` assigns it.
            "user_id": {"bsonType": "string"},
            "name": {"bsonType": "string"},
            # Size of the upload in bytes (the text below can have fewer characters).
            "size": {"bsonType": ["int", "long"], "minimum": 0},
            "content_type": {"bsonType": "string"},
            # The file's contents. Only text files are accepted for now.
            "text": {"bsonType": "string"},
            # Null until the file is sent with a message; deleted along with its thread.
            "thread_id": {"bsonType": ["string", "null"]},
            "created_at": {"bsonType": "date"},
        },
    }
}

FILES_INDEXES = [IndexModel([("thread_id", ASCENDING)])]
