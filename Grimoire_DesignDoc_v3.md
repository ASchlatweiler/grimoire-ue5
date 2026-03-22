

| GRIMOIRE UE5 Context Bridge for Claude  |  Design Document v1.0 Read-Only  •  Project-Agnostic  •  Always-On  •  grimoire-ue5 |
| :---- |

# **1\. Purpose & Problem Statement**

The core limitation of AI-assisted development in any large project is context compression. Descriptions of system state are lossy — they capture intent but not current reality. As a project grows, the gap between what was described and what actually exists widens.

This tool closes that gap by giving Claude read access to a live UE5 project through a local MCP server. Instead of reasoning from memory and description, Claude can query actual project state — Blueprint components, variable bindings, asset registries, event graphs — and use that ground truth as the foundation for design, debugging, and architecture recommendations.

| Design Philosophy Read-only. No write access. This is a context tool, not an automation tool. The human makes all changes. Claude just gets to see what actually exists. |
| :---- |

# **2\. Architecture Overview**

Three layers. Each has a single responsibility.

| Layer | Responsibility | Technology |
| :---- | :---- | :---- |
| **MCP Server** | Exposes tools to Claude over the MCP protocol. Routes tool calls to the Unreal bridge via IPC. | Python (mcp library), stdio transport |
| **IPC Bridge** | Passes serialized tool requests from the MCP server to the Unreal Python host. Returns JSON responses. | TCP localhost (127.0.0.1:65432), JSON protocol |
| **Unreal Host** | Runs inside the UE5 editor as a persistent background script. Listens on a socket, executes queries via the Unreal Python API, returns results. | Unreal Python (unreal module), async socket server |

## **2.1 Data Flow**

| Claude (claude.ai)     ↓  MCP tool call MCP Server  (mcp\_server.py)     ↓  JSON over TCP (127.0.0.1:65432) IPC Bridge  (bridge.py)     ↓  socket message Unreal Host  (ue5\_host.py  —  running inside UE5 editor)     ↓  unreal.\* API calls Live UE5 Project State     ↑  JSON response (same path back) |
| :---- |

| Why Localhost TCP? Uses TCP loopback (127.0.0.1) — traffic never leaves the machine, never touches the router or ISP. Works identically on Windows, Mac, and Linux with no platform-specific code. Port 65432 is arbitrary and configurable. |
| :---- |

# **3\. Project Configuration**

The server is project-agnostic. A config.toml file specifies which project is active and where the socket lives. Switching projects means updating two values.

## **3.1 config.toml**

| \[project\] name        \= "Sanctum"                    \# Human label only root        \= "C:/UE5Projects/Sanctum"     \# UE project root \[ipc\] host        \= "127.0.0.1"         port        \= 65432                        \# Arbitrary, must match ue5\_host.py timeout\_sec \= 5 \[server\] log\_level   \= "info" |
| :---- |

The Unreal host is also project-agnostic — it queries whatever project the editor currently has open. No per-project host configuration needed.

# **4\. V1 Tool Definitions**

Eight tools covering the most immediately useful read queries against a live UE5 project.

| Tool Name | Description | Returns |
| :---- | :---- | :---- |
| **list\_blueprints** | List all Blueprint assets in the project, optionally filtered by path prefix or name substring. | Array of { name, path, class } |
| **get\_blueprint** | Full inspection of a named Blueprint: components, variables, functions, interfaces implemented. | Structured object with component tree, vars, funcs |
| **list\_components** | List all components on a Blueprint actor. Quick dependency check without full inspection. | Array of { name, class, parent } |
| **get\_variables** | All variables on a Blueprint: name, type, default value, visibility flags. | Array of { name, type, default, isExposed } |
| **list\_interfaces** | All Blueprint Interfaces defined in the project. | Array of { name, path, functions\[\] } |
| **get\_interface** | Inspect a specific BPI: function signatures, input/output pins. | { name, functions: \[{ name, inputs\[\], outputs\[\] }\] } |
| **find\_event\_bindings** | Find all Blueprints that bind or implement a given event name or interface function. | Array of { blueprint, bindingType, context } |
| **asset\_search** | Search asset registry by class type and optional name filter. | Array of { name, path, assetClass, size } |

## **4.1 Example Response  —  get\_blueprint('BPC\_InteractableBase')**

| {   "name": "BPC\_InteractableBase",   "path": "/Game/Sanctum/Components/BPC\_InteractableBase",   "parent\_class": "ActorComponent",   "interfaces": \["BPI\_Interactable"\],   "components": \[\],   "variables": \[     { "name": "bIsLocked",      "type": "bool",     "default": false,  "isExposed": true  },     { "name": "InteractRadius", "type": "float",    "default": 150.0,  "isExposed": true  },     { "name": "OnInteracted",   "type": "delegate", "default": null,   "isExposed": false }   \],   "functions": \["Interact", "CanInteract", "GetInteractLabel"\] } |
| :---- |

