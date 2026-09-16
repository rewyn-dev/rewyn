"""A demo project, so the console can be understood before it is useful.

An inspection tool has a bootstrapping problem: it is worth nothing until you
have recorded something, and you cannot tell whether it is worth recording
until you have seen it. Every screen this console has answers a question
about runs that already exist, and a developer evaluating Rewyn has none.

So the console can seed its own. This records a small, realistic project --
two agents, a context, tools, two versions, a cluster of identical failures,
an evaluation and a dataset -- using :class:`~rewyn.testing.FakeModel`, so
it needs no API key, no network and no example package. Every run it writes
is tagged ``demo``, which is what makes it honestly removable: the console
marks the project as demo data while it is loaded, and ``clear`` deletes
exactly the runs this module created and nothing else.

It exists to be thrown away. Nothing in the product depends on it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rewyn.storage.local import LocalStore

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.agents.agent import Agent
    from rewyn.context import Context

DEMO_TAG = "demo"

# Realistic enough to reason about, small enough to read in one sitting.
SUCCESSES = 8
FAILURES = 3
FAILURE_MESSAGE = "ToolError: crm timed out after 30s"


@dataclass(frozen=True)
class DemoProject:
    """What seeding left behind."""

    runs: int
    failures: int
    agents: tuple[str, ...]
    dataset: str | None = None


def is_loaded(store: LocalStore) -> bool:
    """True when this project is holding demo data."""
    return any(_demo_run_ids(store))


def _demo_run_ids(store: LocalStore) -> list[str]:
    found: list[str] = []
    runs_dir = store.runs_dir
    if not runs_dir.exists():
        return found
    for entry in sorted(runs_dir.iterdir()):
        if not (entry / "manifest.json").exists():
            continue
        try:
            manifest = store.read_manifest(entry.name)
        except (ValueError, OSError):
            continue
        if DEMO_TAG in manifest.tags:
            found.append(entry.name)
    return found


def clear(store: LocalStore) -> int:
    """Delete every run this module created. Returns how many went."""
    ids = _demo_run_ids(store)
    for run_id in ids:
        store.delete_run(run_id)
    _clear_dataset(store)
    return len(ids)


def _clear_dataset(store: LocalStore) -> None:
    path = store.home / "datasets" / "demo-support.json"
    if path.exists():
        path.unlink()


def _context() -> Context:
    """The support agent's context: a policy, an account note, a stale one."""
    from rewyn.context import Context, text_item

    context: Context = Context(name="support-context", budget=2000)
    context.add(
        text_item(
            "Refunds above 50,000 require manager approval as of policy v19.",
            source="kb://policy/refunds",
        )
    )
    context.add(
        text_item(
            "Acme Corp has paid every invoice on time since 2021.",
            source="crm://acme/history",
        )
    )
    return context


async def _record(store: LocalStore) -> DemoProject:
    # Imported from their own modules rather than the package root: the root's
    # lazy re-export is typed `Any`, which would quietly untype every tool here.
    from rewyn.agents.agent import Agent
    from rewyn.core.run import start_run
    from rewyn.runtime.recorder import default_recorder
    from rewyn.testing import FakeModel
    from rewyn.tools.tool import tool

    @tool
    def lookup_account(name: str) -> dict[str, object]:
        """Look up a customer's account standing."""
        return {"name": name, "standing": "good", "since": 2021}

    @tool
    def issue_refund(amount: int) -> dict[str, object]:
        """Issue a refund to the customer."""
        return {"issued": amount, "approval": "manager" if amount > 50_000 else "none"}

    def support(version: str, *, tools: list[Any]) -> Agent:
        model = FakeModel(
            [
                FakeModel.tool_call("lookup_account", {"name": "Acme Corp"}),
                "Acme is in good standing since 2021; the refund is within policy.",
            ],
            cycle=True,
        )
        return Agent(
            model=model,
            name="support-agent",
            version=version,
            instructions="Answer the customer's question using the account record.",
            tools=tools,
            context=_context(),
            max_iterations=4,
        )

    questions = [
        "Can Acme get a refund on invoice 4471?",
        "Is Acme in good standing?",
        "What is our refund policy above 50,000?",
        "Acme is asking about invoice 4471 again",
    ]

    # Two versions of the same agent, so version history, drift and releases
    # have the two points they need to say anything at all.
    for index in range(SUCCESSES):
        version = "1" if index < SUCCESSES // 2 else "2"
        tools = [lookup_account] if version == "1" else [lookup_account, issue_refund]
        agent = support(version, tools=tools)
        await agent.arun(questions[index % len(questions)], tags=[DEMO_TAG])

    # A cluster of identical failures, which is what an incident is (UI §36).
    for index in range(FAILURES):
        try:
            async with start_run(
                "support-agent",
                input=f"Refund status for invoice {5000 + index}",
                tags=[DEMO_TAG],
            ):
                raise RuntimeError(FAILURE_MESSAGE)
        except RuntimeError:
            pass

    # A second agent, so the registry pages are not a list of one.
    triage = Agent(
        model=FakeModel(["This is a billing question; routing to billing."], cycle=True),
        name="triage-agent",
        version="1",
        instructions="Route the message to the right queue.",
        max_iterations=2,
    )
    await triage.arun("My card was charged twice", tags=[DEMO_TAG])

    dataset = _dataset(store)
    await _evaluate(store)
    default_recorder().flush()

    return DemoProject(
        runs=SUCCESSES + FAILURES + 1,
        failures=FAILURES,
        agents=("support-agent", "triage-agent"),
        dataset=dataset,
    )


def _dataset(store: LocalStore) -> str:
    """A small regression set, so the quality screens have a baseline."""
    from rewyn.evaluation.dataset import Dataset

    dataset = Dataset(name="demo-support")
    dataset.add("Is Acme in good standing?", "good standing")
    dataset.add("What is our refund policy above 50,000?", "manager approval")
    dataset.save(home=store.home)
    return dataset.name


async def _evaluate(store: LocalStore) -> None:
    """Score the newest demo run, so Evaluations is not empty either."""
    from rewyn.core.run import start_run
    from rewyn.evaluation.evaluator import Subject, evaluator

    @evaluator
    def task_success(subject: Subject) -> float:
        return 0.93

    @evaluator
    def groundedness(subject: Subject) -> float:
        return 0.81

    async with start_run("demo-evaluation", tags=[DEMO_TAG]):
        await task_success.ascore(Subject(output="good standing"))
        await groundedness.ascore(Subject(output="good standing"))


def load(store: LocalStore, *, replace: bool = True) -> DemoProject:
    """Seed the demo project. Synchronous, for the CLI and the console."""
    if replace:
        clear(store)
    store.initialize()
    return asyncio.run(_record(store))


async def aload(store: LocalStore, *, replace: bool = True) -> DemoProject:
    """Seed the demo project from inside a running loop."""
    if replace:
        clear(store)
    store.initialize()
    return await _record(store)
