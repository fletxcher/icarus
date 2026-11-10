"""MongoDB-side definitions for `users` and `sessions`: validators and indexes."""

from typing import Any, get_args

from pymongo import ASCENDING, DESCENDING, IndexModel

from .schemas import IdentityProvider, Role, Theme, UserStatus

NULLABLE_STRING = ["string", "null"]
NULLABLE_DATE = ["date", "null"]


def _enum(literal: Any) -> dict[str, Any]:
    return {"bsonType": "string", "enum": list(get_args(literal))}


USERS_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": [
            "_id",
            "email",
            "identities",
            "role",
            "status",
            "preferences",
            "created_at",
            "updated_at",
        ],
        "properties": {
            "_id": {"bsonType": "string"},
            # Lowercase only, so the unique index can't be dodged by capitalization.
            "email": {
                "bsonType": "string",
                "maxLength": 254,
                "pattern": r"^[^\sA-Z@]+@[^\sA-Z@]+$",
            },
            "email_verified_at": {"bsonType": NULLABLE_DATE},
            "name": {"bsonType": NULLABLE_STRING, "maxLength": 100},
            "avatar_url": {"bsonType": NULLABLE_STRING, "maxLength": 2048},
            "password_hash": {"bsonType": NULLABLE_STRING, "maxLength": 512},
            "identities": {
                "bsonType": "array",
                "maxItems": 10,
                "items": {
                    "bsonType": "object",
                    "additionalProperties": False,
                    "required": ["provider", "subject", "linked_at"],
                    "properties": {
                        "provider": _enum(IdentityProvider),
                        "subject": {"bsonType": "string", "minLength": 1, "maxLength": 255},
                        "linked_at": {"bsonType": "date"},
                    },
                },
            },
            "role": _enum(Role),
            "status": _enum(UserStatus),
            "preferences": {
                "bsonType": "object",
                "additionalProperties": False,
                "required": ["theme"],
                "properties": {
                    "default_model": {"bsonType": NULLABLE_STRING},
                    "theme": _enum(Theme),
                },
            },
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
            "last_login_at": {"bsonType": NULLABLE_DATE},
        },
    }
}

SESSIONS_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": ["_id", "user_id", "token_hash", "created_at", "last_seen_at", "expires_at"],
        "properties": {
            "_id": {"bsonType": "string"},
            "user_id": {"bsonType": "string"},
            "token_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
            "created_at": {"bsonType": "date"},
            "last_seen_at": {"bsonType": "date"},
            "expires_at": {"bsonType": "date"},
            "revoked_at": {"bsonType": NULLABLE_DATE},
            "user_agent": {"bsonType": NULLABLE_STRING, "maxLength": 512},
            "ip": {"bsonType": NULLABLE_STRING, "maxLength": 45},
        },
    }
}

USERS_INDEXES = [
    IndexModel([("email", ASCENDING)], unique=True, name="email_unique"),
    # One account per provider identity. Partial, because users without any identity would
    # otherwise all collide on the empty array's null entry.
    IndexModel(
        [("identities.provider", ASCENDING), ("identities.subject", ASCENDING)],
        unique=True,
        partialFilterExpression={"identities.0": {"$exists": True}},
        name="identity_unique",
    ),
]

SESSIONS_INDEXES = [
    # Looking up the session for a request's cookie.
    IndexModel([("token_hash", ASCENDING)], unique=True, name="token_hash_unique"),
    # MongoDB removes a session once its expiry time has passed (checked about every minute).
    IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="expires_at_ttl"),
    # A user's sessions, newest first: the "signed-in devices" list and sign out everywhere.
    IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_sessions"),
]
