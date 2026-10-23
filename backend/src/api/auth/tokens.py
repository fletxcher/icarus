import hashlib
import secrets


def new_token() -> str:
    """A random session token for the cookie (256 bits). Never stored as-is."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """What is stored in `sessions.token_hash`. A fast hash is enough: tokens are random."""
    return hashlib.sha256(token.encode()).hexdigest()
