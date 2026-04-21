# Grimoire — UE5 Context Bridge

Read-only MCP server that gives Claude live access to your UE5 project state — Blueprints, interfaces, variables, functions, event graphs, materials, structs, and Data Assets.

```
Claude → MCP Server (stdio) → IPC Bridge (TCP) → Unreal Host (in editor)
```

- **MCP Server:** Exposes tools to Claude, routes requests over TCP
- **Unreal Host:** Runs inside UE5 editor, executes queries via Unreal Python API

---

## What This Unlocks

Grimoire gives Claude live read access to your UE5 project. Combined with other MCP servers, this enables workflows that weren't previously possible:

- **Grimoire + Notion** — Claude reads your Blueprint architecture live and writes design summaries, task cards, or system documentation directly into Notion
- **Grimoire + GitHub** — Claude inspects your Blueprint systems and opens issues, updates wikis, or writes PR descriptions grounded in actual code
- **Grimoire + Slack** — Claude reads a Blueprint and posts a technical summary to your team channel
- **Grimoire alone** — Claude reasons about your systems in context, catches bugs, suggests refactors, and answers architecture questions without you describing anything

The pattern is: **Grimoire provides the UE5 context, other MCP servers act on it.**

---

## Prerequisites

- Python 3.10+
- UE5 project with **Python Script Plugin** enabled
- **Json Blueprint Utilities** plugin enabled (built-in, free, Epic Games)
- Claude Desktop or another MCP client

---

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

The editor must be able to import `ue5_host` on its Python path. Two common approaches:

- **Copy** — Copy the entire `ue5_host` folder into your project (e.g. paste it at `YourProject/Content/Python/ue5_host/`). No symlink required; this is the simplest option on Windows.
- **Symlink** — Point `Content/Python/ue5_host` at a single checkout elsewhere if you prefer not to duplicate files.

Either way, the layout should be `.../ue5_host/ue5_host.py` plus the rest of the package next to it. You can also skip both and pass a **full absolute path** to the startup script (see step 4).

### 4. Enable the Host in UE5

- Open your UE5 project
- **Edit → Project Settings → Plugins → Python**
- Under **Startup Scripts**, add: `ue5_host.ue5_host`
  - Or the full path, e.g. `C:/path/to/grimoire-ue5/ue5_host/ue5_host`
- Restart the editor (or run the script manually once)

### 5. Register the MCP Server with Claude Desktop

