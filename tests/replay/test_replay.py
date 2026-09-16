"""Deterministic replay, live replay and the replay hooks."""

from __future__ import annotations

import pytest

from rewyn.agents.agent import Agent
from rewyn.core.event import EventType
from rewyn.core.run import RunManifest, start_run
from rewyn.core.sync import run_sync
from rewyn.models.base import Message, Role, ToolCallPart
from rewyn.replay.deterministic import (
    ComponentMode,
    ReplayExhaustedError,
    ReplayHooks,
    ReplayMismatchError,
)
from rewyn.replay.live import replay_prompts, swapped_model
from rewyn.replay.recorder import RecordedRun, ReplayError
from rewyn.replay.replay import ReplayMode, areplay, replay
from rewyn.runtime.recorder import default_recorder
from rewyn.testing.fake_model import FakeModel
from rewyn.tools.tool import tool


@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def build_agent(script=None, *, name: str = "calc") -> Agent:
    model = FakeModel(
        script
        if script is not None
        else [FakeModel.tool_call("add", {"a": 2, "b": 3}), "The answer is 5."]
    )
    return Agent(model, tools=[add], name=name)


def record_run() -> tuple[str, str]:
    agent = build_agent()
    result = agent.run("what is 2+3?")
    default_recorder().flush()
    return result.run_id, result.output


def test_recorded_run_indexes_model_and_tool_calls():
    run_id, _ = record_run()
    recorded = RecordedRun.load(run_id)
    assert len(recorded.model_calls) == 2
    assert all(c.response is not None for c in recorded.model_calls)
    assert [c.request_fingerprint for c in recorded.model_calls] == [
        c.response.request_fingerprint for c in recorded.model_calls
    ]
    assert [c.name for c in recorded.tool_calls] == ["add"]
    assert recorded.tool_calls[0].arguments == {"a": 2, "b": 3}
    assert recorded.tool_calls[0].result == 5


def test_a_response_without_a_matching_fingerprint_attaches_in_order():
    from rewyn.core.event import EventType
    from rewyn.core.run import start_run

    with start_run("hand-rolled", record=False) as run:
        run.emit(EventType.MODEL_CALLED, {"request_fingerprint": "sha256:aaa", "messages": []})
        run.emit(
            EventType.MODEL_RESPONSE,
            {
                "request_fingerprint": "sha256:zzz",
                "message": {"role": "assistant", "content": "recovered"},
            },
        )
    recorded = RecordedRun.from_run(run)
    assert len(recorded.model_calls) == 1
    assert recorded.model_calls[0].text == "recovered"


def test_a_model_call_without_a_response_reads_as_empty_text():
    from rewyn.core.event import EventType
    from rewyn.core.run import start_run

    with start_run("truncated", record=False) as run:
        run.emit(EventType.MODEL_CALLED, {"request_fingerprint": "sha256:aaa", "messages": []})
    recorded = RecordedRun.from_run(run)
    assert recorded.model_calls[0].response is None
    assert recorded.model_calls[0].text == ""


def test_several_runs_load_at_once():
    from rewyn.replay.recorder import load_runs

    ids = [record_run()[0], record_run()[0]]
    assert [r.id for r in load_runs(ids)] == ids


def test_a_recording_without_tool_specs_falls_back_to_tool_names():
    from rewyn.replay.live import recorded_tool_specs

    run_id, _ = record_run()
    call = RecordedRun.load(run_id).model_calls[0]
    call.request.pop("tool_specs")
    assert [s.name for s in recorded_tool_specs(call)] == ["add"]


def test_recorded_run_can_be_read_from_memory():
    agent = build_agent()
    with start_run("inline", record=False) as run:
        agent.run("what is 2+3?")
    recorded = RecordedRun.from_run(run)
    assert len(recorded.model_calls) == 2
    assert recorded.tool_calls[0].name == "add"


def test_deterministic_replay_reproduces_the_recorded_output():
    run_id, output = record_run()
    result = replay(run_id)
    assert result.mode is ReplayMode.RECONSTRUCT
    assert result.output == output
    assert result.identical
    assert result.faithful
    assert result.mismatches == []


def test_reconstruction_re_emits_the_recorded_events():
    run_id, _ = record_run()
    result = replay(run_id)
    default_recorder().flush()
    original = RecordedRun.load(run_id)
    replayed = RecordedRun.load(result.run_id)
    assert [c.text for c in replayed.model_calls] == [c.text for c in original.model_calls]
    assert [c.result for c in replayed.tool_calls] == [c.result for c in original.tool_calls]
    substituted = replayed.events_of(EventType.REPLAY_SUBSTITUTED)
    assert len(substituted) == result.substitutions
    assert all(e.payload["source_run_id"] == run_id for e in substituted)


