"""Controlled code execution (spec §22).

::

    sandbox = rewyn.sandbox.create()
    result = sandbox.execute("print(2 + 2)")

The built-in :class:`SubprocessSandbox` runs code in a scratch directory
with a timeout, a scrubbed environment and best-effort resource limits. It
is a development sandbox, not a security boundary; production isolation is
delegated to external providers implementing :class:`SandboxProvider`.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.span import SpanKind
from rewyn.core.sync import run_sync
from rewyn.core.types import JSONObject, RewynError
from rewyn.models.pricing import compute_unit_cost
from rewyn.tools.tool import Tool, make_tool

Language = Literal["python", "bash"]

OUTPUT_LIMIT = 20_000


class SandboxSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout: float = 30.0
    env: dict[str, str] = Field(default_factory=dict)
    inherit_env: list[str] = Field(default_factory=lambda: ["PATH", "HOME", "LANG", "TMPDIR"])
    network: Literal["none", "allow"] = "none"
    memory_limit_mb: int | None = None
    packages: list[str] = Field(default_factory=list)
    workdir: str | None = None


class SandboxResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Language
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    code_hash: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SandboxError(RewynError):
    pass


@runtime_checkable
class Sandbox(Protocol):
    name: str
    spec: SandboxSpec

    async def aexecute(self, code: str, *, language: Language = "python") -> SandboxResult: ...

    async def write_file(self, path: str, content: str | bytes) -> None: ...

    async def read_file(self, path: str) -> bytes: ...

    async def snapshot(self) -> dict[str, str]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class SandboxProvider(Protocol):
    name: str

    async def create(self, spec: SandboxSpec) -> Sandbox: ...


def _hash(code: str) -> str:
    return "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


class SubprocessSandbox:
    name = "subprocess"

    def __init__(self, spec: SandboxSpec) -> None:
        self.spec = spec
        self._tmp = tempfile.mkdtemp(prefix="rewyn-sandbox-")
        self.workdir = Path(spec.workdir) if spec.workdir else Path(self._tmp)
        self.workdir.mkdir(parents=True, exist_ok=True)

    def _env(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in self.spec.inherit_env if k in os.environ}
        env.update(self.spec.env)
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        if self.spec.network == "none":
            # Best effort: most HTTP clients honour these; not a firewall.
            env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
            env["NO_PROXY"] = ""
        return env

    def _preexec(self) -> Any:
        limit = self.spec.memory_limit_mb
        if limit is None or sys.platform == "win32":
            return None
        import resource

        def _apply() -> None:
            bytes_limit = limit * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))

        return _apply

    def _resolve(self, path: str) -> Path:
        base = self.workdir.resolve()
        target = (base / path).resolve()
        if base not in target.parents and target != base:
            raise SandboxError(f"{path!r} escapes the sandbox directory")
        return target

    async def aexecute(self, code: str, *, language: Language = "python") -> SandboxResult:
        python_argv = [sys.executable, "-I", "-c", code]
        argv = python_argv if language == "python" else ["bash", "-c", code]
        started = time.perf_counter()
        timed_out = False
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.workdir,
            env=self._env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=self._preexec(),
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=self.spec.timeout)
        except TimeoutError:
            timed_out = True
            process.kill()
            out, err = await process.communicate()
        result = SandboxResult(
            language=language,
            exit_code=process.returncode if not timed_out else -9,
            stdout=out.decode("utf-8", errors="replace")[:OUTPUT_LIMIT],
            stderr=err.decode("utf-8", errors="replace")[:OUTPUT_LIMIT],
            duration_ms=(time.perf_counter() - started) * 1000.0,
            timed_out=timed_out,
            code_hash=_hash(code),
        )
        async with aensure_run("sandbox") as run:
            with run.span("sandbox:execute", SpanKind.SANDBOX):
                cost = compute_unit_cost(
                    "sandbox", self.name, calls=1, seconds=result.duration_ms / 1000.0
                )
                if cost:
                    run.record_cost("sandbox", cost)
                run.emit(
                    EventType.SANDBOX_EXECUTED,
                    {
                        "provider": self.name,
                        "cost": cost,
                        "language": language,
                        "code_hash": result.code_hash,
                        "code": code[:OUTPUT_LIMIT],
                        "exit_code": result.exit_code,
                        "timed_out": timed_out,
                        "duration_ms": result.duration_ms,
                        "stdout": result.stdout[:2000],
                        "stderr": result.stderr[:2000],
                        "timeout": self.spec.timeout,
                        "network": self.spec.network,
                    },
                )
        return result

    def execute(self, code: str, *, language: Language = "python") -> SandboxResult:
        return run_sync(self.aexecute(code, language=language))

    async def write_file(self, path: str, content: str | bytes) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)

    async def read_file(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    async def snapshot(self) -> dict[str, str]:
        """Text files in the sandbox directory (bounded), keyed by relative path."""
        files: dict[str, str] = {}
        for entry in sorted(self.workdir.rglob("*")):
            if entry.is_file() and entry.stat().st_size <= 200_000:
                try:
                    files[str(entry.relative_to(self.workdir))] = entry.read_text("utf-8")
                except UnicodeDecodeError:
                    continue
        return files

    async def aclose(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def close(self) -> None:
        run_sync(self.aclose())


class SubprocessProvider:
    name = "subprocess"

    async def create(self, spec: SandboxSpec) -> Sandbox:
        return SubprocessSandbox(spec)


_default_provider: SandboxProvider = SubprocessProvider()


def set_default_provider(provider: SandboxProvider) -> None:
    global _default_provider  # noqa: PLW0603
    _default_provider = provider


async def acreate(provider: SandboxProvider | None = None, **spec: Any) -> Sandbox:
    return await (provider or _default_provider).create(SandboxSpec(**spec))


def create(provider: SandboxProvider | None = None, **spec: Any) -> Sandbox:
    """Create a sandbox (spec §22: ``rewyn.sandbox.create()``)."""
    return run_sync(acreate(provider, **spec))


def sandbox_tool(sandbox: Sandbox, *, name: str = "run_code") -> Tool:
    """Expose a sandbox to agents as a ``run_code(code, language)`` tool."""

    async def run_code(code: str, language: Language = "python") -> JSONObject:
        """Execute code in an isolated sandbox and return stdout, stderr and exit code."""
        result = await sandbox.aexecute(code, language=language)
        return result.model_dump(exclude={"code_hash"})

    return make_tool(run_code, name=name, risk_level="high", permissions=["sandbox:execute"])
