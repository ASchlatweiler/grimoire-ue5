"""
UE5 Context Bridge — MCP Server

Exposes tools to Claude over MCP. Routes tool calls to the Unreal host via IPC.
Run with: python -m ue5_mcp.mcp_server (from project root)
"""
import sys
print(sys.version)

import json

from mcp.server.fastmcp import FastMCP

try:
    from .bridge import send_request
    from .config import Config
except ImportError:
    from bridge import send_request
    from config import Config

config = Config.load()
mcp = FastMCP("UE5 Context Bridge", json_response=True)


def _call(tool: str, params: dict) -> str:
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
def get_blueprint(blueprint_name: str) -> str:
    """Full inspection of a Blueprint: components, variables, functions, interfaces implemented."""
    return _call("get_blueprint", {"blueprint_name": blueprint_name})


@mcp.tool()
def list_components(blueprint_name: str) -> str:
    """List all components on a Blueprint actor. Quick dependency check without full inspection."""
    return _call("list_components", {"blueprint_name": blueprint_name})


@mcp.tool()
def get_variables(blueprint_name: str, include_locals: bool = False) -> str:
    """All variables on a Blueprint: name, type, default value, visibility flags. Set include_locals=True to include function-scope locals."""
    return _call("get_variables", {
        "blueprint_name": blueprint_name,
        "include_locals": include_locals,
    })


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


@mcp.tool()
def query_cache(
    tool: str,
    parent_class: str | None = None,
    has_function: str | None = None,
    has_variable: str | None = None,
    references_type: str | None = None,
    name_filter: str | None = None,
    input_type: str | None = None,
    output_type: str | None = None,
    type_name: str | None = None,
) -> str:
    """Query the Grimoire SQLite cache. Set tool to 'query_blueprints', 'query_functions', or 'query_references'. query_blueprints: filter by parent_class, has_function, has_variable, references_type. query_functions: filter by name_filter, input_type, output_type. query_references: requires type_name."""
    if tool == "query_blueprints":
        return _call("query_blueprints", {
            "parent_class": parent_class,
            "has_function": has_function,
            "has_variable": has_variable,
            "references_type": references_type,
        })
    elif tool == "query_functions":
        return _call("query_functions", {
            "name_filter": name_filter,
            "input_type": input_type,
            "output_type": output_type,
        })
    elif tool == "query_references":
        return _call("query_references", {"type_name": type_name})
    else:
        return '{"error": true, "message": "tool must be query_blueprints, query_functions, or query_references"}'


def _debug_schema():
    import json
    for tool in mcp._tool_manager.list_tools():
        try:
            schema = json.dumps(tool.model_json_schema() if hasattr(tool, 'model_json_schema') else str(tool))
            print(f"OK: {tool.name}")
        except Exception as e:
            print(f"FAIL: {e}")


if __name__ == "__debug__":
    _debug_schema()


if __name__ == "__main__":
    mcp.run(transport="stdio")
