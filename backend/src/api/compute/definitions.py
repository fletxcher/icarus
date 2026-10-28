"""MongoDB-side definitions for `simulations`: validator and indexes."""

from typing import Any, get_args

from pymongo import ASCENDING, DESCENDING, IndexModel

from .schemas import CfdCase, FeaMaterial, SimulationStatus

NULLABLE_STRING = ["string", "null"]


def _enum(field: str) -> list[str]:
    return list(get_args(CfdCase.model_fields[field].annotation))


CFD_CASE: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["reynolds", "mach", "mesh"],
    "properties": {
        "kind": {"bsonType": "string", "enum": ["cfd"]},
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
}

FEA_CASE: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["kind", "geometry_file_id", "support_axis", "support_side"],
    "properties": {
        "kind": {"bsonType": "string", "enum": ["fea"]},
        "geometry_file_id": {"bsonType": "string"},
        "geometry_name": {"bsonType": NULLABLE_STRING},
        "units": {"bsonType": "string", "enum": ["mm", "m", "in"]},
        "material": {"bsonType": "string", "enum": list(get_args(FeaMaterial))},
        "mesh": {"bsonType": "string", "enum": ["coarse", "medium", "fine"]},
        "support_axis": {"bsonType": "string", "enum": ["x", "y", "z"]},
        "support_side": {"bsonType": "string", "enum": ["min", "max"]},
        "spin_rpm": {"bsonType": "number", "minimum": 0, "maximum": 200000},
        "spin_axis_origin": {
            "bsonType": "array",
            "items": {"bsonType": "number"},
            "minItems": 3,
            "maxItems": 3,
        },
        "spin_axis_direction": {
            "bsonType": "array",
            "items": {"bsonType": "number"},
            "minItems": 3,
            "maxItems": 3,
        },
        "pressure_mpa": {"bsonType": "number", "minimum": -100, "maximum": 100},
        "gas_temperature_c": {"bsonType": ["number", "null"], "minimum": -50, "maximum": 1500},
        "htc_w_m2k": {
            "bsonType": "number",
            "minimum": 0,
            "exclusiveMinimum": True,
            "maximum": 50000,
        },
        "base_temperature_c": {"bsonType": "number", "minimum": -50, "maximum": 1500},
        "modal": {"bsonType": "bool"},
    },
}

SIMULATIONS_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "additionalProperties": False,
        "required": ["_id", "thread_id", "case", "status", "created_at", "updated_at"],
        "properties": {
            "_id": {"bsonType": "string"},
            # The owner. Missing only on data from before accounts existed; `claim-data` assigns it.
            "user_id": {"bsonType": "string"},
            # The chat that started the job, if any.
            "thread_id": {"bsonType": NULLABLE_STRING},
            # True once taken off the simulations list; the job itself is kept.
            "hidden": {"bsonType": "bool"},
            # Inputs, as given to the work station in case.json: a CfdCase or a FeaCase, told
            # apart by `kind` (older CFD jobs have none).
            "case": {"anyOf": [CFD_CASE, FEA_CASE]},
            "status": {"bsonType": "string", "enum": list(get_args(SimulationStatus))},
            # Written by the work station's run_openfoam.sh (status.json): cl, cd, cm, history,
            # warnings and so on.
            "result": {"bsonType": ["object", "null"]},
            "error": {"bsonType": NULLABLE_STRING},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
    }
}

SIMULATIONS_INDEXES = [
    IndexModel([("thread_id", ASCENDING), ("created_at", DESCENDING)]),
    # A user's simulations, newest first (the list and the usage totals).
    IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_simulations"),
]
