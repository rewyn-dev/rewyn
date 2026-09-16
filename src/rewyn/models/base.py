"""Unified model interface (spec §6, §24).

A :class:`Model` turns a provider-agnostic :class:`ModelRequest` into a
:class:`ModelResponse`. Every call emits ``MODEL_CALLED`` and
``MODEL_RESPONSE`` events into the active run, records usage and cost, and
validates structured output when an ``output_schema`` is requested.

Provider adapters subclass :class:`Model` and implement ``_generate`` (and
optionally ``_stream``). They never touch events or budgets themselves.
"""

from __future__ import annotations

import abc
import json
import re
import time
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from rewyn.core.event import EventType
from rewyn.core.run import DependencyRef, aensure_run
from rewyn.core.schema import (
    fingerprint,
    format_validation_error,
    parse_as,
    schema_for_type,
    validate_json,
)
from rewyn.core.span import SpanKind
from rewyn.core.sync import iterate_sync, run_sync
from rewyn.core.types import JSONObject, RewynError, new_id
from rewyn.models.pricing import compute_cost


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """An image given by URL or base64 ``data`` with a ``media_type``."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["image"] = "image"
    url: str | None = None
    data: str | None = None
    media_type: str | None = None
    detail: str | None = None


class ToolCallPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tool_call"] = "tool_call"
    id: str = Field(default_factory=lambda: new_id("call"))
    name: str
    arguments: JSONObject = Field(default_factory=dict)


class ToolResultPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    name: str | None = None
    content: Any = None
    is_error: bool = False

    @property
    def content_text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return json.dumps(self.content, ensure_ascii=False, default=str)


class ReasoningPart(BaseModel):
    """Provider reasoning/thinking content. ``provider_data`` is passed back verbatim."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["reasoning"] = "reasoning"
    text: str = ""
    signature: str | None = None
    provider_data: JSONObject | None = None


Part = Annotated[
    TextPart | ImagePart | ToolCallPart | ToolResultPart | ReasoningPart,
    Field(discriminator="type"),
]


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    content: list[Part] = Field(default_factory=list)
    name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_content(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and isinstance(data.get("content"), str):
            data = dict(data)
            data["content"] = [{"type": "text", "text": data["content"]}]
        return data

    # Constructors --------------------------------------------------------------
    @classmethod
    def system(cls, text: str) -> Message:
        return cls(role=Role.SYSTEM, content=[TextPart(text=text)])

    @classmethod
    def user(cls, content: str | Sequence[Part]) -> Message:
        parts = [TextPart(text=content)] if isinstance(content, str) else list(content)
        return cls(role=Role.USER, content=parts)

    @classmethod
    def assistant(
        cls, text: str | None = None, *, tool_calls: Sequence[ToolCallPart] = ()
    ) -> Message:
        parts: list[Part] = []
        if text:
            parts.append(TextPart(text=text))
        parts.extend(tool_calls)
        return cls(role=Role.ASSISTANT, content=parts)

    @classmethod
    def tool(cls, results: Sequence[ToolResultPart]) -> Message:
        return cls(role=Role.TOOL, content=list(results))

    # Accessors -------------------------------------------------------------------
    @property
    def text(self) -> str:
        return "".join(p.text for p in self.content if isinstance(p, TextPart))

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return [p for p in self.content if isinstance(p, ToolCallPart)]

    @property
    def tool_results(self) -> list[ToolResultPart]:
        return [p for p in self.content if isinstance(p, ToolResultPart)]

    @property
    def reasoning(self) -> list[ReasoningPart]:
        return [p for p in self.content if isinstance(p, ReasoningPart)]


MessageLike = str | Message | Mapping[str, Any]


def coerce_messages(messages: MessageLike | Sequence[MessageLike]) -> list[Message]:
    """Normalise ``str``/dict/``Message`` inputs into a list of ``Message``."""
    if isinstance(messages, str | Message | Mapping):
        messages = [messages]
    result: list[Message] = []
    for item in messages:
        if isinstance(item, Message):
            result.append(item)
        elif isinstance(item, str):
            result.append(Message.user(item))
        else:
            result.append(Message.model_validate(item))
    return result


class ToolSpec(BaseModel):
    """Provider-neutral tool definition."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parameters: JSONObject = Field(default_factory=lambda: {"type": "object", "properties": {}})
    strict: bool = False


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class Cost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = 0.0
    output: float = 0.0
    total: float = 0.0
    currency: str = "USD"
    source: Literal["table", "provider", "unknown"] = "unknown"


FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "error", "other"]
ToolChoice = Literal["auto", "none", "required"] | str


class ModelRequest(BaseModel):
    """Everything sent to a provider, in provider-neutral form. Fingerprintable."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    tool_choice: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] = Field(default_factory=list)
    seed: int | None = None
    output_schema: JSONObject | None = None
    provider_options: JSONObject = Field(default_factory=dict)
    stream: bool = False
    metadata: JSONObject = Field(default_factory=dict)

    def fingerprint(self) -> str:
        return fingerprint(self.model_dump(mode="json", exclude={"metadata", "stream"}))

    def has_tool(self, name: str) -> bool:
        return any(t.name == name for t in self.tools)


class OutputValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_fingerprint: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    repaired: bool = False


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: new_id("resp"))
    provider: str
    model: str
    message: Message
    usage: Usage = Field(default_factory=Usage)
    cost: Cost = Field(default_factory=Cost)
    finish_reason: FinishReason = "stop"
    latency_ms: float = 0.0
    structured: Any = None
    validation: OutputValidation | None = None
    request_fingerprint: str | None = None
    provider_response_id: str | None = None
    raw: Any = Field(default=None, exclude=True, repr=False)

    @property
    def text(self) -> str:
        return self.message.text

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return self.message.tool_calls

    def to_record(self) -> JSONObject:
        return self.model_dump(mode="json")


