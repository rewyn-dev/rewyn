"""Short-term (conversation/session) memory: a bounded message window."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from rewyn.context.budget import TokenCounter, estimate_tokens
from rewyn.models.base import Message


class ShortTermMemory:
    """Keep the most recent messages within a message and/or token limit."""

    def __init__(
        self,
        *,
        max_messages: int = 50,
        max_tokens: int | None = None,
        counter: TokenCounter = estimate_tokens,
    ) -> None:
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.counter = counter
        self._messages: deque[Message] = deque(maxlen=max_messages)

    def append(self, *messages: Message) -> None:
        self._messages.extend(messages)

    def extend(self, messages: Iterable[Message]) -> None:
        self._messages.extend(messages)

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

    def window(self) -> list[Message]:
        """Most recent messages that fit the token budget, oldest first."""
        if self.max_tokens is None:
            return list(self._messages)
        chosen: list[Message] = []
        used = 0
        for message in reversed(self._messages):
            tokens = self.counter(message.text or "")
            if used + tokens > self.max_tokens:
                break
            used += tokens
            chosen.append(message)
        chosen.reverse()
        return chosen

    def transcript(self) -> str:
        return "\n".join(f"{m.role.value}: {m.text}" for m in self.window() if m.text)