# **5\. Unreal Python Host**

ue5\_host.py runs as a persistent background thread inside the UE5 editor, started automatically at editor launch via the project's Python Startup Scripts setting. It listens on a TCP socket (127.0.0.1:65432) and services incoming requests against the live project.

## **5.1 Startup Integration**

| Project Settings \> Plugins \> Python \> Startup Scripts Add entry: ue5\_host.py |
| :---- |

This ensures the socket server is live whenever the editor is open. No manual invocation required during normal development flow.

## **5.2 Key Unreal Python APIs**

| API | Used For |
| :---- | :---- |
| unreal.EditorAssetLibrary | Asset enumeration, path queries, existence checks |
| unreal.EditorFilterLibrary | Filtering asset lists by class or name pattern |
| unreal.Blueprint | Inspecting Blueprint class hierarchy and metadata |
| unreal.BlueprintEditorLibrary | Accessing component trees and variable metadata |
| unreal.AssetRegistryHelpers | Low-level registry queries for class-based searches |
| unreal.load\_asset(path) | Load a specific asset for deep property inspection |

| Threading Constraint Unreal Python runs on the game thread. The socket listener must run on a background thread and dispatch all unreal.\* calls back to the game thread via a queue or unreal.call\_on\_game\_thread(). This is the primary implementation complexity in the host layer — prove this works first before building anything else. |
| :---- |

# **6\. MCP Server**

A standard Python MCP server using the mcp SDK. Exposes the V1 tools, connects to the Unreal host over IPC, handles serialization and input validation.

## **6.1 Dependencies**

| mcp       \# Anthropic MCP Python SDK tomli     \# Config file parsing pydantic  \# Tool input validation |
| :---- |

## **6.2 Project Structure**

| ue5\_mcp/   mcp\_server.py       \# Tool definitions, MCP registration, entry point   bridge.py           \# IPC client: socket connect, send/recv JSON   config.py           \# Config loader   tools/     blueprints.py     \# list\_blueprints, get\_blueprint, list\_components, get\_variables     interfaces.py     \# list\_interfaces, get\_interface, find\_event\_bindings     assets.py         \# asset\_search   ue5\_host/     ue5\_host.py       \# Runs inside UE5 editor     handlers.py       \# One handler function per tool config.toml README.md |
| :---- |

## **6.3 Claude Desktop Registration**

| // claude\_desktop\_config.json {   "mcpServers": {     "ue5-context": {       "command": "python",       "args": \["/path/to/ue5\_mcp/mcp\_server.py"\],       "env": { "UE5\_MCP\_CONFIG": "/path/to/config.toml" }     }   } } |
| :---- |

# **7\. Error Handling**

* Editor offline: tool calls return { error: 'EDITOR\_OFFLINE', message: '...' } rather than hanging.

* Timeout: requests exceeding config timeout\_sec return { error: 'TIMEOUT' }.

* Bad queries: the Unreal host wraps all handlers in try/except and returns structured error objects. It never crashes.

* Input validation: Pydantic validates all tool inputs in the MCP server before the socket is touched. Malformed inputs return a validation error immediately.

# **8\. Out of Scope for V1**

Deliberately deferred to keep v1 focused and shippable.

* Event graph node inspection (deeper Blueprint introspection, harder to serialize cleanly)

* Material graph queries (different API surface, separate concern)

* Level actor inspection (useful but scope creep)

* Cross-project diffing (Sanctum vs AfterHPZero architecture comparison — V2 candidate)

* Write operations of any kind (separate design doc if ever pursued)

# **9\. Recommended Build Order**

| Phase | Deliverable | Goal |
| ----- | :---- | :---- |
| **1** | **ue5\_host.py skeleton** | Socket server alive in editor, responds to ping. Confirms threading model works. This is the highest-risk phase — validate it before anything else. |
| **2** | **bridge.py \+ mcp\_server.py** | MCP server connects to editor. Single tool (list\_blueprints) working end-to-end. |
| **3** | **get\_blueprint \+ list\_components** | Core inspection tools working. Validate response shapes against real Sanctum/AfterHPZero data. |
| **4** | **Remaining V1 tools** | Full 8-tool suite. Test all against both projects. |
| **5** | **Error handling \+ README** | Production-ready. Document project-switching steps. |

# **10\. find\_event\_bindings — Deep Specification**

The most complex tool in V1. Combines a persistent index with on-demand live scan to balance accuracy against performance.

## **10.1 Index Lifecycle**

