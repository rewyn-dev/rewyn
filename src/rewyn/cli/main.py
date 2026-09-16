"""The ``rewyn`` command line interface (spec §48)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from rewyn import __version__
from rewyn.core.event import summarize_payload
from rewyn.core.settings import get_settings
from rewyn.storage.local import LocalStore, RunNotFoundError

app = typer.Typer(
    name="rewyn",
    help="Build. Run. Replay. Improve AI.",
    no_args_is_help=True,
    add_completion=False,
)


def _store() -> LocalStore:
    return LocalStore(get_settings().home)


def _fail(message: str) -> None:
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(code=1)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"rewyn {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    return None


@app.command()
def init(
    path: Annotated[Path | None, typer.Argument(help="Directory for .rewyn/ state.")] = None,
) -> None:
    """Initialise local Rewyn state (``.rewyn/``)."""
    home = (path / ".rewyn") if path else get_settings().home
    created = LocalStore(home).initialize()
    typer.echo(f"initialised {created}")


@app.command()
def quickstart(
    path: Annotated[
        Path | None, typer.Argument(help="Where to write the example. Default: quickstart.py")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Write a runnable first agent, and say what to do with it.

    The file runs as written -- no API key, no placeholder to fill in -- and
    leaves a recorded run behind, which is what every screen in the console
    is derived from (UI spec §47).
    """
    from rewyn.ui.onboarding import FIRST_RUN

    target = path or Path("quickstart.py")
    if target.exists() and not force:
        _fail(f"{target} already exists. Pass --force to overwrite it.")
        return
    target.write_text(FIRST_RUN, encoding="utf-8")
    typer.secho(f"wrote {target}", fg=typer.colors.GREEN)
    typer.echo("\nnext:")
    # Aligned on the longest command, so the comments line up whatever the
    # target is called.
    commands = (f"  python {target}", "  rewyn ui")
    width = max(len(c) for c in commands) + 2
    typer.echo(f"{commands[0]:<{width}}# records a run into .rewyn/")
    typer.echo(f"{commands[1]:<{width}}# read what it did")


@app.command()
def demo(
    action: Annotated[
        str, typer.Argument(help="'load' to seed the demo project, 'clear' to remove it.")
    ] = "load",
) -> None:
    """Load or remove a demo project, so the console has something to show.

    Every run it writes is tagged ``demo``; ``clear`` removes exactly those
    and nothing else, so exploring cannot be confused with your own data.
    """
    from rewyn.ui import demo as demo_project

    store = _store()
    if action == "clear":
        removed = demo_project.clear(store)
        typer.secho(f"removed {removed} demo runs", fg=typer.colors.GREEN)
        return
    if action != "load":
        _fail(f"unknown action {action!r}; use 'load' or 'clear'")
        return
    seeded = demo_project.load(store)
    typer.secho(
        f"loaded {seeded.runs} demo runs across {len(seeded.agents)} agents "
        f"({seeded.failures} failing, on purpose)",
        fg=typer.colors.GREEN,
    )
    typer.echo("  rewyn ui                 # read them")
    typer.echo("  rewyn demo clear         # remove them again")


