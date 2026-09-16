from __future__ import annotations

import pytest
from pydantic import BaseModel

from rewyn.core.event import EventType, ListSink
from rewyn.core.run import start_run
from rewyn.models.base import (
    Message,
    ModelResponse,
    Role,
    StructuredOutputError,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    coerce_messages,
    extract_json,
)
from rewyn.models.pricing import Price, compute_cost, register_price
from rewyn.models.registry import resolve_model
from rewyn.testing import FakeModel


def test_message_constructors_and_accessors() -> None:
    call = ToolCallPart(name="lookup", arguments={"id": 1})
    assistant = Message.assistant("hi", tool_calls=[call])
    assert assistant.text == "hi"
    assert assistant.tool_calls == [call]
    tool = Message.tool([ToolResultPart(tool_call_id=call.id, content={"ok": True})])
    assert tool.tool_results[0].content_text == '{"ok": true}'
    assert Message.model_validate({"role": "user", "content": "plain"}).content == [
        TextPart(text="plain")
    ]
    assert [m.role for m in coerce_messages("q")] == [Role.USER]
    assert coerce_messages([{"role": "system", "content": "s"}, assistant])[1] is assistant


def test_generate_emits_events_and_accounts_usage_and_cost() -> None:
    sink = ListSink()
    model = FakeModel(["hello world"])
    with start_run("t", sinks=[sink]) as run:
        response = model.generate("hi there", temperature=0.2)
    assert response.text == "hello world"
    assert response.finish_reason == "stop"
    assert response.request_fingerprint
    called = sink.of_type(EventType.MODEL_CALLED)[0]
    assert called.payload["model"] == "fake-1"
    assert called.payload["temperature"] == 0.2
    assert called.payload["messages"][0]["content"][0]["text"] == "hi there"
    returned = sink.of_type(EventType.MODEL_RESPONSE)[0]
    assert returned.payload["request_fingerprint"] == called.payload["request_fingerprint"]
    assert returned.payload["usage"]["output_tokens"] == response.usage.output_tokens
    assert run.manifest.usage.model_calls == 1
    assert run.manifest.cost.model == response.cost.total > 0
    assert response.cost.source == "table"
    assert [d.name for d in run.manifest.dependencies] == ["fake:fake-1"]
    model_span = next(s for s in run.spans if s.name == "model:fake-1")
    assert called.span_id == model_span.id


def test_generate_outside_a_run_creates_an_implicit_run() -> None:
    model = FakeModel(["x"])
    response = model.generate("q")
    assert response.text == "x"
    assert model.requests[0].messages[0].text == "q"


async def test_agenerate_with_tools_and_tool_choice() -> None:
    model = FakeModel([FakeModel.tool_call("search", {"q": "ev"})])
    spec = ToolSpec(name="search", parameters={"type": "object", "properties": {}})
    response = await model.agenerate("find", tools=[spec], tool_choice="search")
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].arguments == {"q": "ev"}
    assert model.requests[0].tool_choice == "search"
    assert model.requests[0].has_tool("search")


def test_structured_output_validation_success_and_failure() -> None:
    class Answer(BaseModel):
        value: int

    sink = ListSink()
    model = FakeModel(['```json\n{"value": 4}\n```', "not json at all"])
    with start_run("t", sinks=[sink]):
        ok = model.generate("q", output_schema=Answer)
        assert ok.structured == Answer(value=4)
        assert ok.validation is not None
        assert ok.validation.valid
        assert ok.validation.repaired
        with pytest.raises(StructuredOutputError) as info:
            model.generate("q", output_schema=Answer)
        assert info.value.response.validation is not None
        assert not info.value.response.validation.valid
        lenient = FakeModel(["{}"]).generate("q", output_schema=Answer, strict_output=False)
        assert lenient.structured is None
        assert lenient.validation is not None
        assert lenient.validation.errors == ["value: Field required"]
    validated = sink.of_type(EventType.OUTPUT_VALIDATED)
    assert [e.payload["valid"] for e in validated] == [True, False, False]
    assert validated[0].payload["schema"]["properties"]["value"] == {"type": "integer"}


def test_structured_output_with_raw_json_schema() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    response = FakeModel(['{"n": 1}']).generate("q", output_schema=schema)
    assert response.structured == {"n": 1}
    assert response.validation is not None
    assert response.validation.valid