Edit your Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "ue5-context": {
      "command": "python",
      "args": ["-m", "ue5_mcp.mcp_server"],
      "cwd": "C:/path/to/grimoire-ue5",
      "env": {
        "UE5_MCP_CONFIG": "C:/path/to/grimoire-ue5/config.toml"
      }
    }
  }
}
```

Replace `cwd` and `UE5_MCP_CONFIG` with your actual paths. Add other MCP servers (Notion, GitHub, Slack etc) to the same config to enable multi-server workflows.

---

## Tools

| Tool | Description |
|------|-------------|
| `ping` | Check if the UE5 editor host is reachable |
| `list_blueprints` | List Blueprint assets (optional: `path_prefix`, `name_substring`) |
| `get_blueprint` | Full inspection: parent class, components, variables, functions, event graphs |
| `list_components` | Components on a Blueprint actor |
| `get_variables` | All variables with resolved types. `include_locals=True` for function-scope locals |
| `list_interfaces` | All Blueprint Interfaces in the project |
| `get_interface` | Full interface inspection: function signatures with typed input/output pins |
| `asset_search` | Search assets by class and name |
| `find_event_bindings` | Find Blueprints implementing a given event or interface (via cache) |
| `query_cache` | Query the SQLite cache by parent class, function name, variable name, or type reference |
| `get_data_asset` | Read property values from a Blueprint Data Asset instance (walk speeds, scalars, config) |
| `get_struct` | Inspect a UserDefinedStruct: field names and types |
| `get_material` | Inspect a Material or MaterialFunction: exposed parameters (scalar, vector, texture), output slot connections, and material function calls |

---

## What Grimoire Can Read

### Blueprints
- **Parent class** — resolved via Asset Registry
- **Component hierarchy** — full SCS tree
- **Variables** — names and resolved types (`EGait`, `FInventorySheet`, `BP_PlayerState_C` — not raw `ByteProperty`)
- **Functions** — names, typed input/output pins, execution flow body
- **Event graphs** — bound lifecycle events (`ReceiveBeginPlay`, `ReceiveControllerChanged`), custom events, and interface events — with full execution body where traceable
- **Warnings** — honest flags when data is partially unavailable, with workarounds

### Data Assets
- **Property values** — actual configured data from DA instances (`BaseWalkSpeed: 215`, `SprintMultiplier: 2.85`)
- **Nested struct values** — full property tree with GUID suffixes cleaned

### Structs
- **Field definitions** — all `UserDefinedStruct` fields with resolved types
- **Navigable type system** — `FInventorySheet → FInventoryContainer → FItemStack` fully traversable

### Materials
- **Scalar parameters** — name + default value (`HexScale: 50.0`, `StateBlend: 1.0`)
- **Vector parameters** — name + default RGBA (`OpenColor`, `LockedColor`)
- **Texture parameters** — name + default texture reference
- **Output slot connections** — which expression feeds `EmissiveColor`, `Opacity` etc
- **Material function calls** — which `MF_*` functions are used

### Cache
- **SQLite persistence** — parsed data survives across sessions
- **Auto-invalidation** — dirty flag watchdog marks stale entries every 15 seconds after asset saves, no manual cache clears needed

---

### Execution Body Format

Function and event bodies reconstruct the logical spine of each graph as a readable step list:

```
call SubscribeToStats_Player
set CachedInvComp
bind_delegate(OnInventorySheetUpdate)
branch
macro:IsValid
dynamiccast
set BP_PlayerState
bind_delegate(OnStatsChanged)
return ValidationResult (SInteractionValidationResults) <- CurrentResult
select (bool) [False, True]
```

Node types surfaced: `call`, `set`, `get`, `branch`, `macro:Name`, `bind_delegate(Name)`, `dynamiccast`, `switchenum`, `array.op`, `select`, `make`, `break`, `return X (Type) <- Source`

---

## Known Limitations

- **Interface implementation list** — UE5 Python API does not expose which interfaces a Blueprint implements. Workaround: use `query_cache(tool='query_blueprints', has_function='FunctionName')` to find implementors by shared function name.
- **Branch-aware execution bodies** — function bodies are flat step lists; branch true/false paths are not nested. UE5's T3D export does not serialize exec pin connections. For complex branching functions, a Blueprint screenshot helps cross-reference execution paths.
- **EventGraph partial traceability** — events whose exec chain begins with a macro node (e.g. `Switch Has Authority`) cannot be fully walked from T3D data. These events surface with an `EVENTGRAPH_PARTIAL` warning and `[exec chain not traceable]` body. The event's existence and binding are still reported correctly.
- **Deeply nested pure function chains** — first-level pure node sources are resolved (`<- select`, `<- GetIsLocked`); deeper chains may still show `<- ?`.

Both the interface list and exec pin connection gaps are known UE5 Python API limitations. An Epic support ticket has been filed requesting `exec pin LinkedTo` in T3D exports and `BlueprintGeneratedClass.interfaces` in Python bindings.

---

## How It Works

The UE5 Python API restricts direct access to Blueprint internals. Grimoire uses three alternative pathways:

1. **SubobjectDataSubsystem** — `k2_gather_subobject_data_for_blueprint()` + `export_text()` parsing for components
2. **JsonObjectGraphFunctionLibrary.stringify()** — for variable names, resolved types (`PinSubCategoryObject` → actual type name), and Data Asset property values
3. **AssetExportTask T3D export** — two-pass node graph parsing for function signatures, execution flow, event graphs, macros, delegates, Select nodes, and Material parameters

Requires the **Json Blueprint Utilities** plugin (built-in, free).

---

## Switching Projects

- Update `config.toml`: change `[project].root` and optionally `[ipc].port`
- For multiple editors: set `UE5_MCP_PORT` per project (e.g. `65432`, `65433`)
- Restart Claude Desktop after config changes

---

## Troubleshooting

**Editor offline / Connection refused**
- Ensure the UE5 editor is open with your project loaded
- Confirm the host started (check Output Log for `UE5 Context Bridge: listening on...`)
- Verify `config.toml` port matches the host (default `65432`)

**Port conflict**
- Use a different port in `config.toml` and set `UE5_MCP_PORT` in environment
- Or run only one UE5 editor at a time

**Timeout**
- Increase `timeout_sec` in `config.toml`
- Large projects may need more time for asset scans

**Python startup script not running**
- Check that the Python Script Plugin is enabled
- Use the full absolute path to `ue5_host` in Startup Scripts if a relative path fails

**Cache stale after code changes**
- Clear specific entries: `DELETE FROM blueprint_cache WHERE asset_path LIKE '%BlueprintName%'`
- Cache DB location: `YourProject/Saved/Grimoire/cache.db`
- The watchdog auto-invalidates on asset save — manual clears only needed after handler code changes

---

## Roadmap

### V7 (planned)
- Animation Blueprint anim graph support
- Branch-aware body format — pending Epic Python API exposure of exec pin connections
- Multi-project switching without config changes
- `grimoire_setup.py` — automated install and config generation