def test_re_execution_uses_the_recording_and_never_calls_the_model():
    run_id, output = record_run()
    empty = FakeModel([])
    agent = Agent(empty, tools=[add], name="calc")
    result = replay(run_id, target=agent)
    assert result.mode is ReplayMode.EXECUTE
    assert result.output == output
    assert result.identical
    assert result.faithful, result.mismatches
    assert empty.calls == 0, "a recorded replay must not call the provider"
    assert result.substitutions == 3


def test_re_execution_with_live_tools_runs_the_real_function():
    calls: list[tuple[int, int]] = []

    @tool
    def add_spy(a: int, b: int) -> int:
        """Add two numbers, recording the call."""
        calls.append((a, b))
        return a + b

    model = FakeModel([FakeModel.tool_call("add_spy", {"a": 2, "b": 3}), "done"])
    agent = Agent(model, tools=[add_spy], name="spy")
    run_id = agent.run("go").run_id
    default_recorder().flush()
    calls.clear()

    agent2 = Agent(FakeModel([]), tools=[add_spy], name="spy")
    result = replay(run_id, target=agent2, tools="live")
    assert calls == [(2, 3)]
    assert result.tool_mode == "live"


def test_prompt_replay_compares_a_new_model_against_the_recording():
    run_id, _ = record_run()
    other = FakeModel(["a different answer"], name="other", cycle=True)
    result = replay(run_id, model=other, context="original", tools="recorded")
    assert result.mode is ReplayMode.PROMPT
    assert len(result.prompts) == 2
    assert not result.identical
    assert all(p.changed for p in result.prompts)
    assert result.prompts[0].replay_model == "fake:other"


def test_prompt_replay_records_a_failing_experiment_instead_of_raising():
    run_id, _ = record_run()
    broken = FakeModel([RuntimeError("provider down")], cycle=True, name="broken")
    result = replay(run_id, model=broken)
    assert result.prompts[0].error is not None
    assert "provider down" in result.prompts[0].error


def test_live_replay_without_a_target_is_refused():
    run_id, _ = record_run()
    with pytest.raises(ReplayError, match="needs target="):
        replay(run_id, model="live")


def test_unknown_context_mode_is_refused():
    run_id, _ = record_run()
    with pytest.raises(ReplayError, match="context must be"):
        replay(run_id, context="nonsense")


async def test_hooks_match_by_fingerprint_then_by_position():
    run_id, _ = record_run()
    recorded = RecordedRun.load(run_id)
    hooks = ReplayHooks(recorded)

    first = recorded.model_calls[0]
    # Rebuild the exact recorded request so the fingerprint matches.
    model = FakeModel([])
    request = model.build_request(first.messages)
    request.provider = first.provider
    request.model = first.model
    request.tools = [
        __import__("rewyn.models.base", fromlist=["ToolSpec"]).ToolSpec.model_validate(s)
        for s in first.request["tool_specs"]
    ]
    substituted = await hooks.before_model_call(request)
    assert substituted is not None
    assert hooks.substitutions[0].matched_by == "fingerprint"
    assert hooks.mismatches == []


async def test_hooks_report_a_mismatch_when_the_prompt_changed():
    run_id, _ = record_run()
    hooks = ReplayHooks(RecordedRun.load(run_id))
    model = FakeModel([])
    response = await hooks.before_model_call(model.build_request("a completely new prompt"))
    assert response is not None
    assert hooks.diverged
    assert hooks.mismatches[0].reason == "content_changed"


async def test_strict_hooks_raise_on_divergence():
    run_id, _ = record_run()
    hooks = ReplayHooks(RecordedRun.load(run_id), strict=True)
    model = FakeModel([])
    with pytest.raises(ReplayMismatchError):
        await hooks.before_model_call(model.build_request("a completely new prompt"))


async def test_strict_hooks_raise_when_the_recording_is_exhausted():
    empty = RecordedRun(RunManifest(id="run_empty", name="empty"), [])
    hooks = ReplayHooks(empty, strict=True)
    with pytest.raises(ReplayExhaustedError):
        await hooks.before_model_call(FakeModel([]).build_request("anything"))


async def test_lenient_hooks_fall_through_when_the_recording_is_exhausted():
    empty = RecordedRun(RunManifest(id="run_empty", name="empty"), [])
    hooks = ReplayHooks(empty)
    assert await hooks.before_model_call(FakeModel([]).build_request("anything")) is None
    assert hooks.mismatches[0].reason == "not_recorded"


async def test_tool_hooks_match_on_arguments_then_fall_back_to_the_name():
    run_id, _ = record_run()
    hooks = ReplayHooks(RecordedRun.load(run_id))
    exact = await hooks.before_tool_call(ToolCallPart(name="add", arguments={"a": 2, "b": 3}))
    assert exact is not None
    assert exact.content == 5
    assert hooks.substitutions[0].matched_by == "arguments"
    assert hooks.mismatches == []


