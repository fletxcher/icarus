"""Names new chats from their first message."""

import asyncio
import logging
import re
from collections.abc import Mapping

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from api.agent.stream import split_message
from api.chats.repository import ChatRepository

logger = logging.getLogger(__name__)

TITLE_PROMPT = (
    "You write short titles for chat conversations. Reply with only the title for the "
    "conversation that starts with the user's message: 2 to 6 words, no quotes, no "
    "trailing punctuation."
)
TITLE_MAX_LENGTH = 60
MESSAGE_MAX_CHARS = 2000
TIMEOUT_S = 30


def clean_title(raw: str) -> str | None:
    """First line of the model's reply, without quotes, markdown or trailing punctuation."""
    line = next((line for line in raw.splitlines() if line.strip()), "")
    line = re.sub(r"^(title:\s*)", "", line.strip(), flags=re.IGNORECASE)
    line = line.strip(" \"'`*#").rstrip(".!?:;")
    line = re.sub(r"\s+", " ", line).strip()
    if len(line) > TITLE_MAX_LENGTH:
        line = line[:TITLE_MAX_LENGTH].rsplit(" ", 1)[0]
    return line or None


class TitleGenerator:
    def __init__(self, chats: ChatRepository, models: Mapping[str, BaseChatModel]) -> None:
        self._chats = chats
        self._models = models
        # Keeps running tasks referenced: one may outlive its request (e.g. when the user
        # stops the reply) and should still finish and save the title.
        self._tasks: set[asyncio.Task[str | None]] = set()

    def start(self, thread_id: str, model_id: str, message: str) -> asyncio.Task[str | None]:
        """Generates a title in the background; await the task for the saved title."""
        task = asyncio.create_task(self.generate(thread_id, model_id, message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def generate(self, thread_id: str, model_id: str, message: str) -> str | None:
        """Asks the model for a title and saves it unless the user renamed the chat.

        Returns the saved title, or None if none was generated or it wasn't applied.
        """
        model = self._models.get(model_id)
        if model is None:
            return None
        try:
            response = await asyncio.wait_for(
                model.ainvoke(
                    [SystemMessage(TITLE_PROMPT), HumanMessage(message[:MESSAGE_MAX_CHARS])]
                ),
                TIMEOUT_S,
            )
        except Exception:
            logger.warning("title generation failed for thread %s", thread_id, exc_info=True)
            return None

        _, text = split_message(response if isinstance(response, AIMessage) else AIMessage(""))
        title = clean_title(text)
        if title is None:
            logger.warning("title generation for thread %s returned no title", thread_id)
            return None
        if not await self._chats.set_auto_title(thread_id, title):
            logger.info("thread %s was renamed meanwhile; keeping the user's title", thread_id)
            return None
        logger.info("titled thread %s: %r", thread_id, title)
        return title
