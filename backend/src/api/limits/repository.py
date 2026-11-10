from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from ..catalog import MESSAGE_USAGE

# A message counts against its user for this long after it's sent.
WINDOW = timedelta(hours=24)


class LimitReached(Exception):
    """The user has used all their messages; `resets_at` is when the oldest one stops counting."""

    def __init__(self, resets_at: datetime) -> None:
        super().__init__(resets_at)
        self.resets_at = resets_at


@dataclass(frozen=True)
class Allowance:
    limit: int
    used: int
    # When the oldest counted message stops counting (a slot opens); None if none are counted.
    resets_at: datetime | None

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)


class MessageLimiter:
    """Allows each user `limit` messages in any 24 hours (0: no limit). A message is one prompt
    and its reply, counted when the reply starts. A rolling window, kept as one document per
    user so that claiming a message is a single atomic update."""

    def __init__(self, db: AsyncDatabase, *, limit: int) -> None:
        self._usage = db[MESSAGE_USAGE]
        self.limit = limit

    @property
    def unlimited(self) -> bool:
        return self.limit <= 0

    async def claim(self, user_id: str) -> str:
        """Counts one message and returns its id (to refund it); raises LimitReached when the
        user has none left. Safe against simultaneous requests."""
        now = datetime.now(UTC)
        claim_id = str(uuid4())
        update = [
            {
                "$set": {
                    "claims": {
                        "$filter": {
                            "input": {"$ifNull": ["$claims", []]},
                            "as": "claim",
                            "cond": {"$gt": ["$$claim.at", now - WINDOW]},
                        }
                    }
                }
            },
            {
                "$set": {
                    "claims": {
                        "$cond": [
                            {"$lt": [{"$size": "$claims"}, self.limit]},
                            {"$concatArrays": ["$claims", [{"id": claim_id, "at": now}]]},
                            "$claims",
                        ]
                    }
                }
            },
        ]
        for _ in range(2):  # twice, in case a simultaneous first claim creates the document
            try:
                await self._usage.update_one({"_id": user_id}, update, upsert=True)
                break
            except DuplicateKeyError:
                continue
        document = await self._usage.find_one({"_id": user_id}) or {"claims": []}
        claims = document["claims"]
        if any(claim["id"] == claim_id for claim in claims):
            return claim_id
        raise LimitReached(min(claim["at"] for claim in claims) + WINDOW)

    async def refund(self, user_id: str, claim_id: str) -> None:
        """Gives a message back, when the reply failed through no fault of the user's."""
        await self._usage.update_one({"_id": user_id}, {"$pull": {"claims": {"id": claim_id}}})

    async def allowance(self, user_id: str) -> Allowance:
        document = await self._usage.find_one({"_id": user_id})
        cutoff = datetime.now(UTC) - WINDOW
        times = sorted(
            claim["at"] for claim in (document or {}).get("claims", []) if claim["at"] > cutoff
        )
        return Allowance(self.limit, len(times), times[0] + WINDOW if times else None)
