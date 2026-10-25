"""MongoDB-side definitions for `simulations`: validator and indexes."""

from typing import Any, get_args

from pymongo import ASCENDING, DESCENDING, IndexModel

from .schemas import CfdCase, SimulationStatus

NULLABLE_STRING = ["string", "null"]


def _enum(field: str) -> list[str]:
    return list(get_args(CfdCase.model_fields[field].annotation))


SIMULATIONS_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": ["_id", "thread_id", "case", "status", "created_at", "updated_at"],
        "properties": {
            "_id": {"bsonType": "string"},
            # The chat that started the job, if any.
            "thread_id": {"bsonType": NULLABLE_STRING},
            # True once taken off the simulations list; the job itself is kept.
            "hidden": {"bsonType": "bool"},
            # Inputs, as given to the work station in case.json (see CfdCase).
            "case": {
                "bsonType": "object",
                "additionalProperties": False,
                "required": ["reynolds", "mach", "mesh"],
                "properties": {
                    "shape": {"bsonType": "string", "enum": _enum("shape")},
                    # Airfoil only (older jobs have no shape and are all airfoils).
                    "naca": {"bsonType": NULLABLE_STRING, "pattern": r"^\d{4,5}$"},
                    "aspect_ratio": {"bsonType": ["number", "null"], "minimum": 1, "maximum": 20},
                    "half_angle": {"bsonType": ["number", "null"], "minimum": 5, "maximum": 60},
                    "alpha": {"bsonType": "number", "minimum": -30, "maximum": 30},
                    "reynolds": {"bsonType": "number", "minimum": 0, "exclusiveMinimum": True},
                    "mach": {
                        "bsonType": "number",
                        "minimum": 0,
                        "maximum": 5,
                        "exclusiveMaximum": True,
                    },
                    "mesh": {"bsonType": "string", "enum": _enum("mesh")},
                    "wall_type": {"bsonType": "string", "enum": _enum("wall_type")},
                    "wall_velocity_x": {"bsonType": "number"},
                    "wall_velocity_y": {"bsonType": "number"},
                    "spin_ratio": {"bsonType": "number", "minimum": -10, "maximum": 10},
                    "roughness_height": {"bsonType": "number", "minimum": 0, "maximum": 0.05},
                    "regime": {"bsonType": "string", "enum": _enum("regime")},
                    "video": {"bsonType": "bool"},
                },
            },
            "status": {"bsonType": "string", "enum": list(get_args(SimulationStatus))},
            # Written by the work station's run_case.sh (status.json): cl, cd, cm, history,
            # warnings and so on.
            "result": {"bsonType": ["object", "null"]},
            "error": {"bsonType": NULLABLE_STRING},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
    }
}

SIMULATIONS_INDEXES = [IndexModel([("thread_id", ASCENDING), ("created_at", DESCENDING)])]
