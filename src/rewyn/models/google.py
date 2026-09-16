"""Google Gemini adapter (``google-genai`` SDK).

Imported lazily; install with ``pip install "rewyn[google]"``. Extra
``GenerateContentConfig`` fields go through ``provider_options``.
"""

from __future__ import annotations

import base64
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

_FINISH: dict[str, FinishReason] = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
    "IMAGE_SAFETY": "content_filter",
    "MALFORMED_FUNCTION_CALL": "error",
}


class GeminiModel(Model):
    provider: ClassVar[str] = "google"

    def __init__(
        self,
        name: str,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        vertexai: bool | None = None,
        project: str | None = None,
        location: str | None = None,
        **defaults: Any,
    ) -> None:
        super().__init__(name, defaults=defaults)
        self._client = client
        self._client_kwargs: dict[str, Any] = {}
        if api_key is not None:
            self._client_kwargs["api_key"] = api_key
        if vertexai is not None:
            self._client_kwargs["vertexai"] = vertexai
        if project is not None:
            self._client_kwargs["project"] = project
        if location is not None:
            self._client_kwargs["location"] = location
        default_redactor().register_secrets(
            [api_key, os.environ.get("GOOGLE_API_KEY"), os.environ.get("GEMINI_API_KEY")]
        )

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover
                raise MissingDependencyError("google-genai", "google") from exc
            self._client = genai.Client(**self._client_kwargs)
        return self._client

    @staticmethod
    def _types() -> Any:
        try:
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise MissingDependencyError("google-genai", "google") from exc
        return types

    # Request translation -----------------------------------------------------
    def _build_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        types = self._types()
        config: dict[str, Any] = {}
        system_text = "\n\n".join(m.text for m in request.messages if m.role is Role.SYSTEM)
        if system_text:
            config["system_instruction"] = system_text
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.max_tokens is not None:
            config["max_output_tokens"] = request.max_tokens
        if request.top_p is not None:
            config["top_p"] = request.top_p
        if request.stop:
            config["stop_sequences"] = request.stop
        if request.seed is not None:
            config["seed"] = request.seed
        if request.tools:
            config["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name,
                            description=t.description or None,
                            parameters_json_schema=t.parameters,
                        )
                        for t in request.tools
                    ]
                )
            ]
        if request.tool_choice is not None:
            mode = {"auto": "AUTO", "none": "NONE", "required": "ANY"}.get(request.tool_choice)
            fcc: dict[str, Any] = {"mode": mode or "ANY"}
            if mode is None:
                fcc["allowed_function_names"] = [request.tool_choice]
            config["tool_config"] = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(**fcc)
            )
        if request.output_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_json_schema"] = request.output_schema
        config.update(request.provider_options)
        contents = [
            _to_gemini_content(types, m) for m in request.messages if m.role is not Role.SYSTEM
        ]
        return {
            "model": request.model,
            "contents": contents,
            "config": types.GenerateContentConfig(**config),
        }

    # Provider calls ----------------------------------------------------------
    async def _generate(self, request: ModelRequest) -> ModelResponse:
        response = await self.client.aio.models.generate_content(**self._build_kwargs(request))
        return self._parse_response(response, request)

    async def _stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        stream = await self.client.aio.models.generate_content_stream(**self._build_kwargs(request))
        text_parts: list[str] = []
        reasoning: list[str] = []
        calls: list[ToolCallPart] = []
        usage = Usage()
        finish = "other"
        response_id: str | None = None
        async for chunk in stream:
            response_id = response_id or getattr(chunk, "response_id", None)
            if getattr(chunk, "usage_metadata", None):
                usage = _parse_usage(chunk.usage_metadata)
            for candidate in getattr(chunk, "candidates", None) or []:
                if getattr(candidate, "finish_reason", None):
                    finish = _finish_name(candidate.finish_reason)
                content = getattr(candidate, "content", None)
                for part in (getattr(content, "parts", None) or []) if content else []:
                    if getattr(part, "function_call", None):
                        call = _to_tool_call(part.function_call, len(calls))
                        calls.append(call)
                        yield StreamEvent(
                            type="tool_call_start", tool_call_id=call.id, tool_name=call.name
                        )
                        yield StreamEvent(
                            type="tool_call_end", tool_call_id=call.id, tool_name=call.name
                        )
                    elif getattr(part, "text", None):
                        if getattr(part, "thought", False):
                            reasoning.append(part.text)
                            yield StreamEvent(type="reasoning_delta", text=part.text)
                        else:
                            text_parts.append(part.text)
                            yield StreamEvent(type="text_delta", text=part.text)
        parts: list[Any] = []
        if reasoning:
            parts.append(ReasoningPart(text="".join(reasoning)))
        if text_parts:
            parts.append(TextPart(text="".join(text_parts)))
        parts.extend(calls)
        yield StreamEvent(
            type="response",
            response=ModelResponse(
                provider=self.provider,
                model=request.model,
                message=Message(role=Role.ASSISTANT, content=parts),
                usage=usage,
                finish_reason="tool_calls" if calls else _FINISH.get(finish, "other"),
                provider_response_id=response_id,
            ),
        )

    # Response translation ----------------------------------------------------
    def _parse_response(self, response: Any, request: ModelRequest) -> ModelResponse:
        parts: list[Any] = []
        finish = "other"
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            candidate = candidates[0]
            finish = _finish_name(getattr(candidate, "finish_reason", None))
            content = getattr(candidate, "content", None)
            index = 0
            for part in (getattr(content, "parts", None) or []) if content else []:
                if getattr(part, "function_call", None):
                    parts.append(_to_tool_call(part.function_call, index))
                    index += 1
                elif getattr(part, "text", None):
                    signature = getattr(part, "thought_signature", None)
                    provider_data = (
                        {"thought_signature": base64.b64encode(signature).decode("ascii")}
                        if isinstance(signature, bytes)
                        else None
                    )
                    if getattr(part, "thought", False):
                        parts.append(ReasoningPart(text=part.text, provider_data=provider_data))
                    else:
                        parts.append(TextPart(text=part.text))
        has_calls = any(isinstance(p, ToolCallPart) for p in parts)
        return ModelResponse(
            provider=self.provider,
            model=getattr(response, "model_version", None) or request.model,
            message=Message(role=Role.ASSISTANT, content=parts),
            usage=_parse_usage(getattr(response, "usage_metadata", None)),
            finish_reason="tool_calls" if has_calls else _FINISH.get(finish, "other"),
            provider_response_id=getattr(response, "response_id", None),
            raw=response,
        )


