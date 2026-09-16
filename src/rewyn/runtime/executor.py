"""Execution runtime: timeouts, cancellation and run status mapping."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TypeVar

from rewyn.core.event import EventType
from rewyn.core.run import RunStatus, current_run
from rewyn.runtime.cancellation import CancellationToken, CancelledError, cancellation_scope

T = TypeVar("T")


class Executor:
    """Run coroutines under a cancellation token and optional timeout.

    Cancellation and timeouts mark the active run ``CANCELLED`` and emit
    ``RUN_CANCELLED`` through the normal run lifecycle.
    """

    def __init__(
        self, *, timeout: float | None = None, token: CancellationToken | None = None
    ) -> None:
        self.timeout = timeout
        self.token = token or CancellationToken()

    async def execute(self, factory: Callable[[], Awaitable[T]]) -> T:
        with cancellation_scope(self.token):
            task = asyncio.ensure_future(factory())
            waiter = asyncio.ensure_future(self.token.wait())
            try:
                done, _ = await asyncio.wait(
                    {task, waiter},
                    timeout=self.timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if task in done:
                    return task.result()
                reason = self.token.reason if self.token.cancelled else "timeout"
                if not self.token.cancelled:
                    self.token.cancel(reason)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, CancelledError):
                    await task
                run = current_run()
                if run is not None and run.status is RunStatus.RUNNING:
                    run.emit(EventType.RUN_CANCELLED, {"reason": reason})
                raise CancelledError(reason)
            finally:
                waiter.cancel()
