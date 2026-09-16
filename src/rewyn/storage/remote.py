"""Syncing local runs to a Rewyn API (spec §45, §46).

Local-first means the cloud is optional, and it also means the cloud is
never in the critical path. This client is what ``rewyn sync`` drives:

- **Retry.** Connection failures, ``429`` and ``5xx`` are retried with
  exponential backoff. A ``4xx`` is a request problem and is not retried.
- **Buffering.** Anything that still fails is written to a local queue and
  attempted again on the next sync, so a laptop that was offline catches up
  rather than losing runs.
- **Fail open.** Sync collects failures into a :class:`SyncResult` instead
  of raising, because a telemetry upload must never take down the thing it
  is observing. Pass ``strict=True`` when you do want the exception.

Credentials live in ``<REWYN_HOME>/credentials.json`` with owner-only
permissions, and the API key is registered with the redactor so it cannot
leak into a recorded event.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.settings import get_settings
from rewyn.core.sync import run_sync
from rewyn.core.types import JSONObject, MissingDependencyError, RewynError

API_VERSION = "v1"
DEFAULT_ENDPOINT = "https://api.rewyn.dev"
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_QUEUE = 5000


class RemoteError(RewynError):
    """A request to the Rewyn API failed."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status in RETRY_STATUSES


class NotAuthenticatedError(RemoteError):
    """No credentials are configured, or the key was rejected."""


