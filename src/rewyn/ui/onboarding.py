"""What to do first (UI spec §47, §59, §61).

Every other module here answers a question about runs that already exist.
This one answers the question a developer has before any do: *what is this,
and what do I do?*

UI §47 says empty states must teach the product, and gives the shape --
a sentence, a command, and the button that gets you to the next thing. That
generalises into the two documents this module builds:

- **Steps**: the path from nothing to a console worth opening, each step
  marked done from what the project actually contains, so it is a checklist
  that fills itself in rather than a tutorial you have to track yourself.
- **Jobs**: the four things the tool is *for*, each one a real screen, each
  marked with what it still needs. UI §59 gives every screen a question;
  these give the questions a reason.

Both surfaces build these from the same counts, so the answer to "what
should I do next" does not depend on which console you opened.
"""

from __future__ import annotations

from rewyn.ui import schemas as s

# The snippet the empty state hands you. It runs as written -- no API key, no
# placeholder to fill in, and it produces a run with a tool call and a context
# in it, so the screens it unlocks have something to show. UI §47's example is
# `pip install rewyn`; this is that, finished.
QUICKSTART = '''pip install "rewyn[ui]"'''

FIRST_RUN = '''from rewyn import Agent, tool
from rewyn.testing import FakeModel


@tool
def lookup_account(name: str) -> dict:
    """Look up a customer's account standing."""
    return {"name": name, "standing": "good", "since": 2021}


agent = Agent(
    # Swap for "anthropic:claude-opus-5" once you have a key set.
    model=FakeModel([
        FakeModel.tool_call("lookup_account", {"name": "Acme"}),
        "Acme has been in good standing since 2021.",
    ]),
    name="support-agent",
    version="1",
    tools=[lookup_account],
)

result = agent.run("Is Acme in good standing?")
print(result.output)
'''

SUMMARY = (
    "Rewyn records what your AI actually did — every model call, tool call, "
    "retrieved document and decision — so you can replay it, compare two runs, "
    "and prove a change made things better rather than hoping."
)


def location(
    *,
    surface: s.Surface,
    project: str,
    home: str | None = None,
    endpoint: str | None = None,
) -> s.ProjectLocation:
    """Where this console is reading from, and how to change it (UI §41)."""
    if surface == "local":
        how = (
            "A local project is a directory. This console reads $REWYN_HOME "
            "(default ./.rewyn) and names it with $REWYN_PROJECT. There is "
            "nothing to create: record a run and the project exists."
        )
    else:
        how = (
            "A cloud project is provisioned once with "
            "`rewyn-cloud create-project <name>`, which prints the API key "
            "your agents sync with. This console shows the project that key "
            "belongs to."
        )
    return s.ProjectLocation(
        surface=surface, project=project, home=home, endpoint=endpoint, how_to_change=how
    )


def steps(counts: s.ConsoleCounts) -> list[s.OnboardingStep]:
    """The path from nothing to a useful console, marked from what exists."""
    return [
        s.OnboardingStep(
            key="install",
            title="Install the SDK",
            detail=(
                "The console you are looking at ships with it. Nothing here needs "
                "an account, a key, or a network."
            ),
            done=True,
            code=QUICKSTART,
        ),
        s.OnboardingStep(
            key="record",
            title="Record your first run",
            detail=(
                "Wrap an agent and run it. Recording is automatic and local: this "
                "file runs as written, with no API key."
            ),
            done=counts.runs > 0,
            code=FIRST_RUN,
            href="/runs" if counts.runs else None,
            action="Open runs" if counts.runs else "",
        ),
        s.OnboardingStep(
            key="inspect",
            title="Read what it actually did",
            detail=(
                "Open the run: the timeline of every event, the context it "
                "assembled, the tools it called and what each one cost."
            ),
            done=counts.runs > 0,
            href="/runs",
            action="Open runs",
        ),
        s.OnboardingStep(
            key="test",
            title="Turn a run into a regression test",
            detail=(
                "Press T on any run. The case keeps its input and the output you "
                "expect, so the behaviour you just fixed cannot come back."
            ),
            done=counts.datasets > 0,
            href="/datasets",
            action="Open datasets",
        ),
        s.OnboardingStep(
            key="prove",
            title="Prove your next change",
            detail=(
                "Run the dataset against a new version. The report says what got "
                "better, what got worse, and whether it is safe to ship."
            ),
            done=counts.reports > 0,
            href="/regression",
            action="Open regression",
        ),
    ]


def jobs(counts: s.ConsoleCounts) -> list[s.UseCase]:
    """What the tool is for, and what each job still needs (UI §59)."""
    return [
        s.UseCase(
            key="debug",
            title="Debug an answer you do not trust",
            question="What is my AI doing?",
            detail=(
                "Find the run, read its timeline, and see the exact context, "
                "prompt and tool results behind the answer. Then replay it with "
                "one component changed to test what caused it."
            ),
            href="/runs",
            ready=counts.runs > 0,
            needs="Record a run." if not counts.runs else "",
        ),
        s.UseCase(
            key="prove",
            title="Prove a change did not make it worse",
            question="Did my change make it worse?",
            detail=(
                "Save real runs as test cases, then run them against your new "
                "version. Regression reports the cases that got worse by name, "
                "not a single score that hides them."
            ),
            href="/regression" if counts.datasets else "/datasets",
            ready=counts.reports > 0,
            needs=("Save a run as a test, then run the dataset." if not counts.reports else ""),
        ),
        s.UseCase(
            key="explain",
            title="Find what changed outside your code",
            question="What could have changed outside my code?",
            detail=(
                "A model, a prompt, a retrieved document, an MCP server: the "
                "things that change behaviour without a commit. Rewyn records "
                "each one's version on every run, so drift has evidence."
            ),
            href="/dependencies",
            ready=counts.versions > 1,
            needs=(
                "Record runs at two versions of an agent — set version= and bump it."
                if counts.versions <= 1
                else ""
            ),
        ),
        s.UseCase(
            key="cost",
            title="Understand what it costs",
            question="What did the spend go on?",
            detail=(
                "Cost per successful task rather than cost per run, broken down "
                "by agent, model, user, tool and environment — a cheap run that "
                "failed is not a saving."
            ),
            href="/cost",
            ready=counts.runs > 0,
            needs="Record a run." if not counts.runs else "",
        ),
    ]


def document(
    *,
    place: s.ProjectLocation,
    counts: s.ConsoleCounts,
    demo_loaded: bool = False,
    can_load_demo: bool = False,
) -> s.Onboarding:
    """The whole first-run document."""
    return s.Onboarding(
        location=place,
        counts=counts,
        empty=counts.runs == 0,
        demo_loaded=demo_loaded,
        can_load_demo=can_load_demo,
        summary=SUMMARY,
        steps=steps(counts),
        jobs=jobs(counts),
    )
