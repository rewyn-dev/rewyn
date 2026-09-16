"""Model Context Protocol: first-class client, server and discovery."""

from rewyn.mcp.adapter import MCPToolError, mcp_tool_to_rewyn, result_to_value
from rewyn.mcp.client import MCPClient, MCPServerConfig, connect
from rewyn.mcp.discovery import discover_configs, load_mcp_config, parse_mcp_config
from rewyn.mcp.registry import MCPRegistry
from rewyn.mcp.server import run_stdio, serve_tools

__all__ = [
    "MCPClient",
    "MCPRegistry",
    "MCPServerConfig",
    "MCPToolError",
    "connect",
    "discover_configs",
    "load_mcp_config",
    "mcp_tool_to_rewyn",
    "parse_mcp_config",
    "result_to_value",
    "run_stdio",
    "serve_tools",
]