class StreamEvent(BaseModel):
    """One increment of a streamed response. The final event carries ``response``."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "text_delta",
        "reasoning_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
        "response",
    ]
    text: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str = ""
    response: ModelResponse | None = None


class ModelError(RewynError):
    """A provider call failed."""


class StructuredOutputError(RewynError):
    """The model's output did not satisfy the requested schema."""

    def __init__(self, errors: Sequence[str], response: ModelResponse) -> None:
        super().__init__("structured output failed validation: " + "; ".join(errors))
        self.errors = list(errors)
        self.response = response


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json(text: str) -> tuple[Any, bool]:
    """Parse JSON from model text, tolerating code fences and surrounding prose.

    Returns ``(value, repaired)`` where ``repaired`` is True when the text
    needed cleanup before it parsed.
    """
    stripped = text.strip()
    try:
        return json.loads(stripped), False
    except json.JSONDecodeError:
        pass
    fenced = _FENCE.match(stripped)
    if fenced:
        try:
            return json.loads(fenced.group(1)), True
        except json.JSONDecodeError:
            stripped = fenced.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1]), True
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON value found in model output")


class Model(abc.ABC):
    """Base class for all model adapters."""

    provider: ClassVar[str] = "abstract"

    def __init__(self, name: str, *, defaults: Mapping[str, Any] | None = None) -> None:
        self.name = name
        self.defaults: dict[str, Any] = dict(defaults or {})

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    @property
    def dependency(self) -> DependencyRef:
        return DependencyRef(kind="model", name=f"{self.provider}:{self.name}")

    # Provider contract ---------------------------------------------------------
    @abc.abstractmethod
    async def _generate(self, request: ModelRequest) -> ModelResponse:
        """Perform one provider call. Must not emit events."""

    async def _stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Stream a provider call. Default: no deltas, just the final response."""
        response = await self._generate(request)
        yield StreamEvent(type="text_delta", text=response.text)
        yield StreamEvent(type="response", response=response)

    # Public API ----------------------------------------------------------------
    def build_request(
        self,
        messages: MessageLike | Sequence[MessageLike],
        *,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        stop: Sequence[str] | None = None,
        seed: int | None = None,
        output_schema: Any = None,
        provider_options: Mapping[str, Any] | None = None,
        stream: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> ModelRequest:
        options = {**self.defaults}
        for key, value in (
            ("temperature", temperature),
            ("max_tokens", max_tokens),
            ("top_p", top_p),
            ("seed", seed),
            ("tool_choice", tool_choice),
        ):
            if value is not None:
                options[key] = value
        schema = _resolve_schema(output_schema)
        merged_provider_options = {
            **options.pop("provider_options", {}),
            **dict(provider_options or {}),
        }
        return ModelRequest(
            provider=self.provider,
            model=self.name,
            messages=coerce_messages(messages),
            tools=list(tools or []),
            stop=list(stop or options.pop("stop", [])),
            output_schema=schema,
            provider_options=merged_provider_options,
            stream=stream,
            metadata=dict(metadata or {}),
            **options,
        )

    async def agenerate(
        self,
        messages: MessageLike | Sequence[MessageLike],
        *,
        tools: Sequence[ToolSpec] | None = None,
        output_schema: Any = None,
        strict_output: bool = True,
        **options: Any,
    ) -> ModelResponse:
        request = self.build_request(messages, tools=tools, output_schema=output_schema, **options)
        return await self._instrumented_generate(request, output_schema, strict_output)

    def generate(
        self,
        messages: MessageLike | Sequence[MessageLike],
        *,
        tools: Sequence[ToolSpec] | None = None,
        output_schema: Any = None,
        strict_output: bool = True,
        **options: Any,
    ) -> ModelResponse:
        return run_sync(
            self.agenerate(
                messages,
                tools=tools,
                output_schema=output_schema,
                strict_output=strict_output,
                **options,
            )
        )

    async def astream(
        self,
        messages: MessageLike | Sequence[MessageLike],
        *,
        tools: Sequence[ToolSpec] | None = None,
        output_schema: Any = None,
        strict_output: bool = True,
        **options: Any,
    ) -> AsyncIterator[StreamEvent]:
        request = self.build_request(
            messages, tools=tools, output_schema=output_schema, stream=True, **options
        )
        async for event in self._instrumented_stream(request, output_schema, strict_output):
            yield event

    def stream(
        self,
        messages: MessageLike | Sequence[MessageLike],
        *,
        tools: Sequence[ToolSpec] | None = None,
        output_schema: Any = None,
        strict_output: bool = True,
        **options: Any,
    ) -> Iterator[StreamEvent]:
        return iterate_sync(
            self.astream(
                messages,
                tools=tools,
                output_schema=output_schema,
                strict_output=strict_output,
                **options,
            )
        )

    # Instrumentation -------------------------------------------------------------
    async def _instrumented_generate(
        self, request: ModelRequest, output_type: Any, strict_output: bool
    ) -> ModelResponse:
        async with aensure_run("model") as run:
            run.add_dependency(self.dependency)
            with run.span(f"model:{self.name}", SpanKind.MODEL, provider=self.provider):
                request_fp = request.fingerprint()
                run.emit(EventType.MODEL_CALLED, _request_payload(request, request_fp))
                started = time.perf_counter()
                try:
                    substituted = await run.hooks.before_model_call(request)
                    if substituted is not None:
                        run.emit(
                            EventType.REPLAY_SUBSTITUTED,
                            {"kind": "model", "request_fingerprint": request_fp},
                        )
                        response = substituted
                    else:
                        response = await self._generate(request)
                except Exception as exc:
                    run.emit(
                        EventType.MODEL_RESPONSE,
                        {
                            "request_fingerprint": request_fp,
                            "provider": self.provider,
                            "model": self.name,
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": (time.perf_counter() - started) * 1000.0,
                        },
                    )
                    raise
                response = self._finalize(response, request, request_fp, started)
                run.record_usage(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cache_read_tokens=response.usage.cache_read_tokens,
                    cache_write_tokens=response.usage.cache_write_tokens,
                    reasoning_tokens=response.usage.reasoning_tokens,
                    model_calls=1,
                )
                run.record_cost("model", response.cost.total)
                run.emit(EventType.MODEL_RESPONSE, _response_payload(response))
                if request.output_schema is not None:
                    self._validate_structured(response, request, output_type, strict_output, run)
                return response

    async def _instrumented_stream(
        self, request: ModelRequest, output_type: Any, strict_output: bool
    ) -> AsyncIterator[StreamEvent]:
        async with aensure_run("model") as run:
            run.add_dependency(self.dependency)
            with run.span(f"model:{self.name}", SpanKind.MODEL, provider=self.provider):
                request_fp = request.fingerprint()
                run.emit(EventType.MODEL_CALLED, _request_payload(request, request_fp))
                started = time.perf_counter()
                substituted = await run.hooks.before_model_call(request)
                final: ModelResponse | None = None
                try:
                    if substituted is not None:
                        run.emit(
                            EventType.REPLAY_SUBSTITUTED,
                            {"kind": "model", "request_fingerprint": request_fp},
                        )
                        final = substituted
                        if final.text:
                            yield StreamEvent(type="text_delta", text=final.text)
                    else:
                        async for event in self._stream(request):
                            if event.type == "response":
                                final = event.response
                            else:
                                yield event
                except Exception as exc:
                    run.emit(
                        EventType.MODEL_RESPONSE,
                        {
                            "request_fingerprint": request_fp,
                            "provider": self.provider,
                            "model": self.name,
                            "error": f"{type(exc).__name__}: {exc}",
                            "latency_ms": (time.perf_counter() - started) * 1000.0,
                        },
                    )
                    raise
                if final is None:
                    raise ModelError(f"{self!r} stream ended without a final response")
                final = self._finalize(final, request, request_fp, started)
                run.record_usage(
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                    cache_read_tokens=final.usage.cache_read_tokens,
                    cache_write_tokens=final.usage.cache_write_tokens,
                    reasoning_tokens=final.usage.reasoning_tokens,
                    model_calls=1,
                )
                run.record_cost("model", final.cost.total)
                run.emit(EventType.MODEL_RESPONSE, _response_payload(final))
                if request.output_schema is not None:
                    self._validate_structured(final, request, output_type, strict_output, run)
                yield StreamEvent(type="response", response=final)

    def _finalize(
        self, response: ModelResponse, request: ModelRequest, request_fp: str, started: float
    ) -> ModelResponse:
        if response.latency_ms == 0.0:
            response.latency_ms = (time.perf_counter() - started) * 1000.0
        response.request_fingerprint = request_fp
        if response.cost.source == "unknown":
            response.cost = compute_cost(self.provider, self.name, response.usage)
        return response

    def _validate_structured(
        self,
        response: ModelResponse,
        request: ModelRequest,
        output_type: Any,
        strict_output: bool,
        run: Any,
    ) -> None:
        assert request.output_schema is not None
        schema_fp = fingerprint(request.output_schema)
        errors: list[str] = []
        repaired = False
        try:
            data, repaired = extract_json(response.text)
        except ValueError as exc:
            errors.append(str(exc))
            data = None
        if not errors:
            if isinstance(output_type, type) or _is_type_like(output_type):
                try:
                    response.structured = parse_as(output_type, data)
                except ValidationError as exc:
                    errors.extend(format_validation_error(exc))
            else:
                errors.extend(validate_json(request.output_schema, data))
                if not errors:
                    response.structured = data
        response.validation = OutputValidation(
            schema_fingerprint=schema_fp, valid=not errors, errors=errors, repaired=repaired
        )
        run.emit(
            EventType.OUTPUT_VALIDATED,
            {
                "response_id": response.id,
                "schema": request.output_schema,
                "valid": not errors,
                "errors": errors,
                "repaired": repaired,
                "output": data if not errors else response.text,
            },
        )
        if errors and strict_output:
            raise StructuredOutputError(errors, response)


def _is_type_like(value: Any) -> bool:
    """True for typing constructs such as ``list[int]`` that are not classes."""
    return not isinstance(value, dict) and value is not None and hasattr(value, "__module__")


def _resolve_schema(output_schema: Any) -> JSONObject | None:
    if output_schema is None:
        return None
    if isinstance(output_schema, dict):
        return dict(output_schema)
    return schema_for_type(output_schema)


def _request_payload(request: ModelRequest, request_fp: str) -> JSONObject:
    return {
        "request_fingerprint": request_fp,
        "provider": request.provider,
        "model": request.model,
        "messages": [m.model_dump(mode="json") for m in request.messages],
        "tools": [t.name for t in request.tools],
        "tool_specs": [t.model_dump(mode="json") for t in request.tools],
        "tool_choice": request.tool_choice,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "top_p": request.top_p,
        "stop": request.stop,
        "seed": request.seed,
        "output_schema": request.output_schema,
        "provider_options": request.provider_options,
        "stream": request.stream,
    }


def _response_payload(response: ModelResponse) -> JSONObject:
    return {
        "request_fingerprint": response.request_fingerprint,
        "response_id": response.id,
        "provider": response.provider,
        "model": response.model,
        "message": response.message.model_dump(mode="json"),
        "finish_reason": response.finish_reason,
        "usage": response.usage.model_dump(),
        "cost": response.cost.model_dump(),
        "latency_ms": response.latency_ms,
        "provider_response_id": response.provider_response_id,
    }
