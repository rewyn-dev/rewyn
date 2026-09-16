"""Discover MCP server configurations from config files.

Two formats are accepted:

- The widely used ``{"mcpServers": {"name": {"command": ..., "args": [...]}}}``
  layout (also ``{"url": ...}`` entries for HTTP servers).
- Rewyn's own ``{"servers": [{"name": ..., "transport": ..., ...}]}``.

Search order: ``./mcp.json``, ``./.rewyn/mcp.json``, ``$REWYN_HOME/mcp.json``,
``~/.rewyn/mcp.json``, plus any paths in ``REWYN_MCP_CONFIG`` (``os.pathsep``
separated).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from rewyn.core.settings import get_settings
from rewyn.mcp.client import MCPServerConfig


def parse_mcp_config(data: dict[str, object], *, source: str = "config") -> list[MCPServerConfig]:
    configs: list[MCPServerConfig] = []
    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        for name, raw in servers.items():
            if not isinstance(raw, dict):
                continue
            if raw.get("url"):
                configs.append(
                    MCPServerConfig(
                        name=str(name),
                        transport="http",
                        url=str(raw["url"]),
                        metadata={"source": source},
                    )
                )
            elif raw.get("command"):
                configs.append(
                    MCPServerConfig(
                        name=str(name),
                        transport="stdio",
                        command=str(raw["command"]),
                        args=[str(a) for a in raw.get("args", [])],
                        env={str(k): str(v) for k, v in dict(raw.get("env", {})).items()},
                        cwd=str(raw["cwd"]) if raw.get("cwd") else None,
                        metadata={"source": source},
                    )
                )
    listed = data.get("servers")
    if isinstance(listed, list):
        for raw in listed:
            if isinstance(raw, dict):
                configs.append(
                    MCPServerConfig.model_validate({**raw, "metadata": {"source": source}})
                )
    return configs


def load_mcp_config(path: Path) -> list[MCPServerConfig]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return []
    return parse_mcp_config(data, source=str(path))


def default_config_paths() -> list[Path]:
    paths = [
        Path("mcp.json"),
        Path(".rewyn") / "mcp.json",
        get_settings().home / "mcp.json",
        Path.home() / ".rewyn" / "mcp.json",
    ]
    extra = os.environ.get("REWYN_MCP_CONFIG")
    if extra:
        paths.extend(Path(p) for p in extra.split(os.pathsep) if p)
    return paths


def discover_configs(paths: Iterable[Path] | None = None) -> list[MCPServerConfig]:
    """Load every readable config file, first definition of a name wins."""
    seen: dict[str, MCPServerConfig] = {}
    for path in paths if paths is not None else default_config_paths():
        if not path.is_file():
            continue
        try:
            configs = load_mcp_config(path)
        except (OSError, ValueError):
            continue
        for config in configs:
            seen.setdefault(config.name, config)
    return list(seen.values())