def _finish_name(value: Any) -> str:
    if value is None:
        return "other"
    return str(getattr(value, "name", value)).removeprefix("FinishReason.")


def _to_tool_call(function_call: Any, index: int) -> ToolCallPart:
    args = dict(getattr(function_call, "args", None) or {})
    return ToolCallPart(
        id=getattr(function_call, "id", None) or f"call_{index}",
        name=function_call.name,
        arguments=args,
    )


def _parse_usage(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    return Usage(
        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
        output_tokens=(getattr(usage, "candidates_token_count", 0) or 0)
        + (getattr(usage, "thoughts_token_count", 0) or 0),
        cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
        reasoning_tokens=getattr(usage, "thoughts_token_count", 0) or 0,
    )


def _to_gemini_content(types: Any, message: Message) -> Any:
    parts: list[Any] = []
    for part in message.content:
        if isinstance(part, TextPart):
            parts.append(types.Part(text=part.text))
        elif isinstance(part, ImagePart):
            if part.url:
                parts.append(
                    types.Part(
                        file_data=types.FileData(file_uri=part.url, mime_type=part.media_type)
                    )
                )
            else:
                parts.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=part.media_type or "image/png",
                            data=base64.b64decode(part.data or ""),
                        )
                    )
                )
        elif isinstance(part, ToolCallPart):
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        id=part.id, name=part.name, args=part.arguments
                    )
                )
            )
        elif isinstance(part, ToolResultPart):
            response: JSONObject = (
                part.content if isinstance(part.content, dict) else {"result": part.content}
            )
            if part.is_error:
                response = {"error": response}
            parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=part.tool_call_id, name=part.name or "tool", response=response
                    )
                )
            )
        elif isinstance(part, ReasoningPart):
            signature = (part.provider_data or {}).get("thought_signature")
            if signature:
                parts.append(
                    types.Part(
                        text=part.text or " ",
                        thought=True,
                        thought_signature=base64.b64decode(signature),
                    )
                )
    role = "model" if message.role is Role.ASSISTANT else "user"
    return types.Content(role=role, parts=parts)
