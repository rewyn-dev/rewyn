from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from rewyn.core.run import start_run
from rewyn.models.anthropic import AnthropicModel, _to_anthropic_message
from rewyn.models.base import (
    ImagePart,
    Message,
    ReasoningPart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from rewyn.security.redaction import default_redactor


class _Messages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.result


def _client(result: Any) -> Any:
    return SimpleNamespace(messages=_Messages(result))


def _message(blocks: list[Any], stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        id="msg_1",
        model="claude-opus-5",
        content=blocks,
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=20,
            cache_creation_input_tokens=10,
        ),
    )


def test_request_translation_and_cost() -> None:
    client = _client(_message([SimpleNamespace(type="text", text="Bonjour")]))
    model = AnthropicModel(
        "claude-opus-5", client=client, api_key="sk-ant-api03-secretvalue1234567890"
    )
    messages = [
        Message.system("Be French"),
        Message.user("hi"),
        Message.assistant(
            "thinking done",
            tool_calls=[ToolCallPart(id="tu1", name="f", arguments={"a": 1})],
        ),
        Message.tool([ToolResultPart(tool_call_id="tu1", content="42", is_error=True)]),
        Message.user([TextPart(text="look"), ImagePart(data="QUJD", media_type="image/png")]),
    ]
    with start_run("t", sinks=[]) as run:
        response = model.generate(
            messages,
            tools=[ToolSpec(name="f", description="d")],
            tool_choice="required",
            stop=["END"],
            output_schema={"type": "object"},
            strict_output=False,
            provider_options={"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}},
        )
    kwargs = client.messages.kwargs
    assert kwargs["system"] == "Be French"
    assert kwargs["max_tokens"] == 16_000
    assert kwargs["messages"][0] == {"role": "user", "content": "hi"}
    assert kwargs["messages"][1]["content"][1] == {
        "type": "tool_use",
        "id": "tu1",
        "name": "f",
        "input": {"a": 1},
    }
    assert kwargs["messages"][2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tu1", "content": "42", "is_error": True}
        ],
    }
    assert kwargs["messages"][3]["content"][1]["source"]["media_type"] == "image/png"
    assert kwargs["tools"] == [
        {"name": "f", "description": "d", "input_schema": {"type": "object", "properties": {}}}
    ]
    assert kwargs["tool_choice"] == {"type": "any"}
    assert kwargs["stop_sequences"] == ["END"]
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {
        "format": {"type": "json_schema", "schema": {"type": "object"}},
        "effort": "low",
    }
    assert response.text == "Bonjour"
    assert response.usage.cache_read_tokens == 20
    assert response.usage.cache_write_tokens == 10
    assert response.cost.source == "table"
    # 70 uncached * 5 + 20 * 0.5 + 10 * 6.25 = 350 + 10 + 62.5 per million; output 50 * 25
    assert round(response.cost.input * 1_000_000, 3) == 422.5
    assert round(response.cost.output * 1_000_000, 3) == 1250.0
    assert run.manifest.cost.model == response.cost.total
    assert (
        default_redactor().redact_text("key sk-ant-api03-secretvalue1234567890") == "key [REDACTED]"
    )


def test_tool_use_and_thinking_blocks_round_trip() -> None:
    blocks = [
        SimpleNamespace(type="thinking", thinking="hmm", signature="sig"),
        SimpleNamespace(type="redacted_thinking", data="opaque"),
        SimpleNamespace(type="tool_use", id="tu2", name="get", input={"k": "v"}),
    ]
    model = AnthropicModel("claude-opus-5", client=_client(_message(blocks, stop="tool_use")))
    response = model.generate("q")
    assert response.finish_reason == "tool_calls"
    assert response.message.reasoning[0] == ReasoningPart(text="hmm", signature="sig")
    assert response.message.reasoning[1].provider_data == {
        "type": "redacted_thinking",
        "data": "opaque",
    }
    assert response.tool_calls[0].arguments == {"k": "v"}
    back = _to_anthropic_message(response.message)
    assert back["content"][0] == {"type": "thinking", "thinking": "hmm", "signature": "sig"}
    assert back["content"][1] == {"type": "redacted_thinking", "data": "opaque"}
    assert back["content"][2]["type"] == "tool_use"


def test_refusal_maps_to_content_filter() -> None:
    model = AnthropicModel("claude-opus-5", client=_client(_message([], stop="refusal")))
    assert model.generate("q").finish_reason == "content_filter"


async def test_streaming_events() -> None:
    events = [
        SimpleNamespace(
            type="content_block_start", index=0, content_block=SimpleNamespace(type="text")
        ),
        SimpleNamespace(
            type="content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text="Hi")
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="thinking_delta", thinking="t"),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="tu3", name="f"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"a":1}'),
        ),
        SimpleNamespace(type="content_block_stop", index=1),
    ]
    final = _message(
        [
            SimpleNamespace(type="text", text="Hi"),
            SimpleNamespace(type="tool_use", id="tu3", name="f", input={"a": 1}),
        ],
        stop="tool_use",
    )

    class _Stream:
        async def __aenter__(self) -> _Stream:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            if not events:
                raise StopAsyncIteration
            return events.pop(0)

        async def get_final_message(self) -> Any:
            return final

    class _StreamMessages:
        kwargs: dict[str, Any] = {}

        def stream(self, **kwargs: Any) -> _Stream:
            self.kwargs = kwargs
            return _Stream()

    client = SimpleNamespace(messages=_StreamMessages())
    model = AnthropicModel("claude-opus-5", client=client)
    out = [e async for e in model.astream("q")]
    assert client.messages.kwargs["max_tokens"] == 64_000
    assert [e.type for e in out] == [
        "text_delta",
        "reasoning_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
        "response",
    ]
    assert out[3].arguments_delta == '{"a":1}'
    assert out[-1].response is not None
    assert out[-1].response.tool_calls[0].id == "tu3"
