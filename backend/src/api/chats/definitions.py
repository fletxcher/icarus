"""MongoDB-side definitions for `threads` and `messages`: validators and indexes."""

from typing import Any, get_args

from pymongo import ASCENDING, DESCENDING, IndexModel

from .schemas import MessageStatus, Role

NULLABLE_STRING = ["string", "null"]
NULLABLE_DATE = ["date", "null"]
INTEGER = ["int", "long"]


def _enum(literal: Any) -> dict[str, Any]:
    return {"bsonType": "string", "enum": list(get_args(literal))}


def _part(kind: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "bsonType": "object",
        "additionalProperties": False,
        "required": ["type", *required],
        "properties": {"type": {"bsonType": "string", "enum": [kind]}, **properties},
    }


THREADS_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": ["_id", "title", "auto_title", "created_at", "updated_at", "message_seq"],
        "properties": {
            "_id": {"bsonType": "string"},
            # The owner. Missing only on data from before accounts existed; `claim-data` assigns it.
            "user_id": {"bsonType": "string"},
            "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
            # True while the title is a placeholder that a generated one may replace.
            "auto_title": {"bsonType": "bool"},
            "created_at": {"bsonType": "date"},
            # Moves on every new message; the sidebar sorts by it.
            "updated_at": {"bsonType": "date"},
            # Set while the thread is pinned.
            "pinned_at": {"bsonType": NULLABLE_DATE},
            # Counter that gives each message its `seq`; incremented atomically.
            "message_seq": {"bsonType": INTEGER, "minimum": 0},
        },
    }
}

MESSAGES_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": [
            "_id",
            "thread_id",
            "seq",
            "role",
            "content",
            "status",
            "model",
            "parts",
            "attachments",
            "created_at",
        ],
        "properties": {
            "_id": {"bsonType": "string"},
            "thread_id": {"bsonType": "string"},
            # Position within the thread (1, 2, 3, ...). Unique per thread.
            "seq": {"bsonType": INTEGER, "minimum": 1},
            "role": _enum(Role),
            # The answer's text alone (for history, copying and search); `parts` has the rest.
            "content": {"bsonType": "string"},
            "status": _enum(MessageStatus),
            "model": {"bsonType": NULLABLE_STRING},
            # An assistant reply in the order it happened: reasoning, tool calls and text.
            "parts": {
                "bsonType": "array",
                "items": {
                    "oneOf": [
                        _part("text", {"text": {"bsonType": "string"}}, ["text"]),
                        _part("reasoning", {"text": {"bsonType": "string"}}, ["text"]),
                        _part(
                            "tool",
                            {
                                "id": {"bsonType": "string"},
                                "name": {"bsonType": "string"},
                                "args": {"bsonType": "object"},
                                "status": {
                                    "bsonType": "string",
                                    "enum": ["running", "success", "error"],
                                },
                                "result": {"bsonType": NULLABLE_STRING},
                                # UI-only output, e.g. create_plot's Plotly figure.
                                "artifact": {"bsonType": ["object", "null"]},
                            },
                            ["id", "name", "args", "status"],
                        ),
                    ]
                },
            },
            # Files sent with a user message; the content lives in `files`.
            "attachments": {
                "bsonType": "array",
                "maxItems": 10,
                "items": {
                    "bsonType": "object",
                    "additionalProperties": False,
                    "required": ["id", "name", "size", "content_type"],
                    "properties": {
                        "id": {"bsonType": "string"},
                        "name": {"bsonType": "string"},
                        "size": {"bsonType": INTEGER, "minimum": 0},
                        "content_type": {"bsonType": "string"},
                    },
                },
            },
            # Why the reply failed, when status is "error".
            "error": {"bsonType": NULLABLE_STRING},
            "created_at": {"bsonType": "date"},
        },
    }
}

# No explicit names: MongoDB's generated ones (e.g. "updated_at_-1") match the indexes that
# already exist in deployed databases.
THREADS_INDEXES = [
    IndexModel([("updated_at", DESCENDING)]),
    # A user's chats, most recently active first (the sidebar).
    IndexModel([("user_id", ASCENDING), ("updated_at", DESCENDING)], name="user_threads"),
]
MESSAGES_INDEXES = [IndexModel([("thread_id", ASCENDING), ("seq", ASCENDING)], unique=True)]
