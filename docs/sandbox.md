# Sandbox

## Concept

Letting a model write code and then running that code is useful and
dangerous in proportion. The danger is not usually malice; it is a
data-analysis snippet that reads a file it should not, or loops forever, or
pip-installs something at runtime.

`Sandbox` is the boundary. The default provider runs code in a subprocess
with a timeout, no network, a controlled environment and an optional memory
cap. Execution emits `SANDBOX_EXECUTED` with the exit code, output and
duration, so what ran is in the run log.

The provider is an interface. The subprocess backend is the local default;
a container or microVM backend implements the same protocol.

## Minimal example

```python
from rewyn.runtime import create

result = create().execute("print(sum(range(10)))")
print(result.stdout, result.exit_code)
```

## Production example

```python
from rewyn.runtime import create, sandbox_tool
from rewyn.tools import MaxRiskLevel, RiskLevel

box = create(
    timeout=15.0,
    network="none",
    memory_limit_mb=512,
    env={"DATA_DIR": "/tmp/analysis"},
    inherit_env=[],  # inherit nothing from the parent process
)

result = await box.aexecute(generated_code)
if result.exit_code != 0 or result.timed_out:
    log.warning("sandbox failed", extra={"stderr": result.stderr[:2000]})

# Or give the agent code execution as a tool, under the same policy as any other.
agent = Agent(
    model=...,
    tools=[sandbox_tool(box)],
    permission_policy=MaxRiskLevel(RiskLevel.MEDIUM),
)
```

`inherit_env=[]` is the important line. By default a subprocess inherits the
parent environment, which is where your API keys live.

### Containers

For code you did not write, a subprocess is not enough. The container
provider runs the same protocol with real isolation: every capability
dropped, a read-only root, a kernel-enforced memory cap including swap, a
non-root user, and only the scratch directory mounted.

```python
from rewyn.runtime import ContainerProvider, sandbox

sandbox.set_default_provider(ContainerProvider(image="python:3.13-slim"))
```

It shells out to the runtime rather than taking a client dependency, so
Docker, Podman and nerdctl all work. For genuinely hostile code, point
`runtime` at a microVM runtime such as gVisor or Kata; nothing else changes.

## API reference

`rewyn/runtime/sandbox.py` for `Sandbox`, `SandboxSpec`, `SandboxResult`,
`SandboxProvider`, `SubprocessSandbox`, `create`, `acreate` and
`sandbox_tool`.
`rewyn/runtime/container.py` for `ContainerProvider` and
`ContainerSandbox`.

## Failure modes

**The code times out.** `SandboxResult` reports it rather than raising. Read
`exit_code` and `timed_out` instead of assuming success.

**Network calls fail.** `network="none"` is the default, deliberately. Set
`network="allow"` only when the task genuinely needs it, and understand that
you have removed the main containment.

**A subprocess is not a security boundary.** It bounds accidents well and
determined escapes poorly. Use `ContainerProvider` for untrusted code.

**The container runtime is installed but nothing runs.** `available` checks
that the binary is on PATH, not that the daemon is up. A stopped daemon
surfaces as a non-zero exit code with the runtime's message in stderr.

**Secrets leak into the sandbox.** Set `inherit_env` explicitly. The default
subprocess environment is not empty.

**Output is truncated.** Large stdout is capped to keep events reasonable.
Have the code write to a file and return a path.
