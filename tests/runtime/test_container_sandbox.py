"""Container sandboxing (spec §22).

The command is asserted rather than executed: a test that needs a running
daemon and a pulled image is not a unit test. One integration test runs real
code and is skipped unless a daemon is actually reachable.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from rewyn.core.event import EventType
from rewyn.core.run import start_run
from rewyn.runtime.container import ContainerProvider, ContainerSandbox, container_available
from rewyn.runtime.sandbox import SandboxError, SandboxSpec


def sandbox(**spec: object) -> ContainerSandbox:
    return ContainerSandbox(SandboxSpec(**spec), image="python:3.13-slim")


def test_the_provider_creates_a_container_sandbox():
    provider = ContainerProvider(image="python:3.13-slim", runtime="docker")
    assert provider.name == "container"
    assert "python:3.13-slim" in repr(provider)


async def test_the_provider_satisfies_the_sandbox_provider_protocol():
    from rewyn.runtime.sandbox import SandboxProvider

    provider = ContainerProvider()
    assert isinstance(provider, SandboxProvider)
    box = await provider.create(SandboxSpec(timeout=5.0))
    assert isinstance(box, ContainerSandbox)


def test_the_command_drops_every_capability_and_the_network():
    argv = sandbox(timeout=5.0, network="none").argv_for_test("print(1)")
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in argv
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"


def test_allowing_the_network_is_explicit():
    argv = sandbox(timeout=5.0, network="allow").argv_for_test("print(1)")
    assert argv[argv.index("--network") + 1] == "bridge"


def test_a_memory_limit_is_enforced_by_the_kernel_not_a_proxy_variable():
    argv = sandbox(timeout=5.0, memory_limit_mb=256).argv_for_test("print(1)")
    assert argv[argv.index("--memory") + 1] == "256m"
    assert argv[argv.index("--memory-swap") + 1] == "256m", "swap must be capped too"


def test_the_scratch_directory_is_mounted_and_nothing_else():
    box = sandbox(timeout=5.0)
    argv = box.argv_for_test("print(1)")
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--volume"]
    assert mounts == [f"{box.workdir.resolve()}:/workspace:rw"]


def test_the_container_does_not_run_as_root():
    argv = sandbox(timeout=5.0).argv_for_test("print(1)")
    assert argv[argv.index("--user") + 1] == "1000:1000"


def test_bash_and_python_both_dispatch():
    assert sandbox(timeout=5.0).argv_for_test("print(1)", "python")[-4:] == [
        "python",
        "-I",
        "-c",
        "print(1)",
    ]
    assert sandbox(timeout=5.0).argv_for_test("ls", "bash")[-3:] == ["bash", "-c", "ls"]


def test_env_is_passed_but_not_inherited_by_default(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    argv = sandbox(timeout=5.0, env={"DATA_DIR": "/workspace"}).argv_for_test("print(1)")
    passed = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
    assert "DATA_DIR=/workspace" in passed
    assert not any(p.startswith("SECRET_TOKEN=") for p in passed)


def test_extra_runtime_arguments_are_appended_before_the_image():
    box = ContainerSandbox(
        SandboxSpec(timeout=5.0), image="img", runtime="podman", extra_args=("--gpus", "all")
    )
    argv = box.argv_for_test("print(1)")
    assert argv[0] == "podman"
    assert argv[argv.index("img") - 2 : argv.index("img")] == ["--gpus", "all"]


async def test_a_missing_runtime_is_a_clear_error():
    box = ContainerSandbox(SandboxSpec(timeout=5.0), runtime="definitely-not-installed")
    with pytest.raises(SandboxError, match="is not on PATH"):
        await box.aexecute("print(1)")


def test_availability_reports_the_binary_not_the_daemon():
    assert container_available("definitely-not-installed") is False
    assert container_available("docker") is (shutil.which("docker") is not None)


def _daemon_running() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=False)
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _daemon_running(), reason="needs a running container daemon")
async def test_code_really_runs_in_a_container():
    provider = ContainerProvider(image="python:3.13-slim")
    box = await provider.create(SandboxSpec(timeout=120.0, memory_limit_mb=256))
    async with start_run("container", record=False) as run:
        result = await box.aexecute("print(sum(range(10)))")

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "45"
    executed = run.events_of(EventType.SANDBOX_EXECUTED)[0]
    assert executed.payload["provider"] == "container"
    assert executed.payload["image"] == "python:3.13-slim"