* First run: full crawl of the project /Content folder. Engine folder is opt-in via config (index\_engine \= false by default). Runs once, result persisted in memory for the session.

* Delta updates: the Unreal host binds to asset registry delegates (OnAssetAdded, OnAssetRemoved, OnAssetUpdated) and invalidates only affected index entries on each save. Node moves without saving do not trigger updates.

* Auto-rescan: configurable interval in config.toml, default 15 minutes. Acts as safety net for anything delta detection missed.

* Delegate fallback: if OnAssetAdded/Removed/Updated are unavailable or unstable in the target UE5 Python version, the host falls back to index-on-demand \+ periodic full rescan only. Delta updates are an enhancement, not a hard requirement.

* Live scan: always available as an explicit mode. Bypasses the index and queries current project state directly. Use when you need guaranteed ground truth.

## **10.2 Index Shape**

| {   "event\_bindings": {     "OnInteracted": \[       { "blueprint": "BP\_Door",    "bindingType": "EventDispatcher", "context": "BeginPlay" },       { "blueprint": "BP\_Trigger", "bindingType": "Bind",            "context": "OnOverlap"  }     \]   },   "interface\_implementations": {     "BPI\_Interactable": \[       { "blueprint": "BP\_Chest",   "implementationType": "Full" },       { "blueprint": "BP\_Switch",  "implementationType": "Partial" }     \]   } } |
| :---- |

## **10.3 config.toml Additions**

| \[events\] index\_engine       \= false   \# Opt-in to crawl Engine folder autoscan\_interval  \= 15      \# Minutes. Set to 0 to disable. |
| :---- |

## **10.4 Tool Inputs & Outputs**

find\_event\_bindings accepts four optional parameters. All are optional — called with no arguments it returns the full index.

| Parameter | Type | Default | Description |
| :---- | :---- | :---- | :---- |
| **event\_name** | string | null | Filter results to a specific event name |
| **interface\_name** | string | null | Filter results to a specific interface function |
| **use\_live\_scan** | bool | false | Bypass index, query live project state directly for ground truth |
| **refresh\_index** | bool | false | Force a full index rebuild before returning results |

## **10.5 Output Shape**

| {   "source": "index",           // "index" | "live\_scan"   "index\_age\_seconds": 142,    // How old the index is. Omitted when source=live\_scan.   "event\_bindings": {     "OnInteracted": \[       { "blueprint": "BP\_Door",    "bindingType": "EventDispatcher", "context": "BeginPlay" }     \]   },   "interface\_implementations": {     "BPI\_Interactable": \[       { "blueprint": "BP\_Chest", "implementationType": "Full" }     \]   } } |
| :---- |

# **11\. Error Schema**

All errors share a consistent shape regardless of where in the pipeline they originate. The type field indicates which layer produced the error so Claude and the caller can handle them consistently.

## **11.1 Error Types by Pipeline Layer**

| Type | Origin | Examples |
| :---- | :---- | :---- |
| **VALIDATION** | MCP Server — bad tool input before socket is touched | Missing required parameter, wrong type, unknown tool name |
| **TRANSPORT** | Bridge layer — TCP issue, timeout, editor not running | EDITOR\_OFFLINE, TIMEOUT, CONNECTION\_REFUSED |
| **RUNTIME** | Unreal host — asset failed to load, API returned nothing | ASSET\_NOT\_FOUND, LOAD\_FAILED, ASSET\_LOADING |

## **11.2 Standard Error Response Shape**

| {   "error":   true,   "type":    "TRANSPORT",         // VALIDATION | TRANSPORT | RUNTIME   "code":    "EDITOR\_OFFLINE",    // Specific error code   "message": "Cannot reach UE5 host on 127.0.0.1:65432",   "tool":    "get\_blueprint"      // Which tool was called } |
| :---- |

| Consistency Rule Every error — regardless of origin — returns this shape as a tool result, not as an MCP-level protocol error. This means Claude always receives structured data it can reason about, never a raw exception. |
| :---- |

# **12\. Open Questions**

* Does unreal.call\_on\_game\_thread() satisfy the threading requirement cleanly, or is a tick-based dispatch queue more appropriate?

* Should the TCP port be per-project (to support multiple simultaneous editor instances) or global? A config-driven port means both projects can run simultaneously without conflict.

* Serialization strategy: Blueprint variables reference struct types by name rather than inlining field definitions. get\_struct is a separate call. This keeps get\_blueprint lean regardless of data complexity.

* find\_event\_bindings uses a hybrid approach: a pre-built index for static data shapes (structs, enums, asset classes) refreshed on demand, and live scan for behavioral wiring (event bindings, interface implementations). Index covers reference; live covers current state.

*— End of Document —*