async def test_tool_hooks_report_a_mismatch_when_the_arguments_changed():
    run_id, _ = record_run()
    hooks = ReplayHooks(RecordedRun.load(run_id))
    result = await hooks.before_tool_call(ToolCallPart(name="add", arguments={"a": 9, "b": 9}))
    assert result is not None
    assert result.content == 5, "the recording answers, even though the request changed"
    assert hooks.mismatches[0].kind == "tool"
    assert hooks.mismatches[0].actual == '{"a":9,"b":9}'
    assert hooks.substitutions[0].matched_by == "position"


async def test_tool_hooks_fall_through_for_a_tool_that_was_never_recorded():
    run_id, _ = record_run()
    hooks = ReplayHooks(RecordedRun.load(run_id))
    assert await hooks.before_tool_call(ToolCallPart(name="unseen", arguments={})) is None
    assert hooks.mismatches[0].reason == "not_recorded"


async def test_live_tool_mode_never_substitutes():
    run_id, _ = record_run()
    hooks = ReplayHooks(RecordedRun.load(run_id), tools=ComponentMode.LIVE)
    assert await hooks.before_tool_call(ToolCallPart(name="add", arguments={})) is None


def test_prompt_replay_can_target_a_subset_of_calls():
    run_id, _ = record_run()
    entries = run_sync(
        replay_prompts(
            RecordedRun.load(run_id),
            FakeModel(["only this one"], cycle=True, name="other"),
            indices=[1],
        )
    )
    assert [e.index for e in entries] == [1]


def test_swapping_a_model_is_restored_afterwards():
    agent = build_agent()
    original = agent.model
    replacement = FakeModel([], name="temporary")
    with swapped_model(agent, replacement):
        assert agent.model is replacement
    assert agent.model is original
    with swapped_model(agent, None):
        assert agent.model is original


async def test_hooks_report_what_is_left_of_the_recording():
    run_id, _ = record_run()
    hooks = ReplayHooks(RecordedRun.load(run_id))
    assert hooks.remaining_model_calls == 2
    assert hooks.remaining_tool_calls == 1
    await hooks.before_tool_call(ToolCallPart(name="add", arguments={"a": 2, "b": 3}))
    assert hooks.remaining_tool_calls == 0
    report = hooks.report()
    assert report["model_mode"] == "recorded"
    assert report["substitutions"] == 1
    assert report["unused_model_calls"] == 2
    assert report["mismatches"] == []


async def test_no_substitution_hooks_let_everything_through():
    from rewyn.replay.deterministic import NoSubstitution

    hooks = NoSubstitution()
    assert await hooks.before_model_call(FakeModel([]).build_request("x")) is None
    assert await hooks.before_tool_call(ToolCallPart(name="add", arguments={})) is None


def test_a_denied_tool_call_is_marked_in_the_recording():
    from rewyn.core.event import EventType
    from rewyn.core.run import start_run

    with start_run("denied", record=False) as run:
        run.emit(EventType.TOOL_CALLED, {"tool_call_id": "call_1", "name": "wire"})
        run.emit(EventType.TOOL_DENIED, {"tool_call_id": "call_1", "name": "wire"})
        run.emit(
            EventType.TOOL_RETURNED,
            {"tool_call_id": "call_1", "name": "wire", "result": "denied", "is_error": True},
        )
        run.emit(EventType.TOOL_RETURNED, {"tool_call_id": "call_unknown", "name": "ghost"})
    recorded = RecordedRun.from_run(run)
    assert len(recorded.tool_calls) == 1
    assert recorded.tool_calls[0].denied
    assert recorded.tool_calls[0].is_error
    assert recorded.tool_calls[0].key == "wire:{}"


def test_a_response_with_no_message_yields_no_recorded_response():
    from rewyn.replay.recorder import response_from_payload

    assert response_from_payload({"provider": "fake"}) is None


def test_an_unmatched_response_event_is_ignored():
    from rewyn.core.event import EventType
    from rewyn.core.run import start_run

    with start_run("stray", record=False) as run:
        run.emit(EventType.MODEL_RESPONSE, {"message": {"role": "assistant", "content": "x"}})
    assert RecordedRun.from_run(run).model_calls == []


def test_recorded_generation_options_are_carried_back_into_a_prompt_replay():
    from rewyn.replay.live import request_options

    agent = Agent(
        FakeModel(["done"]), tools=[add], name="tuned", model_options={"temperature": 0.2}
    )
    run_id = agent.run("go").run_id
    default_recorder().flush()
    options = request_options(RecordedRun.load(run_id).model_calls[0])
    assert options["temperature"] == 0.2


