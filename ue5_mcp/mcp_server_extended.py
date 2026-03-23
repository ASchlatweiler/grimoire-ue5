"""
UE5 Context Bridge — Extended MCP Server (interfaces, events, asset search)

Split from main server to improve tool registration in Claude Desktop.
"""
import json

from mcp.server.fastmcp import FastMCP

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


mcp = FastMCP("grimoire-blueprint-extended", json_response=True)


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
def list_interfaces() -> str:
    """List all Blueprint Interfaces defined in the project."""
    return _call("list_interfaces", {})


@mcp.tool()
def get_interface(interface_name: str) -> str:
    """Inspect a Blueprint Interface: function signatures, input/output pins."""
    return _call("get_interface", {"interface_name": interface_name})


@mcp.tool()
def find_event_bindings(
    event_name: str | None = None,
    interface_name: str | None = None,
    use_live_scan: bool = True,
    refresh_index: bool = False,
) -> str:
    """Find Blueprints that bind or implement a given event or interface function."""
    return _call("find_event_bindings", {
        "event_name": event_name,
        "interface_name": interface_name,
        "use_live_scan": use_live_scan,
        "refresh_index": refresh_index,
    })


@mcp.tool()
def asset_search(
    asset_class: str | None = None,
    name_filter: str | None = None,
) -> str:
    """Search asset registry by class type and optional name filter."""
    return _call("asset_search", {"asset_class": asset_class, "name_filter": name_filter})


if __name__ == "__main__":
    mcp.run(transport="stdio")