class Credentials(BaseModel):
    """Where to sync, and with what key."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = DEFAULT_ENDPOINT
    api_key: str = ""
    project: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def base_url(self) -> str:
        return f"{self.endpoint.rstrip('/')}/{API_VERSION}"

    def headers(self) -> dict[str, str]:
        from rewyn import __version__

        return {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": f"rewyn/{__version__}",
            "Content-Type": "application/json",
        }

    def redacted(self) -> JSONObject:
        """A display-safe view. The key is never shown in full."""
        return {
            "endpoint": self.endpoint,
            "project": self.project,
            "api_key": f"{self.api_key[:11]}…" if self.api_key else "",
        }

    # Storage ------------------------------------------------------------------
    @staticmethod
    def path(home: Path | None = None) -> Path:
        return (home or get_settings().home) / "credentials.json"

    def save(self, *, home: Path | None = None) -> Path:
        """Write credentials with owner-only permissions."""
        target = Credentials.path(home)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        self.register()
        return target

    @classmethod
    def load(cls, *, home: Path | None = None) -> Credentials:
        """Read credentials, with environment variables taking precedence."""
        target = cls.path(home)
        found = (
            cls.model_validate_json(target.read_text(encoding="utf-8"))
            if target.exists()
            else cls()
        )
        endpoint = os.environ.get("REWYN_ENDPOINT")
        api_key = os.environ.get("REWYN_API_KEY")
        project = os.environ.get("REWYN_CLOUD_PROJECT")
        resolved = found.model_copy(
            update={
                k: v
                for k, v in (
                    ("endpoint", endpoint),
                    ("api_key", api_key),
                    ("project", project),
                )
                if v
            }
        )
        resolved.register()
        return resolved

    @classmethod
    def clear(cls, *, home: Path | None = None) -> bool:
        target = cls.path(home)
        if not target.exists():
            return False
        target.unlink()
        return True

    def register(self) -> None:
        """Teach the redactor this key so it never reaches disk in an event."""
        if self.api_key:
            from rewyn.security.redaction import default_redactor

            default_redactor().register_secret(self.api_key)


class SyncResult(BaseModel):
    """What one sync did, including what it could not do."""

    model_config = ConfigDict(extra="forbid")

    uploaded: list[str] = Field(default_factory=list)
    replaced: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    queued: int = 0
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failed and not self.errors

    @property
    def total(self) -> int:
        return len(self.uploaded) + len(self.replaced)

    def summary(self) -> JSONObject:
        return {
            "uploaded": len(self.uploaded),
            "replaced": len(self.replaced),
            "skipped": len(self.skipped),
            "failed": len(self.failed),
            "queued": self.queued,
            "ok": self.ok,
        }


class SyncQueue:
    """Run ids that failed to upload, retried on the next sync."""

    def __init__(self, home: Path | None = None) -> None:
        self.path = (home or get_settings().home) / "sync-queue.json"

    def read(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [str(item) for item in data][:MAX_QUEUE] if isinstance(data, list) else []

    def write(self, run_ids: Iterable[str]) -> None:
        unique = list(dict.fromkeys(run_ids))[:MAX_QUEUE]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(unique, indent=2) + "\n", encoding="utf-8")

    def add(self, run_ids: Iterable[str]) -> None:
        self.write([*self.read(), *run_ids])

    def remove(self, run_ids: Iterable[str]) -> None:
        done = set(run_ids)
        self.write([r for r in self.read() if r not in done])

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


class RemoteStore:
    """Push local runs, datasets and evaluation reports to a Rewyn API.

    ``httpx`` is imported lazily and only when a request is made, so the
    core install stays free of it. Tests inject a client built on an
    in-process transport, which is how the end-to-end test talks to the
    cloud service with no server and no network.
    """

    def __init__(
        self,
        credentials: Credentials | None = None,
        *,
        client: Any = None,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 0.25,
        home: Path | None = None,
    ) -> None:
        self.credentials = credentials or Credentials.load(home=home)
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self.home = home
        self.queue = SyncQueue(home)
        self._client = client
        self._owned = client is None

    def __repr__(self) -> str:
        return f"RemoteStore({self.credentials.endpoint!r})"

    # Transport ----------------------------------------------------------------
    def _require_httpx(self) -> Any:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - needs the extra uninstalled
            raise MissingDependencyError("httpx", "remote") from exc
        return httpx

    def _ensure_client(self) -> Any:
        if self._client is None:
            httpx = self._require_httpx()
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owned:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> RemoteStore:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def _request(
        self, method: str, path: str, *, json_body: Any = None, params: Any = None
    ) -> Any:
        """One API call, retried on transient failures."""
        if not self.credentials.configured:
            raise NotAuthenticatedError("no API key configured; run 'rewyn login' first")
        client = self._ensure_client()
        url = f"{self.credentials.base_url}{path}"
        last: RemoteError | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    headers=self.credentials.headers(),
                )
            except Exception as exc:  # transport failure: always worth retrying
                last = RemoteError(f"{type(exc).__name__}: {exc}")
            else:
                if response.status_code < 400:
                    return response.json() if response.content else None
                last = RemoteError(_detail(response), status=response.status_code)
                if response.status_code in (401, 403):
                    raise NotAuthenticatedError(str(last), status=response.status_code)
                if not last.retryable:
                    raise last
            if attempt < self.retries:
                await asyncio.sleep(self.backoff * (2**attempt))
        raise last or RemoteError("request failed")

    # Identity -----------------------------------------------------------------
    async def averify(self) -> JSONObject:
        """Confirm the key works and report the project it belongs to."""
        identity = await self._request("GET", "/me")
        if isinstance(identity, dict) and identity.get("project"):
            self.credentials = self.credentials.model_copy(
                update={"project": str(identity["project"])}
            )
        return dict(identity or {})

    def verify(self) -> JSONObject:
        return run_sync(self.averify())

    async def aping(self) -> bool:
        """True when the endpoint is reachable. Never raises."""
        try:
            client = self._ensure_client()
            response = await client.get(f"{self.credentials.base_url}/health", timeout=self.timeout)
            return bool(response.status_code < 400)
        except Exception:
            return False

    def ping(self) -> bool:
        return run_sync(self.aping())

    # Runs ---------------------------------------------------------------------
    async def apush_run(self, run: Any, *, store: Any = None) -> JSONObject:
        """Upload one run. Re-uploading the same run replaces it server-side."""
        manifest, events = _bundle(run, store)
        result = await self._request(
            "POST", "/runs", json_body={"manifest": manifest, "events": events}
        )
        return dict(result or {})

    def push_run(self, run: Any, *, store: Any = None) -> JSONObject:
        return run_sync(self.apush_run(run, store=store))

    async def apush_runs(
        self,
        run_ids: Sequence[str] | None = None,
        *,
        store: Any = None,
        limit: int | None = None,
        include_queued: bool = True,
        strict: bool = False,
    ) -> SyncResult:
        """Upload runs, draining anything left over from a previous attempt.

        Failures are collected rather than raised, and their run ids are
        queued for the next sync.
        """
        from rewyn.storage.local import LocalStore

        local = store or LocalStore(self.home) if self.home else (store or LocalStore())
        started = time.perf_counter()
        result = SyncResult()
        pending = list(self.queue.read()) if include_queued else []
        wanted = (
            list(run_ids) if run_ids is not None else [m.id for m in local.list_runs(limit=limit)]
        )
        targets = list(dict.fromkeys([*pending, *wanted]))
        failures: list[str] = []
        for run_id in targets:
            try:
                outcome = await self.apush_run(run_id, store=local)
            except NotAuthenticatedError:
                if strict:
                    raise
                result.errors.append("not authenticated; run 'rewyn login'")
                failures.extend(targets[targets.index(run_id) :])
                break
            except Exception as exc:
                if strict:
                    raise
                result.failed.append(run_id)
                result.errors.append(f"{run_id}: {type(exc).__name__}: {exc}")
                failures.append(run_id)
                continue
            if outcome.get("created", True):
                result.uploaded.append(run_id)
            else:
                result.replaced.append(run_id)
        succeeded = set(result.uploaded) | set(result.replaced)
        self.queue.remove(succeeded)
        if failures:
            self.queue.add(f for f in failures if f not in succeeded)
        result.queued = len(self.queue.read())
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        return result

    def push_runs(
        self,
        run_ids: Sequence[str] | None = None,
        *,
        store: Any = None,
        limit: int | None = None,
        include_queued: bool = True,
        strict: bool = False,
    ) -> SyncResult:
        return run_sync(
            self.apush_runs(
                run_ids,
                store=store,
                limit=limit,
                include_queued=include_queued,
                strict=strict,
            )
        )

    async def alist_runs(self, **filters: Any) -> JSONObject:
        """Search runs on the server."""
        clean = {k: v for k, v in filters.items() if v is not None}
        return dict(await self._request("GET", "/runs", params=clean) or {})

    def list_runs(self, **filters: Any) -> JSONObject:
        return run_sync(self.alist_runs(**filters))

    async def apull_run(self, run_id: str, *, store: Any = None) -> str:
        """Download a run and write it into local storage so it can be replayed."""
        from rewyn.core.event import Event
        from rewyn.core.run import RunManifest
        from rewyn.storage.local import LocalStore

        local = store or LocalStore(self.home) if self.home else (store or LocalStore())
        detail = await self._request("GET", f"/runs/{run_id}")
        payload = await self._request("GET", f"/runs/{run_id}/events")
        manifest = RunManifest.model_validate((detail or {}).get("manifest") or {})
        if local.exists(manifest.id):
            local.delete_run(manifest.id)
        local.write_manifest(manifest)
        events = [Event.from_record(record) for record in (payload or {}).get("events") or []]
        if events:
            local.append_events(manifest.id, events)
        return manifest.id

    def pull_run(self, run_id: str, *, store: Any = None) -> str:
        return run_sync(self.apull_run(run_id, store=store))

    # Datasets and evaluations --------------------------------------------------
    async def apush_dataset(self, dataset: Any) -> JSONObject:
        """Upload a dataset so a team shares one source of regression truth."""
        payload = dataset if isinstance(dataset, dict) else dataset.model_dump(mode="json")
        return dict(await self._request("POST", "/datasets", json_body={"dataset": payload}) or {})

    def push_dataset(self, dataset: Any) -> JSONObject:
        return run_sync(self.apush_dataset(dataset))

    async def apull_dataset(self, name: str, version: str | None = None) -> Any:
        """Download a dataset as a local :class:`~rewyn.evaluation.dataset.Dataset`."""
        from rewyn.evaluation.dataset import Dataset

        params = {"version": version} if version else None
        payload = await self._request("GET", f"/datasets/{name}", params=params)
        return Dataset.model_validate((payload or {}).get("dataset") or {})

    def pull_dataset(self, name: str, version: str | None = None) -> Any:
        return run_sync(self.apull_dataset(name, version))

    async def apush_report(self, report: Any) -> JSONObject:
        """Upload a regression report so releases can be compared centrally."""
        payload = report if isinstance(report, dict) else report.model_dump(mode="json")
        return dict(
            await self._request("POST", "/evaluations", json_body={"report": payload}) or {}
        )

    def push_report(self, report: Any) -> JSONObject:
        return run_sync(self.apush_report(report))

    async def alist_reports(self, dataset: str | None = None, limit: int = 50) -> list[JSONObject]:
        params = {"limit": limit} | ({"dataset": dataset} if dataset else {})
        found = await self._request("GET", "/evaluations", params=params)
        return list(found or [])

    def list_reports(self, dataset: str | None = None, limit: int = 50) -> list[JSONObject]:
        return run_sync(self.alist_reports(dataset, limit))


def _detail(response: Any) -> str:
    try:
        body = response.json()
    except Exception:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict) and "detail" in body:
        return f"HTTP {response.status_code}: {body['detail']}"
    return f"HTTP {response.status_code}"


def _bundle(run: Any, store: Any = None) -> tuple[JSONObject, list[JSONObject]]:
    """Normalise a run id, ``Run`` or ``RecordedRun`` into manifest plus events."""
    from rewyn.storage.local import LocalStore

    if isinstance(run, str):
        local = store or LocalStore()
        run_id = local.resolve_run_id(run)
        manifest = local.read_manifest(run_id)
        events = local.read_events(run_id)
    elif hasattr(run, "manifest") and hasattr(run, "events"):
        manifest = run.manifest
        events = list(run.events)
    else:
        raise RemoteError(f"cannot upload {type(run).__name__}; pass a run id or a Run")
    from rewyn.security.redaction import default_redactor

    redactor = default_redactor()
    return (
        redactor.redact(manifest.model_dump(mode="json")),
        [redactor.redact(e.to_record() if hasattr(e, "to_record") else e) for e in events],
    )
