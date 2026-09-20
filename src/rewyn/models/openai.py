"""OpenAI adapter (Chat Completions API).

The ``openai`` SDK is imported lazily; install with ``pip install "rewyn[openai]"``.
Provider-specific parameters go through ``provider_options`` and are passed
to ``chat.completions.create`` verbatim.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from rewyn.core.sync import LoopBoundCache
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

_FINISH: dict[str, FinishReason] = {
    "stop": "stop",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "length": "length",
    "content_filter": "content_filter",
}


class OpenAIModel(Model):
    provider: ClassVar[str] = "openai"

    def __init__(
        self,
        name: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
        organization: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        **defaults: Any,
    ) -> None:
        super().__init__(name, defaults=defaults)
        self._clients = LoopBoundCache(self._new_client)
        if client is not None:
            self._clients.set(client)
        self._client_kwargs: dict[str, Any] = {}
        if api_key is not None:
            self._client_kwargs["api_key"] = api_key
        if base_url is not None:
            self._client_kwargs["base_url"] = base_url
        if organization is not None:
            self._client_kwargs["organization"] = organization
        if timeout is not None:
            self._client_kwargs["timeout"] = timeout
        if max_retries is not None:
            self._client_kwargs["max_retries"] = max_retries
        default_redactor().register_secret(api_key or os.environ.get("OPENAI_API_KEY"))

    @property
    def client(self) -> Any:
        return self._clients.get()

    def _new_client(self) -> Any:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - exercised via MissingDependencyError
            raise MissingDependencyError("openai", "openai") from exc
        return AsyncOpenAI(**self._client_kwargs)

    # Request translation -----------------------------------------------------
    def _build_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [m for msg in request.messages for m in _to_openai_messages(msg)],
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                        **({"strict": True} if t.strict else {}),
                    },
                }
                for t in request.tools
            ]
        if request.tool_choice is not None:
            if request.tool_choice in ("auto", "none", "required"):
                kwargs["tool_choice"] = request.tool_choice
            else:
                kwargs["tool_choice"] = {
                    "type": "function",
                    "function": {"name": request.tool_choice},
                }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_completion_tokens"] = request.max_tokens
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.stop:
            kwargs["stop"] = request.stop
        if request.seed is not None:
            kwargs["seed"] = request.seed
        if request.output_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": request.output_schema},
            }
        kwargs.update(request.provider_options)
        return kwargs

    # Provider calls ----------------------------------------------------------
    async def _generate(self, request: ModelRequest) -> ModelResponse:
        completion = await self.client.chat.completions.create(**self._build_kwargs(request))
        return self._parse_completion(completion, request)

    async def _stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        kwargs = self._build_kwargs(request)
        kwargs["stream"] = True
        kwargs.setdefault("stream_options", {"include_usage": True})
        stream = await self.client.chat.completions.create(**kwargs)
        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish: str | None = None
        usage: Usage = Usage()
        response_id: str | None = None
        async for chunk in stream:
            response_id = response_id or getattr(chunk, "id", None)
            if getattr(chunk, "usage", None):
                usage = _parse_usage(chunk.usage)
            for choice in getattr(chunk, "choices", None) or []:
                delta = choice.delta
                if getattr(delta, "content", None):
                    text_parts.append(delta.content)
                    yield StreamEvent(type="text_delta", text=delta.content)
                for tool_delta in getattr(delta, "tool_calls", None) or []:
                    index = tool_delta.index
                    entry = calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
                    if getattr(tool_delta, "id", None):
                        entry["id"] = tool_delta.id
                    function = getattr(tool_delta, "function", None)
                    if function is not None:
                        if getattr(function, "name", None):
                            entry["name"] += function.name
                            yield StreamEvent(
                                type="tool_call_start",
                                tool_call_id=entry["id"],
                                tool_name=entry["name"],
                            )
                        if getattr(function, "arguments", None):
                            entry["arguments"] += function.arguments
                            yield StreamEvent(
                                type="tool_call_delta",
                                tool_call_id=entry["id"],
                                tool_name=entry["name"],
                                arguments_delta=function.arguments,
                            )
                if getattr(choice, "finish_reason", None):
                    finish = choice.finish_reason
        tool_calls = [
            ToolCallPart(
                id=entry["id"] or f"call_{index}",
                name=entry["name"],
                arguments=_parse_arguments(entry["arguments"]),
            )
            for index, entry in sorted(calls.items())
        ]
        for call in tool_calls:
            yield StreamEvent(type="tool_call_end", tool_call_id=call.id, tool_name=call.name)
        message = Message.assistant("".join(text_parts) or None, tool_calls=tool_calls)
        yield StreamEvent(
            type="response",
            response=ModelResponse(
                provider=self.provider,
                model=request.model,
                message=message,
                usage=usage,
                finish_reason=_FINISH.get(finish or "", "tool_calls" if tool_calls else "other"),
                provider_response_id=response_id,
            ),
        )

    # Response translation ----------------------------------------------------
    def _parse_completion(self, completion: Any, request: ModelRequest) -> ModelResponse:
        choice = completion.choices[0]
        message = choice.message
        parts: list[Any] = []
        content = getattr(message, "content", None)
        if content:
            parts.append(TextPart(text=content))
        for call in getattr(message, "tool_calls", None) or []:
            function = getattr(call, "function", None)
            if function is None:
                continue
            parts.append(
                ToolCallPart(
                    id=call.id, name=function.name, arguments=_parse_arguments(function.arguments)
                )
            )
        finish = _FINISH.get(getattr(choice, "finish_reason", None) or "", "other")
        return ModelResponse(
            provider=self.provider,
            model=getattr(completion, "model", None) or request.model,
            message=Message(role=Role.ASSISTANT, content=parts),
            usage=_parse_usage(getattr(completion, "usage", None)),
            finish_reason=finish,
            provider_response_id=getattr(completion, "id", None),
            raw=completion,
        )


def _parse_arguments(raw: str) -> JSONObject:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_value": parsed}


def _parse_usage(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    return Usage(
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        cache_read_tokens=(getattr(prompt_details, "cached_tokens", 0) or 0)
        if prompt_details
        else 0,
        reasoning_tokens=(getattr(completion_details, "reasoning_tokens", 0) or 0)
        if completion_details
        else 0,
    )


def _image_url(part: ImagePart) -> str:
    if part.url:
        return part.url
    return f"data:{part.media_type or 'image/png'};base64,{part.data or ''}"


def _to_openai_messages(message: Message) -> list[JSONObject]:
    if message.role is Role.SYSTEM:
        return [{"role": "system", "content": message.text}]
    if message.role is Role.USER:
        if all(isinstance(p, TextPart) for p in message.content):
            return [{"role": "user", "content": message.text}]
        content: list[JSONObject] = []
        for part in message.content:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                image: JSONObject = {"url": _image_url(part)}
                if part.detail:
                    image["detail"] = part.detail
                content.append({"type": "image_url", "image_url": image})
        return [{"role": "user", "content": content}]
    if message.role is Role.ASSISTANT:
        payload: JSONObject = {"role": "assistant", "content": message.text or None}
        tool_calls = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
        if tool_calls:
            payload["tool_calls"] = tool_calls
        return [payload]
    results: list[JSONObject] = []
    for part in message.content:
        if isinstance(part, ToolResultPart):
            results.append(
                {"role": "tool", "tool_call_id": part.tool_call_id, "content": part.content_text}
            )
        elif isinstance(part, ReasoningPart):
            continue
    return results
