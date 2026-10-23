"""Writes each collection's schema and indexes to `database/<collection>.json`.

    uv run export-schemas           regenerate the files
    uv run export-schemas --check   exit 1 if the files are out of date (also run by tests)

The files document the database for people (and can be applied with `mongosh` or
Compass); the backend itself reads the Python definitions, not these files.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pymongo import IndexModel

from .catalog import COLLECTIONS, CollectionSpec

# <repo>/database, next to backend/ and frontend/.
DATABASE_DIR = Path(__file__).resolve().parents[3] / "database"


def _index(index: IndexModel) -> dict[str, Any]:
    document = dict(index.document)
    document["key"] = dict(document["key"])  # SON -> dict; key order is kept
    return {"name": document.pop("name"), **document}


def collection_document(spec: CollectionSpec) -> dict[str, Any]:
    return {
        "collection": spec.name,
        "description": spec.description,
        "enforced": spec.enforced,
        "validator": spec.validator,
        "indexes": [_index(index) for index in spec.indexes],
    }


def render(spec: CollectionSpec) -> str:
    return json.dumps(collection_document(spec), indent=2, ensure_ascii=False) + "\n"


def expected_files() -> dict[str, str]:
    """File name -> contents, for every collection in the catalog."""
    return {f"{spec.name}.json": render(spec) for spec in COLLECTIONS}


def stale_files(directory: Path = DATABASE_DIR) -> list[str]:
    """Names of files that are missing, differ from the catalog, or don't belong."""
    expected = expected_files()
    problems = [
        name
        for name, content in expected.items()
        if not (directory / name).is_file() or (directory / name).read_text() != content
    ]
    if directory.is_dir():
        problems += [path.name for path in directory.glob("*.json") if path.name not in expected]
    return sorted(problems)


def write_files(directory: Path = DATABASE_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.glob("*.json"):
        path.unlink()  # drops files of collections that no longer exist
    written = []
    for name, content in expected_files().items():
        (directory / name).write_text(content)
        written.append(directory / name)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if files are out of date")
    arguments = parser.parse_args()

    if arguments.check:
        if problems := stale_files():
            print("out of date (run `uv run export-schemas`):", ", ".join(problems))
            sys.exit(1)
        print(f"{DATABASE_DIR.name}/ is up to date")
        return
    for path in write_files():
        print(f"wrote {path.relative_to(DATABASE_DIR.parent)}")