@app.command()
def runs(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Maximum runs to list.")] = 20,
    project: Annotated[str | None, typer.Option(help="Filter by project.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List recorded runs, newest first."""
    manifests = _store().list_runs(limit=limit, project=project)
    if as_json:
        typer.echo(json.dumps([m.model_dump(mode="json") for m in manifests], indent=2))
        return
    if not manifests:
        typer.echo("no runs recorded yet")
        return
    typer.echo(f"{'RUN ID':<32} {'STATUS':<10} {'NAME':<20} {'EVENTS':>6} {'COST':>10}  STARTED")
    for m in manifests:
        typer.echo(
            f"{m.id:<32} {m.status.value:<10} {m.name[:20]:<20} {m.event_count:>6} "
            f"{m.cost.total:>10.4f}  {m.started_at.isoformat(timespec='seconds')}"
        )


@app.command()
def inspect(
    run: Annotated[str, typer.Argument(help="Run id, unique prefix, or 'latest'.")],
    events: Annotated[bool, typer.Option(help="Show the event timeline.")] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Show a run's manifest and event timeline."""
    store = _store()
    try:
        run_id = store.resolve_run_id(run)
        manifest = store.read_manifest(run_id)
        run_events = store.read_events(run_id)
    except RunNotFoundError as exc:
        _fail(str(exc))
        return
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "events": [e.to_record() for e in run_events],
                },
                indent=2,
            )
        )
        return
    typer.echo(f"run       {manifest.id}")
    typer.echo(f"name      {manifest.name}")
    typer.echo(f"project   {manifest.project}")
    typer.echo(f"status    {manifest.status.value}")
    typer.echo(f"started   {manifest.started_at.isoformat(timespec='seconds')}")
    if manifest.duration_ms is not None:
        typer.echo(f"duration  {manifest.duration_ms:.0f} ms")
    usage = manifest.usage
    typer.echo(
        f"usage     {usage.input_tokens} in / {usage.output_tokens} out tokens, "
        f"{usage.model_calls} model calls, {usage.tool_calls} tool calls"
    )
    typer.echo(f"cost      {manifest.cost.total:.6f} {manifest.cost.currency}")
    if manifest.dependencies:
        typer.echo("dependencies")
        for dep in manifest.dependencies:
            version = "" if dep.version == "unversioned" else f" (v{dep.version})"
            typer.echo(f"  - {dep.kind}: {dep.name}{version}")
    if manifest.error:
        typer.secho(f"error     {manifest.error}", fg=typer.colors.RED)
    if events:
        typer.echo("events")
        for event in run_events:
            summary = summarize_payload(event.payload, limit=80)
            stamp = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
            typer.echo(f"  {event.seq:>4} {stamp} {event.type.value}")
            for key, value in summary.items():
                typer.echo(f"         {key}: {value}")


@app.command()
def export(
    run: Annotated[str, typer.Argument(help="Run id, unique prefix, or 'latest'.")],
    to: Annotated[Path | None, typer.Option("--to", help="Destination directory.")] = None,
    zip_bundle: Annotated[bool, typer.Option("--zip", help="Produce a zip archive.")] = False,
) -> None:
    """Export a run as a portable bundle."""
    from rewyn.replay.export import export_run

    try:
        path = export_run(run, to, store=_store(), archive=zip_bundle)
    except RunNotFoundError as exc:
        _fail(str(exc))
        return
    typer.echo(f"exported {path}")


@app.command("import")
def import_cmd(
    source: Annotated[Path, typer.Argument(help="Bundle directory or zip file.")],
    overwrite: Annotated[bool, typer.Option(help="Replace an existing run.")] = False,
) -> None:
    """Import a run bundle into local storage."""
    from rewyn.replay.export import BundleError, import_run

    try:
        run_id = import_run(source, store=_store(), overwrite=overwrite)
    except BundleError as exc:
        _fail(str(exc))
        return
    typer.echo(f"imported {run_id}")


@app.command()
def doctor() -> None:
    """Check the local installation and environment."""
    from rewyn.storage.remote import RemoteStore

    settings = get_settings()
    problems = 0

    def report(ok: bool, label: str, detail: str = "") -> None:
        nonlocal problems
        mark = "ok " if ok else "!! "
        if not ok:
            problems += 1
        typer.echo(f"{mark} {label:<28} {detail}")

    report(sys.version_info >= (3, 11), "python", sys.version.split()[0])
    report(True, "rewyn", __version__)
    home = settings.home
    try:
        LocalStore(home).initialize()
        writable = os.access(home, os.W_OK)
    except OSError:
        writable = False
    report(writable, "home", str(home.resolve()))
    report(True, "recording", "on" if settings.recording else "off (REWYN_RECORDING)")
    report(settings.redaction, "redaction", "on" if settings.redaction else "OFF")
    for package, extra in (
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("google.genai", "google"),
        ("mcp", "mcp"),
        ("opentelemetry", "otel"),
        ("httpx", "remote"),
    ):
        try:
            installed = importlib.util.find_spec(package) is not None
        except ModuleNotFoundError:
            # find_spec imports the parent of a dotted name, so "google.genai"
            # raises rather than returning None when google is absent -- which
            # is every install that did not ask for the extra.
            installed = False
        detail = "installed" if installed else f"not installed (pip install 'rewyn[{extra}]')"
        typer.echo(f"{'ok ' if installed else '-- '} {package:<28} {detail}")
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        present = bool(os.environ.get(env))
        typer.echo(f"{'ok ' if present else '-- '} {env:<28} {'set' if present else 'not set'}")
    from rewyn.storage.remote import Credentials

    credentials = Credentials.load()
    if not credentials.configured:
        typer.echo(f"{'-- ':<4}{'cloud':<28} local-only (rewyn login to connect)")
    else:
        reachable = RemoteStore(credentials).ping() if importlib.util.find_spec("httpx") else False
        detail = credentials.endpoint + ("" if reachable else " (unreachable)")
        typer.echo(f"{'ok ' if reachable else '-- '} {'cloud':<28} {detail}")
    if problems:
        _fail(f"{problems} problem(s) found")
    typer.echo("all checks passed")


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Port to serve on.")] = 4400,
    host: Annotated[str, typer.Option(help="Interface to bind. Loopback only.")] = "127.0.0.1",
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the console in a browser.")
    ] = True,
) -> None:
    """Serve the local console at http://127.0.0.1:4400 (UI spec §44).

    No account, no key, no cloud: it reads the runs already in ``.rewyn/``.
    """
    try:
        from rewyn.ui.server import serve
    except ImportError:
        _fail(
            "the console needs the 'ui' extra:\n"
            "  pip install 'rewyn[ui]'   (or: uv sync --all-extras)"
        )
        return
    store = _store()
    store.initialize()
    url = f"http://{host}:{port}"
    typer.secho(f"Rewyn console  {url}", fg=typer.colors.CYAN)
    typer.echo(f"reading {store.home.resolve()}  (ctrl-c to stop)")
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    serve(host=host, port=port, store=store)


