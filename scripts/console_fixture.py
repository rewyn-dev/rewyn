"""Serve a console with a recorded run in it, for the end-to-end tests.

The browser tests need something real to look at, so this records the golden
demo into a throwaway ``REWYN_HOME`` and serves it exactly as ``rewyn ui``
would. Nothing here is used at runtime by the product.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT = int(os.environ.get("CONSOLE_PORT", "4456"))


def main() -> int:
    home = Path(os.environ.get("CONSOLE_HOME") or tempfile.mkdtemp(prefix="rewyn-e2e-"))
    os.environ["REWYN_HOME"] = str(home / ".rewyn")

    from tests.ui.conftest import record_demo_run

    async def seed() -> None:
        """Runs, a verdict on one of them, and a failure cluster.

        The console derives everything from recorded runs, so the fixture is
        just runs: two executions of the golden demo, an evaluation of the
        first so the quality screens have something real to show, and three
        identical failures so the incident screens have a real incident.
        """
        first = await record_demo_run()
        await record_demo_run(user="sam")

        from rewyn.core.run import start_run
        from rewyn.evaluation.evaluator import Subject, evaluator
        from rewyn.runtime.recorder import default_recorder

        @evaluator
        def task_success(subject: Subject) -> float:
            return 0.92

        @evaluator
        def groundedness(subject: Subject) -> float:
            return 0.55

        with start_run("evaluation"):
            await task_success.ascore(Subject(output="done", run_id=first))
            await groundedness.ascore(Subject(output="done", run_id=first))

        # One agent with a single run, so the screens that need history have
        # something to say "not enough of it" about.
        from rewyn.agents.agent import Agent
        from rewyn.testing.fake_model import FakeModel

        async with start_run("onboarding-bot", metadata={"environment": "staging"}) as once:
            result = await Agent(model=FakeModel(["welcome"]), name="onboarding-bot").arun("hi")
            once.manifest.output = result.output

        # A failure cluster, so the incident screens have a real incident:
        # three runs of one agent failing the same way is what §36 detects.
        for case in range(3):
            try:
                async with start_run(
                    "refund-agent",
                    input=f"refund {case}",
                    metadata={"environment": "production"},
                ):
                    raise RuntimeError("refund policy requires manager approval")
            except RuntimeError:
                pass

        default_recorder().flush()

    asyncio.run(seed())

    from rewyn.storage.local import LocalStore
    from rewyn.ui.server import serve

    store = LocalStore()
    sys.stdout.write(f"console fixture: {store.home} on :{PORT}\n")
    sys.stdout.flush()
    serve(host="127.0.0.1", port=PORT, store=store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
