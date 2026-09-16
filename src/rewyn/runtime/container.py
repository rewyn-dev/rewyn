"""Container-backed sandboxing (spec §22).

The subprocess sandbox bounds accidents well and determined escapes poorly.
It shares a kernel, a filesystem and a process table with the host, and its
network isolation is a proxy variable that a determined program ignores.
That is fine for code your own model wrote against your own data, and not
fine for code from somewhere you do not control.

This provider runs the same :class:`~rewyn.runtime.sandbox.Sandbox`
protocol inside a container, which gives real filesystem and network
isolation, a hard memory cap the kernel enforces, and a process that cannot
see the host. It shells out to a container runtime rather than taking a
client library dependency, so Docker, Podman and nerdctl all work::

    from rewyn.runtime.container import ContainerProvider
    from rewyn.runtime import sandbox

    sandbox.set_default_provider(ContainerProvider(image="python:3.13-slim"))

A container is not a virtual machine either. For genuinely hostile code,
point ``runtime`` at a microVM runtime such as gVisor or Kata; the interface
does not change.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

from rewyn.runtime.sandbox import (
    Language,
    SandboxError,
    SandboxResult,
    SandboxSpec,
    SubprocessSandbox,
    _hash,
)

DEFAULT_IMAGE = "python:3.13-slim"
DEFAULT_RUNTIME = "docker"


class ContainerSandbox(SubprocessSandbox):
    """Execute code inside a container.

    Inherits the file helpers and path guarding from the subprocess sandbox,
    which mounts its scratch directory into the container, and replaces only
    the execution step.
    """

    name = "container"

    def __init__(
        self,
        spec: SandboxSpec,
        *,
        image: str = DEFAULT_IMAGE,
        runtime: str = DEFAULT_RUNTIME,
        user: str = "1000:1000",
        extra_args: tuple[str, ...] = (),
    ) -> None:
        super().__init__(spec)
        self.image = image
        self.runtime = runtime
        self.user = user
        self.extra_args = tuple(extra_args)

    def __repr__(self) -> str:
        return f"ContainerSandbox(image={self.image!r}, runtime={self.runtime!r})"

    def _argv(self, code: str, language: Language) -> list[str]:
        spec = self.spec
        argv = [
            self.runtime,
            "run",
            "--rm",
            "--interactive",
            # No host network, no privileges, nothing writable outside the
            # mounted scratch directory.
            "--network",
            "none" if spec.network == "none" else "bridge",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=64m",
            "--user",
            self.user,
            "--workdir",
            "/workspace",
            "--volume",
            f"{self.workdir.resolve()}:/workspace:rw",
        ]
        if spec.memory_limit_mb:
            argv += [
                "--memory",
                f"{spec.memory_limit_mb}m",
                "--memory-swap",
                f"{spec.memory_limit_mb}m",
            ]
        for key, value in self._env().items():
            argv += ["--env", f"{key}={value}"]
        argv += [*self.extra_args, self.image]
        argv += ["python", "-I", "-c", code] if language == "python" else ["bash", "-c", code]
        return argv

    def argv_for_test(self, code: str, language: Language = "python") -> list[str]:
        """The exact command this sandbox would run. Public so it can be asserted."""
        return self._argv(code, language)

    async def aexecute(self, code: str, *, language: Language = "python") -> SandboxResult:
        if shutil.which(self.runtime) is None:
            raise SandboxError(
                f"container runtime {self.runtime!r} is not on PATH; "
                "install it or use the subprocess provider"
            )
        started = time.perf_counter()
        timed_out = False
        process = await asyncio.create_subprocess_exec(
            *self._argv(code, language),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.spec.timeout
            )
        except TimeoutError:
            timed_out = True
            process.kill()
            stdout, stderr = await process.communicate()
        result = SandboxResult(
            language=language,
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout.decode("utf-8", "replace"),
            stderr=stderr.decode("utf-8", "replace"),
            duration_ms=(time.perf_counter() - started) * 1000.0,
            timed_out=timed_out,
            code_hash=_hash(code),
        )
        self._record(result, code, language, timed_out)
        return result

    def _record(
        self, result: SandboxResult, code: str, language: Language, timed_out: bool
    ) -> None:
        """Emit SANDBOX_EXECUTED, exactly as the subprocess provider does."""
        from rewyn.core.event import EventType
        from rewyn.core.run import current_run
        from rewyn.models.pricing import compute_unit_cost

        run = current_run()
        if run is None:
            return
        cost = compute_unit_cost("sandbox", self.name, calls=1, seconds=result.duration_ms / 1000.0)
        if cost:
            run.record_cost("sandbox", cost)
        run.emit(
            EventType.SANDBOX_EXECUTED,
            {
                "provider": self.name,
                "image": self.image,
                "runtime": self.runtime,
                "cost": cost,
                "language": language,
                "code_hash": result.code_hash,
                "code": code[:2000],
                "exit_code": result.exit_code,
                "timed_out": timed_out,
                "duration_ms": result.duration_ms,
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:2000],
                "timeout": self.spec.timeout,
                "network": self.spec.network,
            },
        )


class ContainerProvider:
    """Create container sandboxes."""

    name = "container"

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        runtime: str = DEFAULT_RUNTIME,
        user: str = "1000:1000",
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self.image = image
        self.runtime = runtime
        self.user = user
        self.extra_args = tuple(extra_args)

    def __repr__(self) -> str:
        return f"ContainerProvider(image={self.image!r})"

    @property
    def available(self) -> bool:
        """True when the runtime binary is on PATH.

        It does not check that the daemon is running: that costs a process
        spawn and would make a cheap predicate slow. A stopped daemon surfaces
        as a non-zero exit code with the runtime's own message in stderr.
        """
        return shutil.which(self.runtime) is not None

    async def create(self, spec: SandboxSpec) -> Any:
        return ContainerSandbox(
            spec,
            image=self.image,
            runtime=self.runtime,
            user=self.user,
            extra_args=self.extra_args,
        )


def container_available(runtime: str = DEFAULT_RUNTIME) -> bool:
    """Whether a container runtime binary is on PATH (not whether it is running)."""
    return shutil.which(runtime) is not None


def workspace_of(sandbox: ContainerSandbox) -> Path:
    """The host directory mounted into the container."""
    return sandbox.workdir