skills_app = typer.Typer(help="Discover and inspect skills.", invoke_without_command=True)
app.add_typer(skills_app, name="skills")


@skills_app.callback()
def skills_root(
    ctx: typer.Context,
    path: Annotated[
        list[Path] | None, typer.Option("--path", "-p", help="Directories to search.")
    ] = None,
) -> None:
    """List discovered skills (default) or use a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    from rewyn.skills.discovery import discover_skills

    errors: list[str] = []
    skills = discover_skills(path, errors=errors)
    if not skills:
        typer.echo("no skills found")
    for skill in skills:
        typer.echo(f"{skill.name:<28} v{skill.version:<6} {skill.description[:60]}")
        if skill.path:
            typer.echo(f"{'':<28} {skill.path}")
    for error in errors:
        typer.secho(f"!! {error}", err=True, fg=typer.colors.YELLOW)


def _is_skill_path(name: str) -> bool:
    """Whether the argument names a place rather than a skill.

    A bare name must not be treated as a path just because a directory of
    that name happens to sit in the working directory: ``rewyn skills show
    demo`` should find the skill called "demo", not a local ``demo/`` folder
    that has nothing to do with skills.
    """
    candidate = Path(name)
    if candidate.is_file() and candidate.suffix == ".md":
        return True
    return candidate.is_dir() and (candidate / "SKILL.md").exists()


@skills_app.command("show")
def skills_show(
    name: Annotated[str, typer.Argument(help="Skill name or path to a skill directory.")],
    path: Annotated[
        list[Path] | None, typer.Option("--path", "-p", help="Directories to search.")
    ] = None,
) -> None:
    """Show a skill's metadata and instructions."""
    from rewyn.skills.discovery import discover_skills
    from rewyn.skills.loader import SkillLoadError, load_skill

    skill = None
    if _is_skill_path(name):
        try:
            skill = load_skill(name)
        except SkillLoadError as exc:
            _fail(str(exc))
    else:
        skill = next((s for s in discover_skills(path) if s.name == name), None)
    if skill is None:
        _fail(f"skill {name!r} not found")
        return
    typer.echo(f"name         {skill.name}")
    typer.echo(f"version      {skill.version}")
    typer.echo(f"description  {skill.description}")
    typer.echo(f"fingerprint  {skill.fingerprint()}")
    if skill.allowed_tools:
        typer.echo(f"tools        {', '.join(skill.allowed_tools)}")
    if skill.resources:
        typer.echo(f"resources    {', '.join(skill.resources)}")
    if skill.scripts:
        typer.echo(f"scripts      {', '.join(skill.scripts)}")
    typer.echo("")
    typer.echo(skill.instructions)


mcp_app = typer.Typer(help="Inspect configured MCP servers.", invoke_without_command=True)
app.add_typer(mcp_app, name="mcp")


