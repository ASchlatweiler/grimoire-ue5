# GRIMOIRE UE5 Context Bridge

Read-only MCP server that gives Claude live access to your UE5 project state — Blueprints, interfaces, assets, and event bindings.

## Architecture

```
Claude → MCP Server (stdio) → IPC Bridge (TCP) → Unreal Host (in editor)
```

- **MCP Server**: Exposes tools to Claude, routes requests over TCP
- **Unreal Host**: Runs inside UE5 editor, executes queries via Unreal Python API

## Prerequisites

- Python 3.10+
- UE5 project with **Python Script Plugin** enabled
- Claude Desktop or another MCP client

## Installation

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure the project

Edit `config.toml`:

```toml
[project]
name = "Grimoire"
root = "C:/path/to/your/ue5-project"

[ipc]
host = "127.0.0.1"
port = 65432
timeout_sec = 5

[server]
log_level = "info"
```

### 3. Install the Unreal Host

Copy or symlink the `ue5_host` folder into your UE5 project so the editor can load it. Recommended:

- Place `ue5_host` at: `YourProject/Content/Python/ue5_host/`
- Or use an absolute path in Startup Scripts

### 4. Enable the Host in UE5

1. Open your UE5 project
2. **Edit → Project Settings → Plugins → Python**
3. Under **Startup Scripts**, add: `ue5_host.ue5_host`
   - Or the full path, e.g. `C:/path/to/grimoire-ue5/ue5_host/ue5_host`
4. Restart the editor (or run the script manually once)

### 5. Register the MCP Server with Claude Desktop

Edit your Claude Desktop config (e.g. `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "ue5-context": {
      "command": "python",
      "args": ["-m", "ue5_mcp.mcp_server"],
      "cwd": "C:/Users/alexs/Desktop/grimoire-ue5",
      "env": {
        "UE5_MCP_CONFIG": "C:/Users/alexs/Desktop/grimoire-ue5/config.toml"
      }
    }
  }
}
```

Replace `cwd` and `UE5_MCP_CONFIG` with your actual project path.

## Tools

| Tool | Status | Description |
|------|--------|-------------|
| `ping` | ✅ V4 | Check if the UE5 editor host is reachable |
| `list_blueprints` | ✅ V4 | List Blueprint assets (optional: path_prefix, name_substring) |
| `get_blueprint` | ✅ V4 | Full inspection: parent class, components, variables, functions with execution flow |
| `list_components` | ✅ V4 | Components on a Blueprint actor |
| `get_variables` | ✅ V4 | All variables on a Blueprint with types |
| `list_interfaces` | ✅ V4 | All Blueprint Interfaces in the project |
| `asset_search` | ✅ V4 | Search assets by class and name |
| `get_interface` | ⚠️ V4 | Returns interface name and path. Function signatures pending V5 |
| `find_event_bindings` | ⚠️ V4 | Runs without error. Interface implementation scan pending V5 |

### What Grimoire can read

Grimoire gives Claude live read-only access to your UE5 project's Blueprint graph. For any Blueprint asset it can return:

- **Parent class** — resolved via Asset Registry tags
- **Component hierarchy** — full SCS tree via `SubobjectDataSubsystem`
- **Variables** — names and types via `JsonObjectGraphFunctionLibrary` JSON serialization
- **Functions** — names, input parameters, return signatures, and execution flow via T3D asset export + node graph parsing

Execution flow reconstructs the logical spine of each function — variable reads/writes, branches, struct construction/deconstruction, and return paths — as a human-readable step list.

### How it works

The UE5 Python API restricts direct access to Blueprint internals (`NewVariables`, `SimpleConstructionScript`, function graphs) via property protection. Grimoire works around this using three alternative pathways:

- `SubobjectDataSubsystem.k2_gather_subobject_data_for_blueprint()` + `export_text()` parsing for components
- `JsonObjectGraphFunctionLibrary.stringify()` for variable data
- `AssetExportTask` T3D export + two-pass node graph parsing for function signatures and execution flow

These pathways require the **Json Blueprint Utilities** plugin to be enabled in your UE5 project (built-in, free, Epic Games).

### V5 Roadmap

- **Select node resolution** — `K2Node_Select` body display for null-safe and conditional return patterns
- **Interface implementation scan** — `find_event_bindings` full implementation via T3D parsing
- **Interface function signatures** — `get_interface` function pin data
- **EventGraph events** — expose bound events and dispatchers from the ubergraph
- **SQLite caching** — persistent T3D cache to avoid re-export on every call

## Switching Projects

1. Update `config.toml`: change `[project].root` and optionally `[ipc].port`
2. For multiple editors: set `UE5_MCP_PORT` per project (e.g. 65432, 65433) and ensure each editor’s host uses the matching port
3. Restart Claude Desktop after config changes

## Troubleshooting

### Editor offline / Connection refused

- Ensure the UE5 editor is open with your project loaded
- Confirm the Unreal host started (check Output Log for "UE5 Context Bridge: listening on...")
- Verify `config.toml` port matches the host (default 65432)

### Port conflict

- Use a different port in `config.toml` and set `UE5_MCP_PORT` in the environment when launching the editor
- Or run only one UE5 editor at a time

### Timeout

- Increase `timeout_sec` in `config.toml`
- Large projects may need more time for asset scans

### Python startup script not running

- Check that the Python Script Plugin is enabled
- Use the full path to `ue5_host` in Startup Scripts if a relative path fails

## Design

See `Grimoire_DesignDoc_v3.md` for the full design document.
