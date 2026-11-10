"""Makes an existing account an admin.

    uv run make-admin you@example.com

Admins aren't held to the daily message limit.
"""

import argparse
import sys

from pymongo import MongoClient

from .catalog import USERS
from .config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("email", help="the account to make an admin")
    args = parser.parse_args()

    settings = get_settings()
    client = MongoClient(settings.mongodb_uri, tz_aware=True)
    try:
        result = client[settings.mongodb_db][USERS].update_one(
            {"email": args.email.strip().lower()}, {"$set": {"role": "admin"}}
        )
    finally:
        client.close()
    if result.matched_count == 0:
        sys.exit(f"no account with the email {args.email}")
    print(f"{args.email} is now an admin")


if __name__ == "__main__":
    main()
