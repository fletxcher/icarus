"""MongoDB-side definitions for `credentials`: validator and indexes."""

from typing import Any

from pymongo import ASCENDING, IndexModel

KINDS = ["model_key", "workstation"]

CREDENTIALS_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": [
            "_id",
            "user_id",
            "kind",
            "name",
            "sealed",
            "details",
            "created_at",
            "updated_at",
        ],
        "properties": {
            "_id": {"bsonType": "string"},
            "user_id": {"bsonType": "string"},
            "kind": {"bsonType": "string", "enum": KINDS},
            # The provider for a model key; "default" for the one work station.
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 64},
            # The secret (an API key, or an SSH private key), encrypted with CREDENTIALS_KEY.
            "sealed": {"bsonType": "string", "minLength": 1},
            # What can be shown without decrypting: the key's last characters, or the host,
            # port, user, directory and fingerprints of a work station.
            "details": {"bsonType": "object"},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
    }
}

CREDENTIALS_INDEXES = [
    # One credential per kind and name for each user.
    IndexModel(
        [("user_id", ASCENDING), ("kind", ASCENDING), ("name", ASCENDING)],
        unique=True,
        name="user_credential_unique",
    ),
]
