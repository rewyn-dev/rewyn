from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

from google.genai import types

from rewyn.core.run import start_run
from rewyn.models.base import Message, ToolCallPart, ToolResultPart, ToolSpec
from rewyn.models.google import GeminiModel


class _Models:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    async def generate_content(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.result

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.result


def _client(result: Any) -> Any:
    return SimpleNamespace(aio=SimpleNamespace(models=_Models(result)))


def _response(parts: list[Any], finish: Any = types.FinishReason.STOP) -> Any:
    return SimpleNamespace(
        response_id="r1",
        model_version="gemini-2.5-pro-001",
        candidates=[SimpleNamespace(finish_reason=finish, content=SimpleNamespace(parts=parts))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
            thoughts_token_count=3,
            cached_content_token_count=2,
        ),
    )


def test_request_translation() -> None:
    client = _client(_response([SimpleNamespace(text="Hola", thought=False, function_call=None)]))
    model = GeminiModel("gemini-2.5-pro", client=client)
    messages = [
        Message.system("Be Spanish"),
        Message.user("hi"),
        Message.assistant(tool_calls=[ToolCallPart(id="fc1", name="f", arguments={"a": 1})]),
        Message.tool([ToolResultPart(tool_call_id="fc1", name="f", content="ok")]),
    ]
    with start_run("t", sinks=[]):
        response = model.generate(
            messages,
            tools=[ToolSpec(name="f", description="d")],
            tool_choice="f",
            temperature=0.5,
            max_tokens=10,
            output_schema={"type": "object"},
            strict_output=False,
            provider_options={"candidate_count": 1},
        )
    kwargs = client.aio.models.kwargs
    config = kwargs["config"]
    assert config.system_instruction == "Be Spanish"
    assert config.temperature == 0.5
    assert config.max_output_tokens == 10
    assert config.candidate_count == 1
    assert config.response_mime_type == "application/json"
    assert config.tools[0].function_declarations[0].name == "f"
    assert config.tool_config.function_calling_config.allowed_function_names == ["f"]
    contents = kwargs["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].function_call.name == "f"
    assert contents[2].parts[0].function_response.response == {"result": "ok"}
    assert response.text == "Hola"
    assert response.model == "gemini-2.5-pro-001"
    assert response.usage.output_tokens == 8
    assert response.usage.reasoning_tokens == 3
    assert response.usage.cache_read_tokens == 2


def test_function_call_and_thought_parsing() -> None:
    parts = [
        SimpleNamespace(text="why", thought=True, function_call=None, thought_signature=b"sig"),
        SimpleNamespace(
            text=None,
            thought=False,
            function_call=SimpleNamespace(id=None, name="get", args={"x": 1}),
        ),
    ]
    model = GeminiModel("gemini-2.5-pro", client=_client(_response(parts)))
    response = model.generate("q")
    assert response.finish_reason == "tool_calls"
    assert response.message.reasoning[0].provider_data == {
        "thought_signature": base64.b64encode(b"sig").decode()
    }
    assert response.tool_calls[0].id == "call_0"
    assert response.tool_calls[0].arguments == {"x": 1}


def test_safety_finish_maps_to_content_filter() -> None:
    model = GeminiModel(
        "gemini-2.5-pro", client=_client(_response([], finish=types.FinishReason.SAFETY))
    )
    assert model.generate("q").finish_reason == "content_filter"


async def test_streaming_chunks() -> None:
    chunks = [
        _response([SimpleNamespace(text="He", thought=False, function_call=None)], finish=None),
        _response([SimpleNamespace(text="y", thought=False, function_call=None)]),
    ]

    class _Stream:
        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            if not chunks:
                raise StopAsyncIteration
            return chunks.pop(0)

    model = GeminiModel("gemini-2.5-pro", client=_client(_Stream()))
    events = [e async for e in model.astream("q")]
    assert [e.text for e in events[:-1]] == ["He", "y"]
    assert events[-1].response is not None
    assert events[-1].response.text == "Hey"
    assert events[-1].response.finish_reason == "stop"
