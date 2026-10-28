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
            # What the model reads: the file's text, or for a mesh, CAD file or PDF a summary
            # (measurements, or the extracted text) of it.
            "text": {"bsonType": "string"},
            # How the file was read. Missing on files from before this was kept (plain text).
            "kind": {"bsonType": "string", "enum": ["text", "csv", "stl", "obj", "step", "pdf"]},
            # The original bytes of the kinds that are summarized (stl, obj, step, pdf).
            "data": {"bsonType": "binData"},
            # Null until the file is sent with a message; deleted along with its thread.
            "thread_id": {"bsonType": ["string", "null"]},
            "created_at": {"bsonType": "date"},
        },
    }
}

FILES_INDEXES = [IndexModel([("thread_id", ASCENDING)])]