def test_prompt_replay_skips_calls_that_never_got_a_response():
    from rewyn.core.event import EventType
    from rewyn.core.run import start_run

    with start_run("truncated", record=False) as run:
        run.emit(EventType.MODEL_CALLED, {"request_fingerprint": "sha256:a", "messages": []})
    entries = run_sync(replay_prompts(RecordedRun.from_run(run), FakeModel([], name="other")))
    assert entries == []


async def test_live_model_mode_never_substitutes():
    run_id, _ = record_run()
    hooks = ReplayHooks(RecordedRun.load(run_id), model=ComponentMode.LIVE)
    model = FakeModel([])
    assert await hooks.before_model_call(model.build_request("anything")) is None


def test_a_matching_output_from_a_diverging_transcript_is_not_faithful():
    run_id, output = record_run()
    # A differently named stub model changes every request fingerprint, so the
    # recording is matched by position instead of by content.
    agent = Agent(FakeModel([], name="other-model"), tools=[add], name="calc")
    result = replay(run_id, target=agent)
    assert result.output == output
    assert result.identical
    assert not result.faithful
    assert all(m.reason == "content_changed" for m in result.mismatches)


async def test_areplay_accepts_a_loaded_recording():
    run_id, output = record_run()
    result = await areplay(RecordedRun.load(run_id))
    assert result.output == output


def test_replay_of_a_plain_callable_target():
    run_id, _ = record_run()
    seen: list[object] = []

    async def target(payload):
        seen.append(payload)
        return "callable output"

    result = replay(run_id, target=target, context="live")
    assert result.output == "callable output"
    assert seen == ["what is 2+3?"]


def test_a_model_given_as_a_provider_string_drives_a_prompt_replay():
    run_id, _ = record_run()
    result = replay(run_id, model="fake:other", context="original", tools="recorded")
    assert result.mode is ReplayMode.PROMPT
    assert result.prompts[0].replay_model == "fake:other"
    assert result.summary()["mode"] == "prompt"


def test_a_target_that_is_neither_runnable_nor_callable_is_refused():
    run_id, _ = record_run()
    with pytest.raises(ReplayError, match="neither runnable nor callable"):
        replay(run_id, target=object(), context="live")


def test_context_reassembly_is_suppressed_when_the_recorded_prompt_is_replayed():
    from rewyn.context.manager import Context
    from rewyn.context.source import ContextItem, ContextKind

    run_id, output = record_run()
    context = Context(name="docs")
    context.add(ContextItem(kind=ContextKind.KNOWLEDGE, content="Unrelated background."))
    agent = Agent(FakeModel([]), tools=[add], name="calc", context=context)

    result = replay(run_id, target=agent, context="original")
    assert result.output == output
    assert result.faithful, result.mismatches
    assert agent.context is context, "the agent's context must be restored afterwards"


def test_a_strict_replay_propagates_the_divergence_instead_of_capturing_it():
    run_id, _ = record_run()
    agent = Agent(FakeModel([], name="other-model"), tools=[add], name="calc")
    with pytest.raises(ReplayMismatchError):
        replay(run_id, target=agent, strict=True)


def test_replay_captures_a_target_failure_without_raising():
    run_id, _ = record_run()

    def exploding(_payload):
        raise ValueError("boom")

    result = replay(run_id, target=exploding, context="live")
    assert result.error is not None
    assert "boom" in result.error
    assert not result.identical


async def test_prompt_replay_can_change_the_temperature_and_the_instructions():
    """The two things developers vary most, without rebuilding the transcript.

    UI spec §21 offers both as replay controls; replaying a recorded run with
    either changed must leave everything else exactly as recorded.
    """
    from rewyn.replay.live import with_system

    model = FakeModel(["first answer"])
    async with start_run("instructed") as original:
        await model.agenerate(
            [Message.system("Be terse."), Message.user("Explain replay.")],
            temperature=0.1,
        )
    recorded = RecordedRun.from_run(original)

    replacement = FakeModel(["second answer"])
    result = await areplay(
        recorded,
        model=replacement,
        temperature=0.9,
        system="Be exhaustive.",
    )

    assert result.mode is ReplayMode.PROMPT
    assert result.prompts[0].replay_text == "second answer"
    request = replacement.requests[0]
    assert request.temperature == 0.9
    assert request.messages[0].text == "Be exhaustive."
    assert request.messages[-1].text == "Explain replay."
    assert [m.role for m in request.messages].count(Role.SYSTEM) == 1

    unchanged = with_system([Message.user("hello")], "system")
    assert [m.role for m in unchanged] == [Role.SYSTEM, Role.USER]