@mcp_app.callback()
def mcp_root(
    ctx: typer.Context,
    config: Annotated[
        list[Path] | None, typer.Option("--config", "-c", help="MCP config files.")
    ] = None,
) -> None:
    """List configured MCP servers (default) or use a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    from rewyn.mcp.discovery import discover_configs

    configs = discover_configs(config)
    if not configs:
        typer.echo("no MCP servers configured (looked for mcp.json)")
        return
    for server in configs:
        target = (
            server.url
            if server.transport == "http"
            else " ".join([server.command or "", *server.args])
        )
        typer.echo(f"{server.name:<24} {server.transport:<6} {target}")


@mcp_app.command("tools")
def mcp_tools(
    name: Annotated[str, typer.Argument(help="Configured server name.")],
    config: Annotated[
        list[Path] | None, typer.Option("--config", "-c", help="MCP config files.")
    ] = None,
) -> None:
    """Connect to a configured server and list its tools."""
    from rewyn.mcp.client import MCPClient
    from rewyn.mcp.discovery import discover_configs

    server = next((s for s in discover_configs(config) if s.name == name), None)
    if server is None:
        _fail(f"MCP server {name!r} is not configured")
        return
    with MCPClient(server) as client:
        for tool in client.tools():
            typer.echo(f"{tool.name:<32} {tool.risk_level.value:<8} {tool.description[:60]}")


@dataclass
class _AgentSummary:
    runs: int = 0
    versions: set[str] = field(default_factory=set)
    last: datetime | None = None
    ids: list[tuple[str, str, datetime]] = field(default_factory=list)


@app.command()
def agents(
    name: Annotated[str | None, typer.Argument(help="Show runs for one agent.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Runs to scan.")] = 200,
) -> None:
    """List agents seen in recorded runs, or the runs of one agent."""
    manifests = _store().list_runs(limit=limit)
    seen: dict[str, _AgentSummary] = {}
    for manifest in manifests:
        for dep in manifest.dependencies:
            if dep.kind != "agent" or (name is not None and dep.name != name):
                continue
            entry = seen.setdefault(dep.name, _AgentSummary())
            entry.runs += 1
            entry.versions.add(dep.version)
            if entry.last is None or manifest.started_at > entry.last:
                entry.last = manifest.started_at
            entry.ids.append((manifest.id, manifest.status.value, manifest.started_at))
    if not seen:
        typer.echo("no agent runs recorded" if name is None else f"no runs for agent {name!r}")
        return
    if name is None:
        typer.echo(f"{'AGENT':<28} {'RUNS':>5}  {'VERSIONS':<12} LAST RUN")
        for agent_name, entry in sorted(seen.items()):
            last = entry.last.isoformat(timespec="seconds") if entry.last else ""
            versions = ", ".join(sorted(entry.versions))
            typer.echo(f"{agent_name:<28} {entry.runs:>5}  {versions:<12} {last}")
        return
    for run_id, status, started in seen[name].ids:
        typer.echo(f"{run_id:<32} {status:<10} {started.isoformat(timespec='seconds')}")


def _load_object(reference: str) -> Any:
    """Resolve ``package.module:attr`` or ``./path/to/file.py:attr`` to an object."""
    module_ref, _, attribute = reference.partition(":")
    if not attribute:
        _fail(f"{reference!r} must be written as 'module:attribute'")
    path = Path(module_ref)
    if path.suffix == ".py":
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            _fail(f"cannot import {path}")
            raise typer.Exit(code=1)
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(path.stem, module)
        spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(module_ref)
        except ImportError as exc:
            _fail(f"cannot import {module_ref!r}: {exc}")
            raise typer.Exit(code=1) from exc
    try:
        return getattr(module, attribute)
    except AttributeError:
        _fail(f"{module_ref!r} has no attribute {attribute!r}")
        raise typer.Exit(code=1) from None


def _eval_dataset(dataset: Any, evaluators: list[Any], *, as_json: bool) -> None:
    """Score every run a dataset was captured from, without re-executing it."""
    from rewyn.evaluation.evaluator import evaluate
    from rewyn.replay.recorder import RecordedRun

    scored: list[dict[str, Any]] = []
    for item in dataset:
        if item.source_run_id is None:
            continue
        try:
            recorded = RecordedRun.load(item.source_run_id, store=_store())
        except RunNotFoundError:
            scored.append({"item": item.id, "error": "source run is not in local storage"})
            continue
        result = evaluate(recorded, evaluators, expected=item.expected)
        scored.append(
            {
                "item": item.id,
                "run_id": recorded.id,
                "passed": result.passed,
                "value": round(result.value, 4),
                "failures": [s.evaluator for s in result.failures()],
            }
        )
    if as_json:
        typer.echo(json.dumps({"dataset": dataset.name, "cases": scored}, indent=2, default=str))
    else:
        typer.echo(f"dataset {dataset.name} v{dataset.version}")
        if not scored:
            typer.echo("no captured runs to score (add items with 'rewyn datasets add')")
        for case in scored:
            mark = "ok " if case.get("passed") else "!! "
            detail = case.get("error") or ", ".join(case.get("failures") or []) or "all passed"
            typer.echo(f"{mark} {case['item']:<28} {detail}")
    if any(not c.get("passed") for c in scored) or not scored:
        raise typer.Exit(code=1)


def _load_evaluators(references: list[str] | None) -> list[Any]:
    """Load evaluators from ``module:attr`` references (a list attribute is expanded)."""
    from rewyn.evaluation.evaluator import as_evaluator

    loaded: list[Any] = []
    for reference in references or []:
        found = _load_object(reference)
        candidates = found if isinstance(found, list | tuple) else [found]
        loaded.extend(as_evaluator(c) for c in candidates)
    return loaded


@app.command()
def replay(
    run: Annotated[str, typer.Argument(help="Run id, unique prefix, or 'latest'.")],
    model: Annotated[
        str, typer.Option("--model", help="'recorded', or a model string to re-issue prompts to.")
    ] = "recorded",
    tools: Annotated[str, typer.Option("--tools", help="'recorded' or 'live'.")] = "recorded",
    context: Annotated[str, typer.Option("--context", help="'original' or 'live'.")] = "original",
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Replay a recorded run.

    With no ``--model`` the recording is reproduced exactly. With a model
    string every recorded prompt is re-issued against that model instead.
    """
    from rewyn.replay.recorder import ReplayError
    from rewyn.replay.replay import replay as run_replay

    try:
        result = run_replay(run, model=model, tools=tools, context=context, store=_store())
    except (RunNotFoundError, ReplayError) as exc:
        _fail(str(exc))
        return
    if as_json:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
        return
    typer.echo(f"replay    {result.run_id}")
    typer.echo(f"of        {result.original_run_id}")
    typer.echo(f"mode      {result.mode.value}")
    typer.echo(f"model     {result.model_mode}   tools {result.tool_mode}")
    typer.echo(f"identical {result.identical}   faithful {result.faithful}")
    typer.echo(f"swapped   {result.substitutions} recorded response(s)")
    if result.cost_delta:
        typer.echo(f"cost      {result.cost_delta:+.6f} vs the recording")
    for prompt in result.changed_prompts:
        typer.echo(f"  call {prompt.index}: {prompt.original_text[:60]!r}")
        typer.echo(f"       -> {prompt.replay_text[:60]!r}")
    for mismatch in result.mismatches:
        typer.secho(
            f"!! {mismatch.kind} {mismatch.index}: {mismatch.detail}", fg=typer.colors.YELLOW
        )
    if result.error:
        _fail(result.error)


