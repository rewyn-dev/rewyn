"""The documentation must describe the SDK that exists.

Docs drift silently: a rename lands, the tests pass, and a page keeps telling
people to import something that is gone. These tests parse every Python block
in `docs/` and check it against the real package.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import re

import pytest

DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs"
SPEC_AND_PLAN = {
    "DEVELOPMENT_PLAN.md",
    "UI_DEVELOPMENT_PLAN.md",
    "Rewyn — Universal AI Engineering SDK & Cloud Platform.md",
    "Rewyn — UI & Cloud Console Product Specification.md",
}
# Operational pages are organised by topic rather than around one worked
# example. They still owe the reader a concept, an API pointer and the
# failure modes.
OPERATIONAL = {"production.md", "security.md", "cloud.md"}
FENCE = re.compile(r"^```python\n(.*?)^```", re.M | re.S)


def pages() -> list[pathlib.Path]:
    return sorted(p for p in DOCS.glob("*.md") if p.name not in SPEC_AND_PLAN)


def blocks(page: pathlib.Path) -> list[str]:
    return FENCE.findall(page.read_text(encoding="utf-8"))


@pytest.mark.parametrize("page", pages(), ids=lambda p: p.name)
def test_every_python_block_parses(page: pathlib.Path) -> None:
    """A snippet that is not valid Python cannot be copied and run."""
    for index, block in enumerate(blocks(page)):
        try:
            ast.parse(block)
        except SyntaxError as exc:
            pytest.fail(f"{page.name} block {index}: {exc}")


@pytest.mark.parametrize("page", pages(), ids=lambda p: p.name)
def test_every_documented_import_resolves(page: pathlib.Path) -> None:
    """Every `from rewyn... import X` in the docs must actually import."""
    missing: list[str] = []
    for block in blocks(page):
        for node in ast.walk(ast.parse(block)):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("rewyn"):
                continue
            try:
                module = importlib.import_module(node.module)
            except ImportError as exc:
                missing.append(f"{node.module}: {exc}")
                continue
            missing.extend(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if not hasattr(module, alias.name)
            )
    assert not missing, f"{page.name} references symbols that do not exist: {missing}"


def _imported_symbols(tree: ast.AST) -> dict[str, object]:
    """Map local names to the rewyn objects a snippet imported."""
    resolved: dict[str, object] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("rewyn"):
            continue
        try:
            module = importlib.import_module(node.module)
        except ImportError:
            continue
        for alias in node.names:
            target = getattr(module, alias.name, None)
            if target is not None:
                resolved[alias.asname or alias.name] = target
    return resolved


@pytest.mark.parametrize("page", pages(), ids=lambda p: p.name)
def test_documented_calls_use_keywords_that_exist(page: pathlib.Path) -> None:
    """A snippet that passes a keyword the callable does not accept is wrong.

    Imports resolving is not enough: a renamed parameter leaves the import
    working and the example broken.
    """
    wrong: list[str] = []
    for block in blocks(page):
        tree = ast.parse(block)
        symbols = _imported_symbols(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            target = symbols.get(node.func.id)
            if target is None or not callable(target):
                continue
            try:
                signature = inspect.signature(target)
            except (TypeError, ValueError):
                continue
            accepts_any = any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
            )
            if accepts_any:
                continue
            wrong.extend(
                f"{node.func.id}(..., {keyword.arg}=...)"
                for keyword in node.keywords
                if keyword.arg and keyword.arg not in signature.parameters
            )
    assert not wrong, f"{page.name} passes keywords that do not exist: {sorted(set(wrong))}"


def test_the_index_links_to_every_page() -> None:
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    for page in pages():
        if page.name == "README.md":
            continue
        assert f"({page.name})" in index, f"{page.name} is not linked from the index"


@pytest.mark.parametrize("page", pages(), ids=lambda p: p.name)
def test_every_page_covers_the_required_sections(page: pathlib.Path) -> None:
    """Spec §56: concept, examples, API reference and failure modes on every page."""
    if page.name in {"README.md", "getting-started.md"}:
        return
    text = page.read_text(encoding="utf-8")
    required = ["## Concept", "## API reference", "## Failure modes"]
    if page.name not in OPERATIONAL:
        required += ["## Minimal example", "## Production example"]
    for heading in required:
        assert heading in text, f"{page.name} is missing '{heading}'"


@pytest.mark.parametrize("page", pages(), ids=lambda p: p.name)
def test_every_page_shows_working_code(page: pathlib.Path) -> None:
    if page.name == "README.md":
        return
    assert blocks(page), f"{page.name} has no Python examples"


def test_the_documented_sections_match_the_spec() -> None:
    """Spec §56 lists the sections documentation must have."""
    required = {
        "getting-started",
        "models",
        "agents",
        "context-engineering",
        "memory",
        "rag",
        "tools",
        "mcp",
        "skills",
        "loops",
        "graphs",
        "subagents",
        "handoffs",
        "state",
        "sandbox",
        "guardrails",
        "human-in-the-loop",
        "replay",
        "evaluation",
        "regression",
        "production",
        "security",
        "cloud",
    }
    present = {p.stem for p in pages()}
    assert required <= present, f"missing pages: {sorted(required - present)}"
