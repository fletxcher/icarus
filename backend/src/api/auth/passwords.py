import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # Argon2id with the library's current recommended cost
# Checked when no account matches a login, so that case takes as long as a wrong password.
UNMATCHED_HASH = _hasher.hash("no account has this password")


async def hash_password(password: str) -> str:
    """The Argon2id hash stored in `users.password_hash`, computed off the event loop."""
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password: str, password_hash: str) -> bool:
    try:
        return await asyncio.to_thread(_hasher.verify, password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
