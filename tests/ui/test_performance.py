"""The UI §50 performance budgets, asserted against the seeded project.

Plan §10 writes eight budgets and says they are enforced in CI. This module
is that enforcement. Every budget runs against `tests/ui/fixtures.py` at the
scale it was written for -- 100,000 runs, a 5,000-event run, a 40-node graph,
a 2,400-case dataset -- through the real HTTP app.

Three of the eight are stated as browser numbers ("first row painted",
"60fps", "event-to-paint"). A pytest cannot paint. What it can do is hold the
server to the part of the budget the server owns, and each test below says
which part that is rather than pretending to measure the rest: the frontend
side is covered by the virtualization and windowing the Playwright suite
exercises.

Timings are noisy on shared CI, so every measurement is the median of several
attempts and the budgets have real headroom against observed numbers -- these
exist to catch an order-of-magnitude regression (a missing index, a table
scan, an event log read on the header path), not to police milliseconds.
"""

from __future__ import annotations

import gzip
import re
import statistics
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from rewyn.storage.local import LocalStore
from rewyn.ui.index import RunIndex
from rewyn.ui.server import PREFIX, build_app
from tests.ui.conftest import BASE_URL
from tests.ui.fixtures import (
    CASES_IN_A_BIG_DATASET,
    CONTEXT_ITEMS,
    EVENTS_IN_A_BIG_RUN,
    NODES_IN_A_BIG_GRAPH,
    RUNS,
    SeededProject,
    seed_project,
)

pytestmark = pytest.mark.performance

STATIC = Path(__file__).resolve().parents[2] / "src" / "rewyn" / "ui" / "static"


async def measure(call: Callable[[], object], *, attempts: int = 5) -> float:
    """Median wall-clock milliseconds over ``attempts`` runs of ``call``."""
    samples: list[float] = []
    for _ in range(attempts):
        start = time.perf_counter()
        await call()  # type: ignore[misc]
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(samples)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> tuple[LocalStore, SeededProject]:
    """One seeded project for the whole module: building it is the slow part."""
    home = tmp_path_factory.mktemp("perf") / ".rewyn"
    store = LocalStore(home)
    project = seed_project(store, RunIndex(store), runs=RUNS)
    return store, project


@pytest.fixture
def client(seeded: tuple[LocalStore, SeededProject]) -> httpx.AsyncClient:
    """The real app over the seeded store, with background refresh suppressed.

    The 100,000 rows are in the index without a directory each (see
    `fixtures.py`), so a refresh would treat them as deleted runs. Suppressing
    it is what lets the budget measure the query rather than the filesystem.
    """
    store, _project = seeded
    app = build_app(store)
    state = app.state.console
    state.refresh_interval = 10_000.0
    state._last_refresh = time.monotonic()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)


async def test_the_runs_list_answers_at_a_hundred_thousand_runs(
    client: httpx.AsyncClient, seeded: tuple[LocalStore, SeededProject]
):
    """UI §50: first row < 800ms cold, p95 API < 150ms.

    The server's half of "first row painted" is the first page of rows: the
    browser cannot paint a row it has not been sent.
    """
    _store, project = seeded
    assert project.runs == RUNS

    start = time.perf_counter()
    first = await client.get(f"{PREFIX}/runs", params={"limit": 50})
    cold_ms = (time.perf_counter() - start) * 1000.0

    assert first.status_code == 200
    page = first.json()
    assert len(page["runs"]) == 50
    assert page["total"] >= RUNS
    assert cold_ms < 800.0, f"first page took {cold_ms:.0f}ms cold"

    # p95 across the §7 filter set, which is what the table actually issues.
    queries: list[dict[str, object]] = [
        {"limit": 50},
        {"limit": 50, "status": "failed"},
        {"limit": 50, "agent": "refund-agent"},
        {"limit": 50, "environment": "production"},
        {"limit": 50, "user": "raj"},
        {"limit": 50, "agent": "refund-agent", "environment": "production"},
        {"limit": 50, "q": "salesforce"},
    ]
    samples: list[float] = []
    for params in queries:
        for _ in range(5):
            begin = time.perf_counter()
            response = await client.get(f"{PREFIX}/runs", params=params)
            samples.append((time.perf_counter() - begin) * 1000.0)
            assert response.status_code == 200, params
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 < 150.0, f"p95 was {p95:.0f}ms over {len(samples)} requests"


async def test_paging_deep_into_a_hundred_thousand_runs_stays_flat(
    client: httpx.AsyncClient,
):
    """A cursor, not an offset: page 40 must cost what page 1 costs (UI §50)."""
    cursor: str | None = None
    timings: list[float] = []
    for _ in range(40):
        params: dict[str, object] = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        begin = time.perf_counter()
        page = (await client.get(f"{PREFIX}/runs", params=params)).json()
        timings.append((time.perf_counter() - begin) * 1000.0)
        cursor = page.get("next_cursor")
        if not cursor:
            break
    assert len(timings) > 10, "the fixture should page well past the first screen"
    first, last = timings[0], timings[-1]
    assert last < max(first * 6.0, 150.0), (
        f"page 1 took {first:.1f}ms and the last page {last:.1f}ms — that is an offset scan"
    )