@app.command()
def diff(
    run_a: Annotated[str, typer.Argument(help="Baseline run id, prefix, or 'latest'.")],
    run_b: Annotated[str, typer.Argument(help="Comparison run id or prefix.")],
    dimension: Annotated[
        list[str] | None, typer.Option("--dimension", "-d", help="Limit to these dimensions.")
    ] = None,
    explain_changes: Annotated[
        bool, typer.Option("--explain/--no-explain", help="Show causal hypotheses.")
    ] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Compare two runs across the model, prompt, context, tool, cost and output dimensions."""
    from rewyn.replay.diff import Dimension
    from rewyn.replay.diff import diff as diff_runs

    try:
        wanted = [Dimension(d) for d in dimension] if dimension else None
    except ValueError as exc:
        _fail(f"{exc}; valid dimensions: {', '.join(d.value for d in Dimension)}")
        return
    try:
        report = diff_runs(run_a, run_b, store=_store(), dimensions=wanted)
    except RunNotFoundError as exc:
        _fail(str(exc))
        return
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, default=str))
        return
    typer.echo(f"a {report.run_a}")
    typer.echo(f"b {report.run_b}")
    if report.identical:
        typer.echo("no differences")
        return
    typer.echo(f"\ndifferences ({len(report.differences)})")
    for difference in report.differences:
        typer.echo(f"  {difference.describe()}")
    if explain_changes and report.explanations:
        typer.echo(f"\npossible explanations ({len(report.explanations)}) - hypotheses, not facts")
        for explanation in report.explanations:
            typer.echo(f"  {explanation.describe()}")


@app.command("eval")
def eval_cmd(
    target: Annotated[str, typer.Argument(help="Dataset name, or a run id, prefix or 'latest'.")],
    evaluator: Annotated[
        list[str] | None,
        typer.Option("--evaluator", "-e", help="module:attribute holding an evaluator."),
    ] = None,
    expect: Annotated[
        str | None, typer.Option("--expect", help="Expected output for built-in metrics.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Score a dataset's runs, or one recorded run, with evaluators (spec §48).

    A dataset name scores every run the dataset was captured from, which is
    how you check a stored set without re-executing anything. A run id scores
    that run alone.
    """
    from rewyn.evaluation.dataset import Dataset, DatasetError
    from rewyn.evaluation.evaluator import evaluate
    from rewyn.evaluation.metrics import exact_match, no_errors, not_empty
    from rewyn.replay.recorder import RecordedRun

    evaluators = _load_evaluators(evaluator)
    if not evaluators:
        evaluators = [no_errors(), not_empty()]
        if expect is not None:
            evaluators.insert(0, exact_match())

    try:
        dataset = Dataset.load(target)
    except DatasetError:
        dataset = None
    if dataset is not None:
        _eval_dataset(dataset, evaluators, as_json=as_json)
        return

    try:
        recorded = RecordedRun.load(target, store=_store())
    except RunNotFoundError as exc:
        _fail(str(exc))
        return
    result = evaluate(recorded, evaluators, expected=expect)
    if as_json:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
        return
    typer.echo(f"run    {recorded.id}")
    for score in result.scores:
        mark = "ok " if score.passed else "!! "
        detail = f"  {score.reason}" if score.reason else ""
        typer.echo(f"{mark} {score.evaluator:<28} {score.value:>8.3f}{detail}")
    typer.echo(f"\nweighted {result.value:.3f}  {'PASS' if result.passed else 'FAIL'}")
    if not result.passed:
        raise typer.Exit(code=1)


@app.command()
def test(
    dataset: Annotated[str, typer.Argument(help="Dataset name or path to a dataset JSON file.")],
    target: Annotated[
        str, typer.Option("--target", "-t", help="module:attribute holding the agent to test.")
    ],
    evaluator: Annotated[
        list[str] | None,
        typer.Option("--evaluator", "-e", help="module:attribute holding an evaluator."),
    ] = None,
    baseline: Annotated[
        str | None, typer.Option("--baseline", help="Report id, or 'latest' for the last run.")
    ] = None,
    min_success: Annotated[
        float | None, typer.Option("--min-success", help="Fail below this success rate (0-1).")
    ] = None,
    max_cost_per_run: Annotated[
        float | None, typer.Option("--max-cost", help="Fail above this average cost per run.")
    ] = None,
    concurrency: Annotated[int, typer.Option("--concurrency", "-j", help="Cases in flight.")] = 1,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Run a dataset as a regression test and gate on the result (spec §31, §32)."""
    from rewyn.evaluation.dataset import Dataset, DatasetError
    from rewyn.evaluation.metrics import no_errors, not_empty
    from rewyn.evaluation.regression import RegressionError, Thresholds, run_regression

    try:
        data = Dataset.load(dataset)
    except DatasetError as exc:
        _fail(str(exc))
        return
    evaluators = _load_evaluators(evaluator) or [no_errors(), not_empty()]
    thresholds = None
    if min_success is not None or max_cost_per_run is not None:
        thresholds = Thresholds(min_success_rate=min_success, max_cost_per_run=max_cost_per_run)
    try:
        report = run_regression(
            data,
            _load_object(target),
            evaluators=evaluators,
            thresholds=thresholds,
            baseline=baseline,
            concurrency=concurrency,
        )
    except RegressionError as exc:
        _fail(str(exc))
        return
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, default=str))
    else:
        typer.echo(report.render())
        typer.echo(f"\nreport {report.id}")
        for case in report.failures[:10]:
            reason = case.error or ", ".join(s.reason for s in case.scores if not s.passed)
            typer.secho(f"  FAIL {case.item_id}: {reason[:90]}", fg=typer.colors.RED)
    if not report.passed:
        raise typer.Exit(code=1)


datasets_app = typer.Typer(
    help="Create and inspect evaluation datasets.", invoke_without_command=True
)
app.add_typer(datasets_app, name="datasets")


@datasets_app.callback()
def datasets_root(ctx: typer.Context) -> None:
    """List local datasets (default) or use a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    from rewyn.evaluation.dataset import list_datasets

    found = list_datasets()
    if not found:
        typer.echo("no datasets yet (create one with: rewyn datasets add <name> --run latest)")
        return
    typer.echo(f"{'DATASET':<32} {'VERSION':<8} {'ITEMS':>6}  UPDATED")
    for data in found:
        typer.echo(
            f"{data.name:<32} {data.version:<8} {len(data):>6}  "
            f"{data.updated_at.isoformat(timespec='seconds')}"
        )


@datasets_app.command("add")
def datasets_add(
    name: Annotated[str, typer.Argument(help="Dataset name.")],
    run: Annotated[
        list[str], typer.Option("--run", "-r", help="Run id, prefix, or 'latest' to capture.")
    ],
    expected: Annotated[
        str | None, typer.Option("--expected", help="Override the expected output.")
    ] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", help="Tags for the new items.")] = None,
) -> None:
    """Save recorded runs as dataset examples (spec §30)."""
    from rewyn.evaluation.dataset import Dataset, DatasetError

    data = Dataset.load_or_create(name)
    try:
        for run_id in run:
            item = data.add_run(run_id, expected=expected, tags=tag or (), store=_store())
            typer.echo(f"added {item.id} from {item.source_run_id}")
    except (RunNotFoundError, DatasetError) as exc:
        _fail(str(exc))
        return
    path = data.save()
    typer.echo(f"{name} now has {len(data)} item(s) -> {path}")


@datasets_app.command("show")
def datasets_show(
    name: Annotated[str, typer.Argument(help="Dataset name or path.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Items to print.")] = 20,
) -> None:
    """Show a dataset's items."""
    from rewyn.evaluation.dataset import Dataset, DatasetError

    try:
        data = Dataset.load(name)
    except DatasetError as exc:
        _fail(str(exc))
        return
    typer.echo(f"name        {data.name}")
    typer.echo(f"version     {data.version}")
    typer.echo(f"items       {len(data)}")
    typer.echo(f"fingerprint {data.fingerprint()}")
    for item in data.items[:limit]:
        typer.echo(f"\n  {item.id}  {' '.join(item.tags)}")
        typer.echo(f"    input    {item.input_text[:90]}")
        if item.expected is not None:
            typer.echo(f"    expected {str(item.expected)[:90]}")


@app.command()
def manifest(
    application: Annotated[str, typer.Argument(help="Application name for the manifest.")],
    run: Annotated[
        list[str] | None, typer.Option("--run", "-r", help="Runs to roll up (default: latest).")
    ] = None,
    version: Annotated[str, typer.Option("--version", "-v", help="Release version.")] = "0.0.0",
    save: Annotated[bool, typer.Option("--save", help="Write to .rewyn/manifests/.")] = False,
    graph: Annotated[
        bool, typer.Option("--graph", help="Print the dependency tree instead.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Produce the AI behavior manifest for one or more runs (spec §34, §36)."""
    from rewyn.core.manifest import BehaviorManifest, DependencyGraph, ManifestError

    run_ids = run or ["latest"]
    try:
        if graph:
            tree = DependencyGraph.from_runs(run_ids, store=_store())
            if as_json:
                typer.echo(json.dumps(tree.model_dump(mode="json"), indent=2, default=str))
            else:
                typer.echo(tree.render())
            return
        built = BehaviorManifest.from_runs(application, run_ids, version=version, store=_store())
    except (RunNotFoundError, ManifestError) as exc:
        _fail(str(exc))
        return
    if as_json:
        typer.echo(json.dumps(built.model_dump(mode="json"), indent=2, default=str))
    else:
        typer.echo(built.render())
    if save:
        typer.echo(f"\nsaved {built.save()}")


@app.command()
def drift(
    baseline: Annotated[str, typer.Argument(help="Baseline run id, or a saved manifest name.")],
    current: Annotated[str, typer.Argument(help="Current run id, or a saved manifest name.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Detect dependency drift between two runs or two saved manifests (spec §35)."""
    from rewyn.core.manifest import (
        BehaviorManifest,
        ManifestError,
        detect_drift,
        detect_run_drift,
    )

    store = _store()
    try:
        if store.exists(baseline) or baseline == "latest":
            report = detect_run_drift(baseline, current, store=store)
        else:
            report = detect_drift(BehaviorManifest.load(baseline), BehaviorManifest.load(current))
    except (RunNotFoundError, ManifestError) as exc:
        _fail(str(exc))
        return
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, default=str))
        return
    typer.echo(report.render())
    if report.silent_drift:
        typer.secho(
            "\nbehaviour changed with no dependency change - suspect a provider update, "
            "changed external data, or model nondeterminism",
            fg=typer.colors.YELLOW,
        )


@app.command()
def login(
    endpoint: Annotated[
        str, typer.Option("--endpoint", "-e", help="Rewyn API base URL.")
    ] = "https://api.rewyn.dev",
    key: Annotated[
        str | None, typer.Option("--key", "-k", help="API key. Prompted for when omitted.")
    ] = None,
    verify: Annotated[
        bool, typer.Option("--verify/--no-verify", help="Check the key against the API.")
    ] = True,
) -> None:
    """Store credentials for a Rewyn API (spec §45).

    The key is written to ``.rewyn/credentials.json`` with owner-only
    permissions and registered with the redactor so it can never appear in a
    recorded event.
    """
    from rewyn.storage.remote import Credentials, RemoteError, RemoteStore

    if verify and importlib.util.find_spec("httpx") is None:
        _fail("verifying a key needs httpx: pip install 'rewyn[remote]' (or use --no-verify)")
        return
    secret = key or typer.prompt("API key", hide_input=True)
    credentials = Credentials(endpoint=endpoint, api_key=secret.strip())
    if verify:
        try:
            identity = RemoteStore(credentials).verify()
        except RemoteError as exc:
            _fail(f"could not verify the key: {exc}")
            return
        credentials = credentials.model_copy(update={"project": identity.get("project", "")})
        typer.echo(f"authenticated as project {identity.get('project')!r}")
    path = credentials.save()
    typer.echo(f"credentials saved to {path}")


@app.command()
def logout() -> None:
    """Forget the stored Rewyn credentials."""
    from rewyn.storage.remote import Credentials

    if Credentials.clear():
        typer.echo("credentials removed")
        return
    typer.echo("no stored credentials")


@app.command()
def sync(
    run: Annotated[
        list[str] | None, typer.Option("--run", "-r", help="Specific runs (default: all).")
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit", "-n", help="Newest runs to send.")] = None,
    datasets: Annotated[bool, typer.Option("--datasets", help="Also send local datasets.")] = False,
    evaluations: Annotated[
        bool, typer.Option("--evaluations", help="Also send local regression reports.")
    ] = False,
    pull: Annotated[
        str | None, typer.Option("--pull", help="Download one run id into local storage.")
    ] = None,
    status: Annotated[
        bool, typer.Option("--status", help="Show what is configured and queued, send nothing.")
    ] = False,
) -> None:
    """Upload local runs to the configured Rewyn API (spec §45).

    Failures never raise: anything that does not upload is queued and
    retried on the next sync.
    """
    from rewyn.storage.remote import Credentials, RemoteError, RemoteStore, SyncQueue

    credentials = Credentials.load()
    if status:
        queued = SyncQueue().read()
        for label, value in credentials.redacted().items():
            typer.echo(f"{label:<10} {value or '(not set)'}")
        typer.echo(f"{'queued':<10} {len(queued)}")
        if queued:
            typer.echo("  " + ", ".join(queued[:10]))
        return
    if not credentials.configured:
        _fail("no API key configured; run 'rewyn login' first")
        return
    if importlib.util.find_spec("httpx") is None:
        _fail("syncing needs httpx: pip install 'rewyn[remote]'")
        return

    store = RemoteStore(credentials)
    if pull is not None:
        try:
            run_id = store.pull_run(pull)
        except RemoteError as exc:
            _fail(str(exc))
            return
        typer.echo(f"pulled {run_id}")
        return

    result = store.push_runs(run or None, limit=limit)
    typer.echo(
        f"uploaded {len(result.uploaded)}, replaced {len(result.replaced)}, "
        f"failed {len(result.failed)}"
    )
    if datasets:
        from rewyn.evaluation.dataset import list_datasets

        for dataset in list_datasets():
            try:
                store.push_dataset(dataset)
            except RemoteError as exc:
                result.errors.append(f"dataset {dataset.name}: {exc}")
                continue
            typer.echo(f"dataset {dataset.name} v{dataset.version}")
    if evaluations:
        from rewyn.evaluation.regression import RegressionReport

        for report in RegressionReport.history():
            try:
                store.push_report(report)
            except RemoteError as exc:
                result.errors.append(f"report {report.id}: {exc}")
                continue
            typer.echo(f"report {report.id}")
    for error in result.errors:
        typer.secho(f"!! {error}", err=True, fg=typer.colors.YELLOW)
    if result.queued:
        typer.echo(f"{result.queued} run(s) queued for the next sync")
    if not result.ok:
        raise typer.Exit(code=1)


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
