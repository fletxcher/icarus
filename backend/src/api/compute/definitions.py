"""MongoDB-side definitions for `simulations`: validator and indexes."""

from typing import Any, get_args

from pymongo import ASCENDING, DESCENDING, IndexModel

from .schemas import CfdCase, SimulationStatus

NULLABLE_STRING = ["string", "null"]

SIMULATIONS_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": ["_id", "thread_id", "case", "status", "created_at", "updated_at"],
        "properties": {
            "_id": {"bsonType": "string"},
            # The chat that started the job, if any.
            "thread_id": {"bsonType": NULLABLE_STRING},
            # Inputs, as given to the VM in case.json (see CfdCase).
            "case": {
                "bsonType": "object",
                "additionalProperties": False,
                "required": ["naca", "alpha", "reynolds", "mach", "mesh"],
                "properties": {
                    "naca": {"bsonType": "string", "pattern": r"^\d{4,5}$"},
                    "alpha": {"bsonType": "number", "minimum": -30, "maximum": 30},
                    "reynolds": {"bsonType": "number", "minimum": 0, "exclusiveMinimum": True},
                    "mach": {
                        "bsonType": "number",
                        "minimum": 0,
                        "maximum": 5,
                        "exclusiveMaximum": True,
                    },
                    "mesh": {
                        "bsonType": "string",
                        "enum": list(get_args(CfdCase.model_fields["mesh"].annotation)),
                    },
                },
            },
            "status": {"bsonType": "string", "enum": list(get_args(SimulationStatus))},
            # Written by the VM's run_case.sh (status.json), e.g. {"cl": 0.9, "cd": 0.01}.
            "result": {"bsonType": ["object", "null"]},
            "error": {"bsonType": NULLABLE_STRING},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
    }
}

SIMULATIONS_INDEXES = [IndexModel([("thread_id", ASCENDING), ("created_at", DESCENDING)])]
