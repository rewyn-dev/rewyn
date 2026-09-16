"""The runs index: every filter UI §7 lists, and the paging UI §50 requires."""

from __future__ import annotations

from datetime import timedelta

from rewyn.core.types import utcnow
from rewyn.storage.local import LocalStore
from rewyn.ui.index import RunIndex, RunQuery
from tests.ui.conftest import record_demo_run


def test_indexing_is_incremental(store: LocalStore, run_id: str):
    index = RunIndex(store)
    assert index.refresh() == 1
    assert index.refresh() == 0, "an unchanged run is not re-indexed"
    assert index.get(run_id) is not None


def test_a_deleted_run_leaves_the_index(store: LocalStore, run_id: str):
    index = RunIndex(store)
    index.refresh()
    store.delete_run(run_id)
    index.refresh()
    assert index.get(run_id) is None


async def test_every_filter_narrows_the_result(store: LocalStore, run_id: str):
    """UI §7: agent, model, status, user, environment, tool, MCP, skill, tag."""
    other = await record_demo_run(user="sam")
    index = RunIndex(store)
    index.refresh()
    assert {r.id for r in index.search(RunQuery()).runs} == {run_id, other}

    assert len(index.search(RunQuery(user="raj")).runs) == 1
    assert len(index.search(RunQuery(user="nobody")).runs) == 0
    assert len(index.search(RunQuery(agent="acme-credit")).runs) == 2
    assert len(index.search(RunQuery(status="succeeded")).runs) == 2
    assert len(index.search(RunQuery(status="failed")).runs) == 0
    assert len(index.search(RunQuery(environment="production")).runs) == 2
    assert len(index.search(RunQuery(environment="staging")).runs) == 0
    assert len(index.search(RunQuery(skill="credit-policy")).runs) == 2
    assert len(index.search(RunQuery(mcp="salesforce")).runs) == 2
    assert len(index.search(RunQuery(tool="set_credit_limit")).runs) == 2
    assert len(index.search(RunQuery(tool="no_such_tool")).runs) == 0
    assert len(index.search(RunQuery(error=True)).runs) == 0
    assert len(index.search(RunQuery(session_id="sess-1")).runs) == 2


def test_numeric_and_date_filters(store: LocalStore, run_id: str):
    """UI §7: date, cost and latency are ranges, not equality."""
    index = RunIndex(store)
    index.refresh()
    summary = index.get(run_id)
    assert summary is not None

    assert index.search(RunQuery(min_cost=summary.cost)).runs
    assert not index.search(RunQuery(min_cost=summary.cost * 10)).runs
    assert index.search(RunQuery(max_latency_ms=summary.duration_ms + 1)).runs
    assert not index.search(RunQuery(min_latency_ms=summary.duration_ms * 100 + 1000)).runs
    assert index.search(RunQuery(since=utcnow() - timedelta(hours=1))).runs
    assert not index.search(RunQuery(since=utcnow() + timedelta(hours=1))).runs


async def test_search_text_matches_id_agent_and_dependencies(store: LocalStore, run_id: str):
    index = RunIndex(store)
    index.refresh()
    assert index.search(RunQuery(q=run_id[:12])).runs
    assert index.search(RunQuery(q="salesforce")).runs
    assert not index.search(RunQuery(q="zzz-not-here")).runs


async def test_paging_is_keyset_and_stable(store: LocalStore, run_id: str):
    await record_demo_run()
    await record_demo_run()
    index = RunIndex(store)
    index.refresh()

    first = index.search(RunQuery(limit=2))
    assert len(first.runs) == 2
    assert first.total == 3
    assert first.next_cursor

    second = index.search(RunQuery(limit=2, cursor=first.next_cursor))
    assert len(second.runs) == 1
    assert second.next_cursor is None
    assert {r.id for r in first.runs}.isdisjoint({r.id for r in second.runs})


def test_facets_carry_the_filter_vocabulary_with_counts(store: LocalStore, run_id: str):
    index = RunIndex(store)
    index.refresh()
    facets = index.facets()
    assert [f.value for f in facets.agent] == ["acme-credit"]
    assert facets.agent[0].count == 1
    assert "credit-policy" in [f.value for f in facets.skill]
    assert "salesforce" in [f.value for f in facets.mcp]
    assert "production" in [f.value for f in facets.environment]


def test_environments_and_sessions_are_derived_from_runs(store: LocalStore, run_id: str):
    """UI §4 navigation and UI §42."""
    index = RunIndex(store)
    index.refresh()
    environments = index.environments()
    assert [e.name for e in environments] == ["production"]
    assert environments[0].runs == 1

    sessions = index.sessions()
    assert [s.id for s in sessions] == ["sess-1"]
    assert sessions[0].agents == ["acme-credit"]
    assert sessions[0].failures == 0


def test_stats_answer_the_overview_questions(store: LocalStore, run_id: str):
    index = RunIndex(store)
    index.refresh()
    stats = index.stats()
    assert stats["runs"] == 1
    assert stats["success_rate"] == 100.0
    assert stats["avg_latency_ms"] > 0
    assert stats["total_cost"] > 0


async def test_a_score_recorded_by_an_evaluation_lands_on_the_run_it_judged(
    store: LocalStore, run_id: str
):
    """UI §7: the evaluation-score filter, over scores recorded elsewhere.

    An evaluator records its verdict on the evaluating run, not on the run
    under test, so the index has to carry it across.
    """
    from rewyn.core.run import start_run
    from rewyn.evaluation.evaluator import Subject, evaluator
    from rewyn.runtime.recorder import default_recorder

    @evaluator
    def task_success(subject: Subject) -> float:
        return 0.75

    with start_run("evaluation"):
        await task_success.ascore(Subject(output="done", run_id=run_id))
    default_recorder().flush()

    index = RunIndex(store)
    index.refresh()

    judged = index.get(run_id)
    assert judged is not None
    assert judged.eval_score == 0.75
    assert index.scores_for(run_id)[0][:3] == ("task_success", 0.75, True)

    assert [r.id for r in index.search(RunQuery(min_score=0.5)).runs] == [run_id]
    assert index.search(RunQuery(max_score=0.5)).runs == []


async def test_indexing_only_opens_the_event_logs_it_has_to(store: LocalStore, run_id: str):
    """UI §50: a filter must not cost a scan of every run's events."""
    opened: list[str] = []
    original = store.read_events

    def counting(target: str) -> list[object]:
        opened.append(target)
        return original(target)  # type: ignore[return-value]

    store.read_events = counting  # type: ignore[method-assign]
    try:
        RunIndex(store).refresh()
    finally:
        store.read_events = original  # type: ignore[method-assign]
    assert opened == [], "a run with no evaluator dependency was read anyway"