async def test_the_run_header_does_not_scan_a_five_thousand_event_log(
    client: httpx.AsyncClient, seeded: tuple[LocalStore, SeededProject]
):
    """UI §50: run detail header < 400ms on a 5k-event run."""
    _store, project = seeded
    assert project.big_run_id

    elapsed = await measure(lambda: client.get(f"{PREFIX}/runs/{project.big_run_id}"))
    detail = (await client.get(f"{PREFIX}/runs/{project.big_run_id}")).json()
    assert detail["run"]["event_count"] == EVENTS_IN_A_BIG_RUN
    assert elapsed < 400.0, f"the header took {elapsed:.0f}ms"


async def test_the_timeline_pages_rather_than_returning_five_thousand_rows(
    client: httpx.AsyncClient, seeded: tuple[LocalStore, SeededProject]
):
    """UI §50: 60fps timeline scroll.

    The frontend virtualizes; the server's half of that contract is a bounded
    window, so a scroll fetches a page instead of the whole log.
    """
    _store, project = seeded
    elapsed = await measure(
        lambda: client.get(f"{PREFIX}/runs/{project.big_run_id}/timeline", params={"limit": 200})
    )
    view = (
        await client.get(f"{PREFIX}/runs/{project.big_run_id}/timeline", params={"limit": 200})
    ).json()
    assert len(view["entries"]) == 200, "the window is bounded"
    assert view["next_seq"], "and it says where the next window starts"
    assert elapsed < 400.0, f"a timeline window took {elapsed:.0f}ms"


async def test_the_context_inspector_holds_two_hundred_items(
    client: httpx.AsyncClient, seeded: tuple[LocalStore, SeededProject]
):
    """UI §50: context inspector, 200 items, < 300ms."""
    _store, project = seeded
    elapsed = await measure(lambda: client.get(f"{PREFIX}/runs/{project.big_run_id}/context"))
    view = (await client.get(f"{PREFIX}/runs/{project.big_run_id}/context")).json()
    assert len(view["assemblies"][0]["items"]) == CONTEXT_ITEMS
    assert elapsed < 300.0, f"the context projection took {elapsed:.0f}ms"


async def test_a_forty_node_graph_is_laid_out_server_side(
    client: httpx.AsyncClient, seeded: tuple[LocalStore, SeededProject]
):
    """UI §50: graph, 40 nodes, < 500ms to interactive."""
    _store, project = seeded
    assert project.graph_run_id

    elapsed = await measure(lambda: client.get(f"{PREFIX}/runs/{project.graph_run_id}/graph"))
    view = (await client.get(f"{PREFIX}/runs/{project.graph_run_id}/graph")).json()
    assert len(view["nodes"]) >= NODES_IN_A_BIG_GRAPH
    assert elapsed < 500.0, f"the graph projection took {elapsed:.0f}ms"


async def test_a_dataset_of_two_thousand_four_hundred_cases_opens(
    client: httpx.AsyncClient, seeded: tuple[LocalStore, SeededProject]
):
    """UI §50: dataset, 2,400 cases, < 600ms."""
    _store, project = seeded
    assert project.dataset

    elapsed = await measure(lambda: client.get(f"{PREFIX}/datasets/{project.dataset}"))
    detail = (await client.get(f"{PREFIX}/datasets/{project.dataset}")).json()
    assert detail["cases"] == CASES_IN_A_BIG_DATASET
    assert elapsed < 600.0, f"the dataset took {elapsed:.0f}ms"


async def test_the_live_stream_sends_its_first_frame_immediately(
    seeded: tuple[LocalStore, SeededProject],
):
    """UI §50: live stream < 250ms event-to-paint.

    The server owns the "event" half: a subscriber must get a frame without
    waiting out a poll interval, or nothing downstream can meet the budget.
    The generator is driven directly rather than over the test transport,
    which buffers a response body and would measure itself rather than the
    stream -- the same way `tests/ui/test_live.py` drives it.
    """
    from rewyn.ui import live

    store, _project = seeded
    stream = live.stream_live(store, lambda: [], interval=0.02)

    start = time.perf_counter()
    frame = await anext(stream)
    elapsed = (time.perf_counter() - start) * 1000.0
    await stream.aclose()

    assert frame.startswith("data:"), "a subscriber gets a frame, not silence"
    assert elapsed < 250.0, f"the first live frame took {elapsed:.0f}ms"


def test_the_first_route_bundle_fits_in_the_budget():
    """UI §50: JS bundle < 250KB gzipped on first route."""
    index = STATIC / "index.html"
    if not index.exists():  # pragma: no cover - only when the bundle is unbuilt
        pytest.skip("the console bundle is not built; run `make build-web`")

    html = index.read_text(encoding="utf-8")
    referenced = sorted(set(re.findall(r"_next/[^\"']+?\.(?:js|css)", html)))
    assert referenced, "the shell should reference its own bundle"

    total = 0
    for href in referenced:
        asset = STATIC / href
        assert asset.exists(), f"{href} is referenced but not shipped"
        total += len(gzip.compress(asset.read_bytes(), 6))
    kilobytes = total / 1024.0
    assert kilobytes < 250.0, f"the first route ships {kilobytes:.0f}KB gzipped"