def test_extract_json_variants() -> None:
    assert extract_json('{"a": 1}') == ({"a": 1}, False)
    assert extract_json('```json\n{"a": 1}\n```') == ({"a": 1}, True)
    assert extract_json('Sure! {"a": [1, 2]} done') == ({"a": [1, 2]}, True)
    with pytest.raises(ValueError, match="no JSON"):
        extract_json("nothing here")


def test_model_error_is_recorded_and_raised() -> None:
    sink = ListSink()
    model = FakeModel([RuntimeError("provider down")])  # type: ignore[list-item]
    with start_run("t", sinks=[sink]), pytest.raises(RuntimeError, match="provider down"):
        model.generate("q")
    payload = sink.of_type(EventType.MODEL_RESPONSE)[0].payload
    assert payload["error"] == "RuntimeError: provider down"


async def test_astream_yields_deltas_then_final_response() -> None:
    sink = ListSink()
    model = FakeModel(["abcdefghijk"], chunk_size=4)
    async with start_run("t", sinks=[sink]):
        events = [e async for e in model.astream("q")]
    assert [e.text for e in events[:-1]] == ["abcd", "efgh", "ijk"]
    final = events[-1]
    assert final.type == "response"
    assert final.response is not None
    assert final.response.text == "abcdefghijk"
    assert len(sink.of_type(EventType.MODEL_RESPONSE)) == 1
    assert sink.of_type(EventType.MODEL_CALLED)[0].payload["stream"] is True


def test_sync_stream_bridge() -> None:
    model = FakeModel(["stream me"], chunk_size=6)
    texts = [e.text for e in model.stream("q") if e.type == "text_delta"]
    assert texts == ["stream", " me"]


def test_stream_with_scripted_tool_calls() -> None:
    model = FakeModel([FakeModel.tool_calls(("a", {}), ("b", {"x": 1}))])
    kinds = [e.type for e in model.stream("q")]
    assert kinds == [
        "tool_call_start",
        "tool_call_end",
        "tool_call_start",
        "tool_call_end",
        "response",
    ]


def test_defaults_and_provider_options_merge() -> None:
    model = FakeModel(["x"], name="m")
    model.defaults.update({"temperature": 0.1, "provider_options": {"a": 1}})
    model.generate("q", provider_options={"b": 2})
    request = model.requests[0]
    assert request.temperature == 0.1
    assert request.provider_options == {"a": 1, "b": 2}


def test_request_fingerprint_ignores_metadata_and_stream() -> None:
    model = FakeModel()
    base = model.build_request("q")
    assert base.fingerprint() == model.build_request("q", stream=True).fingerprint()
    assert base.fingerprint() == model.build_request("q", metadata={"k": 1}).fingerprint()
    assert base.fingerprint() != model.build_request("q", temperature=0.5).fingerprint()


def test_pricing_lookup_prefers_longest_prefix_and_reports_unknown() -> None:
    register_price("acme", "m", Price(1.0, 1.0))
    register_price("acme", "m-large", Price(10.0, 20.0))
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000, cache_read_tokens=500_000)
    cost = compute_cost("acme", "m-large-2", usage)
    assert cost.source == "table"
    assert cost.input == pytest.approx(5.0 + 0.5)  # half cached at 10% rate
    assert cost.output == pytest.approx(10.0)
    assert compute_cost("acme", "other", usage).source == "unknown"


def test_registry_resolves_strings_and_instances() -> None:
    model = resolve_model("fake:demo")
    assert isinstance(model, FakeModel)
    assert model.name == "demo"
    assert resolve_model(model) is model
    with pytest.raises(Exception, match="provider:model"):
        resolve_model("no-colon")
    with pytest.raises(Exception, match="unknown model provider"):
        resolve_model("nope:x")


def test_fake_model_cycle_and_callable_scripts() -> None:
    model = FakeModel([lambda req: f"echo:{req.messages[-1].text}"], cycle=True)
    assert model.generate("a").text == "echo:a"
    assert model.generate("b").text == "echo:b"
    scripted = ModelResponse(provider="fake", model="x", message=Message.assistant("pre"))
    assert FakeModel([scripted]).generate("q") is scripted
