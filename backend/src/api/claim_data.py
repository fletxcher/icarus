"""Gives the chats, files and simulations made before accounts existed to one account.

    uv run claim-data you@example.com             assign them
    uv run claim-data you@example.com --dry-run   only count them

Those documents have no owner, so nobody can see them until they're claimed. Documents that
already have an owner are never touched.
"""

import argparse
import sys

from pymongo import MongoClient
from pymongo.database import Database

from .catalog import FILES, SIMULATIONS, THREADS, USERS
from .config import get_settings

OWNED = [THREADS, FILES, SIMULATIONS]
UNOWNED = {"user_id": {"$exists": False}}


def claim(db: Database, email: str, *, dry_run: bool = False) -> dict[str, int]:
    """Assigns every unowned chat, file and simulation to the user with this email.

    Returns how many of each were (or, with `dry_run`, would be) assigned.
    Raises LookupError if there's no such user."""
    user = db[USERS].find_one({"email": email.strip().lower()})
    if user is None:
        raise LookupError(f"no account with the email {email}")
    counts = {}
    for name in OWNED:
        if dry_run:
            counts[name] = db[name].count_documents(UNOWNED)
        else:
            counts[name] = (
                db[name].update_many(UNOWNED, {"$set": {"user_id": user["_id"]}}).modified_count
            )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("email", help="the account that should own them")
    parser.add_argument("--dry-run", action="store_true", help="count them without assigning")
    args = parser.parse_args()

    settings = get_settings()
    client = MongoClient(settings.mongodb_uri, tz_aware=True)
    try:
        counts = claim(client[settings.mongodb_db], args.email, dry_run=args.dry_run)
    except LookupError as error:
        sys.exit(str(error))
    finally:
        client.close()
    verb = "would be assigned" if args.dry_run else "assigned"
    print(f"{verb} to {args.email}: " + ", ".join(f"{n} {name}" for name, n in counts.items()))


if __name__ == "__main__":
    main()
