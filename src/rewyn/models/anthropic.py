"""Anthropic adapter (Messages API).

The ``anthropic`` SDK is imported lazily; install with
``pip install "rewyn[anthropic]"``. Thinking configuration, effort,
caching and beta features are passed via ``provider_options`` and forwarded
to ``messages.create``/``messages.stream`` verbatim.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from rewyn.core.types import JSONObject, MissingDependencyError
from rewyn.models.base import (
    FinishReason,
    ImagePart,
    Message,
    Model,
    ModelRequest,
    ModelResponse,
    ReasoningPart,
    Role,
    StreamEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from rewyn.security.redaction import default_redactor

DEFAULT_MAX_TOKENS = 16_000
DEFAULT_STREAM_MAX_TOKENS = 64_000

_STOP: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "content_filter",
    "pause_turn": "other",
}


class AnthropicModel(Model):
    provider: ClassVar[str] = "anthropic"

    def __init__(
        self,
        name: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        **defaults: Any,
    ) -> None:
        super().__init__(name, defaults=defaults)
        self._client = client
        self._client_kwargs: dict[str, Any] = {}
        if api_key is not None:
            self._client_kwargs["api_key"] = api_key
        if base_url is not None:
            self._client_kwargs["base_url"] = base_url
        if timeout is not None:
            self._client_kwargs["timeout"] = timeout
        if max_retries is not None:
            self._client_kwargs["max_retries"] = max_retries
        default_redactor().register_secret(api_key or os.environ.get("ANTHROPIC_API_KEY"))

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover
                raise MissingDependencyError("anthropic", "anthropic") from exc
            self._client = AsyncAnthropic(**self._client_kwargs)
        return self._client

    # Request translation -----------------------------------------------------
    def _build_kwargs(self, request: ModelRequest, *, streaming: bool) -> dict[str, Any]:
        system_text = "\n\n".join(m.text for m in request.messages if m.role is Role.SYSTEM)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens
            or (DEFAULT_STREAM_MAX_TOKENS if streaming else DEFAULT_MAX_TOKENS),
            "messages": [
                _to_anthropic_message(m) for m in request.messages if m.role is not Role.SYSTEM
            ],
        }
        if system_text:
            kwargs["system"] = system_text
        if request.tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                    **({"strict": True} if t.strict else {}),
                }
                for t in request.tools
            ]
        if request.tool_choice is not None:
            kwargs["tool_choice"] = _tool_choice(request.tool_choice)
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.stop:
            kwargs["stop_sequences"] = request.stop
        if request.output_schema is not None:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": request.output_schema}
            }
        for key, value in request.provider_options.items():
            if key == "output_config" and isinstance(value, dict):
                kwargs["output_config"] = {**kwargs.get("output_config", {}), **value}
            else:
                kwargs[key] = value
        return kwargs

    # Provider calls ----------------------------------------------------------
    async def _generate(self, request: ModelRequest) -> ModelResponse:
        message = await self.client.messages.create(**self._build_kwargs(request, streaming=False))
        return self._parse_message(message, request)

    async def _stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        kwargs = self._build_kwargs(request, streaming=True)
        open_tools: dict[int, tuple[str, str]] = {}
        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", "") == "tool_use":
                        open_tools[event.index] = (block.id, block.name)
                        yield StreamEvent(
                            type="tool_call_start", tool_call_id=block.id, tool_name=block.name
                        )
                elif event_type == "content_block_delta":
                    delta = event.delta
                    delta_type = getattr(delta, "type", "")
                    if delta_type == "text_delta":
                        yield StreamEvent(type="text_delta", text=delta.text)
                    elif delta_type == "thinking_delta":
                        yield StreamEvent(type="reasoning_delta", text=delta.thinking)
                    elif delta_type == "input_json_delta" and event.index in open_tools:
                        call_id, name = open_tools[event.index]
                        yield StreamEvent(
                            type="tool_call_delta",
                            tool_call_id=call_id,
                            tool_name=name,
                            arguments_delta=delta.partial_json,
                        )
                elif event_type == "content_block_stop" and event.index in open_tools:
                    call_id, name = open_tools.pop(event.index)
                    yield StreamEvent(type="tool_call_end", tool_call_id=call_id, tool_name=name)
            final = await stream.get_final_message()
        yield StreamEvent(type="response", response=self._parse_message(final, request))

    # Response translation ----------------------------------------------------
    def _parse_message(self, message: Any, request: ModelRequest) -> ModelResponse:
        parts: list[Any] = []
        for block in getattr(message, "content", None) or []:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                parts.append(TextPart(text=block.text))
            elif block_type == "tool_use":
                parts.append(
                    ToolCallPart(id=block.id, name=block.name, arguments=dict(block.input))
                )
            elif block_type == "thinking":
                parts.append(
                    ReasoningPart(
                        text=getattr(block, "thinking", "") or "",
                        signature=getattr(block, "signature", None),
                    )
                )
            elif block_type == "redacted_thinking":
                parts.append(
                    ReasoningPart(
                        provider_data={"type": "redacted_thinking", "data": block.data},
                    )
                )
        usage = getattr(message, "usage", None)
        stop_reason = getattr(message, "stop_reason", None) or ""
        return ModelResponse(
            provider=self.provider,
            model=getattr(message, "model", None) or request.model,
            message=Message(role=Role.ASSISTANT, content=parts),
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
            finish_reason=_STOP.get(stop_reason, "other"),
            provider_response_id=getattr(message, "id", None),
            raw=message,
        )


def _tool_choice(choice: str) -> JSONObject:
    if choice == "auto":
        return {"type": "auto"}
    if choice == "none":
        return {"type": "none"}
    if choice == "required":
        return {"type": "any"}
    return {"type": "tool", "name": choice}


def _image_block(part: ImagePart) -> JSONObject:
    if part.url:
        return {"type": "image", "source": {"type": "url", "url": part.url}}
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": part.media_type or "image/png",
            "data": part.data or "",
        },
    }


def _to_anthropic_message(message: Message) -> JSONObject:
    blocks: list[JSONObject] = []
    if message.role is Role.TOOL:
        for part in message.content:
            if isinstance(part, ToolResultPart):
                block: JSONObject = {
                    "type": "tool_result",
                    "tool_use_id": part.tool_call_id,
                    "content": part.content_text,
                }
                if part.is_error:
                    block["is_error"] = True
                blocks.append(block)
        return {"role": "user", "content": blocks}
    for part in message.content:
        if isinstance(part, TextPart):
            blocks.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            blocks.append(_image_block(part))
        elif isinstance(part, ToolCallPart):
            blocks.append(
                {"type": "tool_use", "id": part.id, "name": part.name, "input": part.arguments}
            )
        elif isinstance(part, ReasoningPart):
            if part.provider_data and part.provider_data.get("type") == "redacted_thinking":
                blocks.append({"type": "redacted_thinking", "data": part.provider_data["data"]})
            elif part.signature:
                blocks.append(
                    {"type": "thinking", "thinking": part.text, "signature": part.signature}
                )
    role = "assistant" if message.role is Role.ASSISTANT else "user"
    if role == "user" and len(blocks) == 1 and blocks[0]["type"] == "text":
        return {"role": role, "content": blocks[0]["text"]}
    return {"role": role, "content": blocks}
