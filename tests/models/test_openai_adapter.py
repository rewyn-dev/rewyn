from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from rewyn.core.run import start_run
from rewyn.models.base import (
    ImagePart,
    Message,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from rewyn.models.openai import OpenAIModel, _to_openai_messages


class _Completions:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.result


def _client(result: Any) -> Any:
    completions = _Completions(result)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _completion(
    content: str | None, tool_calls: list[Any] | None = None, finish: str = "stop"
) -> Any:
    return SimpleNamespace(
        id="chatcmpl-1",
        model="gpt-5-2026",
        choices=[
            SimpleNamespace(
                finish_reason=finish,
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=7,
            prompt_tokens_details=SimpleNamespace(cached_tokens=4),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
        ),
    )


def test_request_translation() -> None:
    client = _client(_completion("Paris"))
    model = OpenAIModel("gpt-5", client=client)
    messages = [
        Message.system("Be brief"),
        Message.user([TextPart(text="what is this?"), ImagePart(url="http://x/y.png")]),
        Message.assistant(
            "calling", tool_calls=[ToolCallPart(id="c1", name="f", arguments={"a": 1})]
        ),
        Message.tool([ToolResultPart(tool_call_id="c1", content={"r": 2})]),
    ]
    with start_run("t", sinks=[]):
        response = model.generate(
            messages,
            tools=[ToolSpec(name="f", description="d", strict=True)],
            tool_choice="f",
            temperature=0.3,
            max_tokens=99,
            output_schema={"type": "object"},
            strict_output=False,
            provider_options={"reasoning_effort": "low"},
        )
    kwargs = client.chat.completions.kwargs
    assert kwargs["model"] == "gpt-5"
    assert kwargs["messages"][0] == {"role": "system", "content": "Be brief"}
    assert kwargs["messages"][1]["content"][1]["image_url"] == {"url": "http://x/y.png"}
    assert kwargs["messages"][2]["tool_calls"][0]["function"] == {
        "name": "f",
        "arguments": '{"a": 1}',
    }
    assert kwargs["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": '{"r": 2}'}
    assert kwargs["tools"][0]["function"]["strict"] is True
    assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "f"}}
    assert kwargs["max_completion_tokens"] == 99
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["reasoning_effort"] == "low"
    assert response.text == "Paris"
    assert response.model == "gpt-5-2026"
    assert response.provider_response_id == "chatcmpl-1"
    assert response.usage.input_tokens == 12
    assert response.usage.cache_read_tokens == 4
    assert response.usage.reasoning_tokens == 2


def test_tool_call_response_parsing() -> None:
    call = SimpleNamespace(id="c9", function=SimpleNamespace(name="get", arguments='{"id": "x"}'))
    model = OpenAIModel("gpt-5", client=_client(_completion(None, [call], finish="tool_calls")))
    response = model.generate("q")
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "c9"
    assert response.tool_calls[0].arguments == {"id": "x"}


def test_base64_image_and_bad_json_arguments() -> None:
    msg = Message.user([ImagePart(data="AAAA", media_type="image/jpeg")])
    out = _to_openai_messages(msg)
    assert out[0]["content"][0]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"
    call = SimpleNamespace(id="c", function=SimpleNamespace(name="f", arguments="{oops"))
    model = OpenAIModel("gpt-5", client=_client(_completion(None, [call], finish="tool_calls")))
    assert model.generate("q").tool_calls[0].arguments == {"_raw": "{oops"}


async def test_streaming_accumulates_text_and_tool_calls() -> None:
    def chunk(delta: Any, finish: str | None = None, usage: Any = None) -> Any:
        return SimpleNamespace(
            id="chatcmpl-s",
            usage=usage,
            choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
        )

    chunks = [
        chunk(SimpleNamespace(content="Hel", tool_calls=None)),
        chunk(SimpleNamespace(content="lo", tool_calls=None)),
        chunk(
            SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        index=0, id="c1", function=SimpleNamespace(name="f", arguments='{"a"')
                    )
                ],
            )
        ),
        chunk(
            SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        index=0, id=None, function=SimpleNamespace(name=None, arguments=": 1}")
                    )
                ],
            ),
            finish="tool_calls",
        ),
        SimpleNamespace(
            id="chatcmpl-s",
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=3,
                completion_tokens=4,
                prompt_tokens_details=None,
                completion_tokens_details=None,
            ),
        ),
    ]

    class _Stream:
        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            if not chunks:
                raise StopAsyncIteration
            return chunks.pop(0)

    model = OpenAIModel("gpt-5", client=_client(_Stream()))
    events = [e async for e in model.astream("q")]
    assert model.client.chat.completions.kwargs["stream"] is True
    assert [e.type for e in events] == [
        "text_delta",
        "text_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_delta",
        "tool_call_end",
        "response",
    ]
    final = events[-1].response
    assert final is not None
    assert final.text == "Hello"
    assert final.tool_calls[0].arguments == {"a": 1}
    assert final.finish_reason == "tool_calls"
    assert final.usage.output_tokens == 4
    assert json.loads(json.dumps(final.to_record()))["message"]["role"] == "assistant"
