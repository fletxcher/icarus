"""Encryption for secrets stored in the database (users' API keys and SSH private keys).

Secrets are sealed with Fernet (AES-128-CBC with an HMAC) under `CREDENTIALS_KEY`. Each one
is sealed together with who it belongs to, so a sealed value copied onto another user's
document, or another credential's, is refused instead of being used.

`CREDENTIALS_KEY` takes several comma-separated keys: the first seals new values and all of
them open old ones, so a key is rotated by putting the new one in front.
"""

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr


class VaultUnavailable(Exception):
    """No (valid) CREDENTIALS_KEY is set, so secrets can't be stored or read."""


class SealedValueRejected(Exception):
    """A stored secret can't be opened: wrong key, damaged, or it belongs to someone else."""


class Vault:
    def __init__(self, key: SecretStr | None) -> None:
        self._fernet: MultiFernet | None = None
        if key is None:
            return
        try:
            keys = [part.strip() for part in key.get_secret_value().split(",") if part.strip()]
            self._fernet = MultiFernet([Fernet(part) for part in keys]) if keys else None
        except ValueError as error:
            raise VaultUnavailable(
                "CREDENTIALS_KEY isn't a valid Fernet key; make one with `python -c "
                '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`'
            ) from error

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def _require(self) -> MultiFernet:
        if self._fernet is None:
            raise VaultUnavailable("CREDENTIALS_KEY isn't set, so credentials can't be stored")
        return self._fernet

    @staticmethod
    def _context(user_id: str, kind: str, name: str) -> dict[str, str]:
        return {"user": user_id, "kind": kind, "name": name}

    def seal(self, user_id: str, kind: str, name: str, value: Any) -> str:
        """Encrypts a JSON-serializable value for this user's credential."""
        payload = {**self._context(user_id, kind, name), "value": value}
        return self._require().encrypt(json.dumps(payload).encode()).decode()

    def open(self, user_id: str, kind: str, name: str, sealed: str) -> Any:
        try:
            payload = json.loads(self._require().decrypt(sealed.encode()))
        except (InvalidToken, ValueError) as error:
            raise SealedValueRejected("the stored credential can't be decrypted") from error
        if {k: payload.get(k) for k in ("user", "kind", "name")} != self._context(
            user_id, kind, name
        ):
            raise SealedValueRejected("the stored credential belongs to another entry")
        return payload["value"]
