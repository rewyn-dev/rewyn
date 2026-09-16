"""A scripted model for deterministic tests and offline demos.

``FakeModel`` replays a queue of scripted responses. Each script entry may
be a string (assistant text), a :class:`ModelResponse`, a :class:`Message`,
or a callable receiving the :class:`ModelRequest` and returning one of
those. Tool calls are scripted with :meth:`FakeModel.tool_call`.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from typing import Any, ClassVar

from rewyn.models.base import (
    Message,
    Model,
    ModelError,
    ModelRequest,
    ModelResponse,
    StreamEvent,
    TextPart,
    ToolCallPart,
    Usage,
)
from rewyn.models.pricing import Price, register_price

Scripted = str | Message | ModelResponse | Callable[[ModelRequest], Any]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FakeModel(Model):
    provider: ClassVar[str] = "fake"

    def __init__(
        self,
        responses: Iterable[Scripted] | None = None,
        *,
        name: str = "fake-1",
        default_response: str = "ok",
        chunk_size: int = 8,
        cycle: bool = False,
    ) -> None:
        super().__init__(name)
        self._script: deque[Scripted] = deque(responses or [])
        self._initial: list[Scripted] = list(self._script)
        self.default_response = default_response
        self.chunk_size = chunk_size
        self.cycle = cycle
        self.requests: list[ModelRequest] = []
        self.calls = 0

    # Scripting helpers -------------------------------------------------------
    @staticmethod
    def tool_call(name: str, arguments: dict[str, Any] | None = None, *, text: str = "") -> Message:
        return Message.assistant(
            text or None, tool_calls=[ToolCallPart(name=name, arguments=arguments or {})]
        )

    @staticmethod
    def tool_calls(*calls: tuple[str, dict[str, Any]]) -> Message:
        return Message.assistant(
            tool_calls=[ToolCallPart(name=name, arguments=args) for name, args in calls]
        )

    def enqueue(self, *responses: Scripted) -> FakeModel:
        self._script.extend(responses)
        return self

    def reset(self) -> None:
        self._script = deque(self._initial)
        self.requests.clear()
        self.calls = 0

    @property
    def remaining(self) -> int:
        return len(self._script)

    # Model contract ----------------------------------------------------------
    def _next(self, request: ModelRequest) -> ModelResponse:
        if self._script:
            item = self._script.popleft()
            if self.cycle:
                self._script.append(item)
        else:
            item = self.default_response
        if callable(item) and not isinstance(item, str | Message | ModelResponse):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ModelResponse):
            return item
        if isinstance(item, str):
            item = Message.assistant(item)
        if not isinstance(item, Message):
            raise ModelError(f"FakeModel cannot use scripted item {item!r}")
        input_text = "".join(m.text for m in request.messages)
        return ModelResponse(
            provider=self.provider,
            model=self.name,
            message=item,
            usage=Usage(
                input_tokens=_estimate_tokens(input_text),
                output_tokens=_estimate_tokens(item.text) + 5 * len(item.tool_calls),
            ),
            finish_reason="tool_calls" if item.tool_calls else "stop",
        )

    async def _generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        self.requests.append(request)
        return self._next(request)

    async def _stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        response = await self._generate(request)
        for part in response.message.content:
            if isinstance(part, TextPart):
                for start in range(0, len(part.text), self.chunk_size):
                    yield StreamEvent(
                        type="text_delta", text=part.text[start : start + self.chunk_size]
                    )
            elif isinstance(part, ToolCallPart):
                yield StreamEvent(type="tool_call_start", tool_call_id=part.id, tool_name=part.name)
                yield StreamEvent(type="tool_call_end", tool_call_id=part.id, tool_name=part.name)
        yield StreamEvent(type="response", response=response)


def script_sequence(*texts: str) -> Sequence[Scripted]:
    return list(texts)


register_price("fake", "fake", Price(1.0, 2.0))
