"""Cooperative cancellation for long-running agents and graphs."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import Callable, Iterator

from rewyn.core.types import RewynError


class CancelledError(RewynError):
    """Raised when a cancellation token has been triggered."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__(reason or "execution was cancelled")
        self.reason = reason


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason: str | None = None
        self._callbacks: list[Callable[[str | None], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str | None = None) -> None:
        if self.cancelled:
            return
        self.reason = reason
        self._event.set()
        for callback in self._callbacks:
            with contextlib.suppress(Exception):
                callback(reason)

    def check(self) -> None:
        """Raise :class:`CancelledError` if cancelled."""
        if self.cancelled:
            raise CancelledError(self.reason)

    async def wait(self) -> None:
        await self._event.wait()

    def on_cancel(self, callback: Callable[[str | None], None]) -> None:
        self._callbacks.append(callback)
        if self.cancelled:
            callback(self.reason)


_current: contextvars.ContextVar[CancellationToken | None] = contextvars.ContextVar(
    "rewyn_cancellation_token", default=None
)


def current_token() -> CancellationToken | None:
    return _current.get()


def check_cancelled() -> None:
    token = _current.get()
    if token is not None:
        token.check()


@contextlib.contextmanager
def cancellation_scope(token: CancellationToken) -> Iterator[CancellationToken]:
    reset = _current.set(token)
    try:
        yield token
    finally:
        _current.reset(reset)
