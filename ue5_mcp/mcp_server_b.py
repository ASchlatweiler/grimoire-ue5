"""
UE5 Context Bridge — MCP Server B (ping, list_blueprints, list_components)
"""
import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("grimoire-blueprint-b")

try:
    from .bridge import send_request
    from .config import Config
except ImportError:
    from bridge import send_request
    from config import Config

_config = None


def _get_config():
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def _call(tool: str, params: dict) -> str:
    config = _get_config()
    result = send_request(
        tool=tool,
        params=params,
        host=config.ipc.host,
        port=config.ipc.port,
        timeout_sec=config.ipc.timeout_sec,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def ping() -> str:
    """Check if the UE5 editor host is reachable. Returns pong on success."""
    return _call("ping", {})


@mcp.tool()
def list_blueprints(
    path_prefix: str | None = None,
    name_substring: str | None = None,
) -> str:
    """List all Blueprint assets in the project. Optionally filter by path prefix or name substring."""
    return _call("list_blueprints", {"path_prefix": path_prefix, "name_substring": name_substring})


@mcp.tool()
def list_components(blueprint_name: str) -> str:
    """List all components on a Blueprint actor. Quick dependency check without full inspection."""
    return _call("list_components", {"blueprint_name": blueprint_name})


if __name__ == "__main__":
    mcp.run(transport="stdio")
