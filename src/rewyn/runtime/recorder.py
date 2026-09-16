"""Asynchronous, batched, fail-open event recorder (spec §53, §54).

The recorder is an :class:`EventSink` backed by a queue and a single
daemon writer thread. ``emit`` is non-blocking and never raises. Events are
redacted before they are written. If the writer fails, the failure is logged
and the host application keeps running; events are retained in a bounded
retry buffer and re-attempted on the next flush.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import defaultdict

from rewyn.core.event import Event, EventType
from rewyn.core.run import RunManifest, RunStatus
from rewyn.core.types import JSONObject
from rewyn.runtime.sampling import Sampler, is_failure, sampler_from_settings
from rewyn.security.redaction import Redactor, default_redactor
from rewyn.storage.local import LocalStore

log = logging.getLogger("rewyn.recorder")

_STOP = object()


class Recorder:
    """Persist events to a :class:`LocalStore` from a background thread."""

    def __init__(
        self,
        store: LocalStore | None = None,
        *,
        redactor: Redactor | None = None,
        batch_size: int = 200,
        flush_interval: float = 0.25,
        max_buffer: int = 100_000,
        sampler: Sampler | None = None,
        max_deferred: int = 2000,
    ) -> None:
        self.store = store or LocalStore()
        self.redactor = redactor or default_redactor()
        self.sampler = sampler or sampler_from_settings()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue: queue.Queue[Event | object] = queue.Queue(maxsize=max_buffer)
        self._pending: dict[str, list[JSONObject]] = defaultdict(list)
        self._manifests: dict[str, RunManifest] = {}
        self._idle = threading.Event()
        self._idle.set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self.dropped = 0
        self.failures = 0
        self.sampled_out = 0
        self.max_deferred = max_deferred
        self._decisions: dict[str, bool] = {}
        self._deferred: dict[str, list[Event]] = {}

    # Sampling ----------------------------------------------------------------
    def _decide(self, event: Event) -> bool:
        """Sample once, at RUN_STARTED, and apply that to the whole run."""
        decision = self._decisions.get(event.run_id)
        if decision is not None:
            return decision
        if event.type is not EventType.RUN_STARTED:
            # An event for a run we never saw start (a subagent, an implicit
            # run) is kept: dropping half a trace helps nobody.
            self._decisions[event.run_id] = True
            return True
        tags = frozenset(str(t) for t in event.payload.get("tags") or [])
        decision = self.sampler.sample(event.run_id, tags=tags)
        self._decisions[event.run_id] = decision
        if not decision:
            self.sampled_out += 1
        return decision

    def _defer(self, event: Event) -> list[Event] | None:
        """Hold a sampled-out run's events until we know whether it failed.

        Keeping only the failure event of a sampled-out run would leave a
        trace that cannot be replayed, which defeats the point of keeping
        failures at all. So the events are buffered, and promoted in full the
        moment the run fails. A run that succeeds is discarded, and a run that
        outgrows the buffer is dropped and counted.
        """
        buffered = self._deferred.setdefault(event.run_id, [])
        if is_failure(event):
            self._decisions[event.run_id] = True
            self.sampled_out = max(0, self.sampled_out - 1)
            promoted = [*buffered, event]
            del self._deferred[event.run_id]
            return promoted
        if event.type is EventType.RUN_FINISHED:
            self.dropped += len(buffered)
            del self._deferred[event.run_id]
            self._decisions.pop(event.run_id, None)
            return None
        if len(buffered) >= self.max_deferred:
            self.dropped += 1
            return None
        buffered.append(event)
        return None

    # EventSink protocol ------------------------------------------------------
    def emit(self, event: Event) -> None:
        """Queue an event. Never blocks, never raises."""
        if self._closed:
            return
        if not self._decide(event):
            for promoted in self._defer(event) or ():
                self._enqueue(promoted)
            return
        self._enqueue(event)

    def _enqueue(self, event: Event) -> None:
        try:
            self._ensure_thread()
            self._idle.clear()
            self._queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1
            log.warning("rewyn recorder buffer full; dropping event %s", event.type)
        except Exception:
            self.failures += 1
            log.exception("rewyn recorder failed to enqueue event")

    def flush(self, timeout: float = 10.0) -> bool:
        """Block until queued events are written (or ``timeout`` elapses)."""
        if self._thread is None or not self._thread.is_alive():
            self._drain_sync()
            return True
        try:
            self._queue.put(_FLUSH, timeout=timeout)
        except queue.Full:
            return False
        return self._idle.wait(timeout)

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._closed = True
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(_STOP)
            self._thread.join(timeout=5.0)

    # Manifest bookkeeping ----------------------------------------------------
    def record_manifest(self, manifest: RunManifest) -> None:
        """Write a manifest immediately (used at run start and finish)."""
        try:
            self.store.write_manifest(self._redacted_manifest(manifest))
        except Exception:
            self.failures += 1
            log.exception("rewyn recorder failed to write manifest %s", manifest.id)

    def _redacted_manifest(self, manifest: RunManifest) -> RunManifest:
        data = self.redactor.redact(manifest.model_dump(mode="json"))
        return RunManifest.model_validate(data)

    # Internals ---------------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="rewyn-recorder", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        deadline = time.monotonic() + self.flush_interval
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                self._write_pending()
                deadline = time.monotonic() + self.flush_interval
                if self._queue.empty():
                    self._idle.set()
                continue
            if item is _STOP:
                self._write_pending()
                self._idle.set()
                return
            if item is _FLUSH:
                self._write_pending()
                if self._queue.empty():
                    self._idle.set()
                continue
            assert isinstance(item, Event)
            self._stage(item)
            if sum(len(v) for v in self._pending.values()) >= self.batch_size:
                self._write_pending()
                deadline = time.monotonic() + self.flush_interval

    def _stage(self, event: Event) -> None:
        try:
            record = self.redactor.redact(event.to_record())
        except Exception:
            self.failures += 1
            log.exception("rewyn recorder failed to redact event; storing type only")
            record = {"type": event.type.value, "run_id": event.run_id, "seq": event.seq}
        self._pending[event.run_id].append(record)

    def _write_pending(self) -> None:
        if not self._pending:
            return
        for run_id, records in list(self._pending.items()):
            try:
                self.store.append_events(run_id, records)
                del self._pending[run_id]
            except Exception:
                self.failures += 1
                log.exception("rewyn recorder failed to write events for %s", run_id)
                if len(records) > self.batch_size * 10:
                    self.dropped += len(records)
                    del self._pending[run_id]

    def _drain_sync(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, Event):
                self._stage(item)
        self._write_pending()
        self._idle.set()


_FLUSH = object()


_default_recorder: Recorder | None = None
_default_lock = threading.Lock()


def default_recorder() -> Recorder:
    """Process-wide recorder bound to the configured ``REWYN_HOME``.

    A new recorder is created if the settings' home changed (tests rotate it).
    """
    global _default_recorder  # noqa: PLW0603
    from rewyn.core.settings import get_settings

    home = get_settings().home
    with _default_lock:
        if _default_recorder is None or _default_recorder.store.home != home:
            if _default_recorder is not None:
                _default_recorder.close()
            store = LocalStore(home)
            store.initialize()
            _default_recorder = RecordingSink(store)
        return _default_recorder


class RecordingSink(Recorder):
    """Recorder that also writes the manifest at run start and finish."""

    def emit(self, event: Event) -> None:
        super().emit(event)
        if event.type in (
            EventType.RUN_STARTED,
            EventType.RUN_FINISHED,
            EventType.RUN_FAILED,
            EventType.RUN_CANCELLED,
        ):
            from rewyn.core.run import current_run

            run = current_run()
            if run is not None and run.id == event.run_id:
                self.record_manifest(run.manifest)
            elif event.type is EventType.RUN_STARTED:
                self.record_manifest(
                    RunManifest(
                        id=event.run_id,
                        name=str(event.payload.get("name", "run")),
                        status=RunStatus.RUNNING,
                    )
                )
