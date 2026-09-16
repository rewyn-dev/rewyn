"""Fixtures for the console tests.

The console is a read model over recorded runs, so the fixtures record real
runs -- the golden demo, which exercises graphs, context, memory, tools,
skills, guardrails and human approval -- and then point the console at them.
Nothing is hand-written into the event log: if a projection passes here, it
passes against what the SDK actually emits.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from rewyn.core.run import start_run
from rewyn.replay.recorder import RecordedRun
from rewyn.runtime.recorder import default_recorder
from rewyn.storage.local import LocalStore
from rewyn.ui.index import RunIndex
from rewyn.ui.server import build_app

# The console answers loopback only, so tests address it the way a browser on
# the developer's machine does.
BASE_URL = "http://127.0.0.1:4400"


@pytest.fixture
def store(rewyn_home: Path) -> LocalStore:
    return LocalStore(rewyn_home)


async def record_demo_run(*, question: str | None = None, user: str = "raj") -> str:
    """Record one golden-demo run and return its id."""
    from examples.enterprise_research_agent.agent import (
        build_context,
        build_graph,
        build_memory,
        build_rag,
    )
    from examples.enterprise_research_agent.script import analyst_script, researcher_script

    from rewyn.human import AutoApprove, approval_scope

    prompt = question or "Should we increase Acme's credit limit?"
    with approval_scope(AutoApprove(by="tester")):
        # Indexing and memory writes are themselves recorded, so they happen
        # inside the run: one demo execution must leave exactly one run behind.
        async with start_run(
            "acme-credit",
            input=prompt,
            metadata={"environment": "production", "user": user, "session_id": "sess-1"},
        ) as run:
            rag = build_rag()
            memory = await build_memory()
            graph = build_graph(
                {"researcher": researcher_script(), "analyst": analyst_script()},
                build_context(rag, memory),
                memory,
            )
            result = await graph.arun(prompt)
            run.manifest.output = result.output
    default_recorder().flush()
    return run.id


@pytest.fixture
async def run_id() -> str:
    return await record_demo_run()


@pytest.fixture
def recorded(run_id: str, store: LocalStore) -> RecordedRun:
    return RecordedRun(store.read_manifest(run_id), store.read_events(run_id))


@pytest.fixture
def index(store: LocalStore) -> RunIndex:
    index = RunIndex(store)
    index.refresh()
    return index


@pytest.fixture
def app(store: LocalStore) -> FastAPI:
    return build_app(store)


@pytest.fixture
def client(app: FastAPI) -> httpx.AsyncClient:
    """Dispatches into the console app in-process: no server, no socket."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)
