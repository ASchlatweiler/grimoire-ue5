---
name: GRIMOIRE Schema Implementation
overview: "Implement the schema refinements from GRIMOIRE-Schema.md: replace debug_error pollution with structured top-level warnings in get_blueprint and find_event_bindings."
todos: []
isProject: false
---

# GRIMOIRE Schema Implementation Plan

## Context

[GRIMOIRE-Schema.md](GRIMOIRE-Schema.md) documents the current schema and recommends refinements based on LLM feedback. The primary implementation work is the **debug handling refactor** (Section 5): stop polluting domain arrays with `{"debug_error": "..."}` objects and instead use a structured `warnings` array.

## Current State

In [ue5_host/handlers.py](ue5_host/handlers.py), three sections in `handle_get_blueprint` inject debug objects:


| Section        | Current behavior                                          | Issue                                                                                                         |
| -------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Components** | `components.append({"debug_error": str(e)})` on exception | Mixed-type array; deduplication later **drops** debug objects (no `scs_node`), so errors can be silently lost |
| **Variables**  | `variables = [{"debug_error": str(e)}]` on exception      | Entire array replaced with single debug object                                                                |
| **Functions**  | `functions = [{"debug_error": str(e)}]` on exception      | Same as variables                                                                                             |


## Target State

```json
{
  "name": "...",
  "path": "...",
  "components": [...],
  "variables": [...],
  "functions": [...],
  "warnings": [
    {"code": "PARTIAL_PARSE", "message": "...", "section": "components"}
  ]
}
```

**find_event_bindings** (when partial failures occur during live scan):

```json
{
  "source": "live_scan",
  "event_bindings": {},
  "interface_implementations": {...},
  "warnings": [
    {"code": "PARTIAL_PARSE", "message": "...", "blueprint": "BP_Problematic"}
  ]
}
```

- Domain arrays contain **only** valid domain objects; diagnostic data lives in top-level `warnings`
- get_blueprint: `section` indicates which extraction failed
- find_event_bindings: `blueprint` identifies the failing asset (per-blueprint try/except currently swallows with `continue`)

## Implementation Steps

### 1. Introduce warnings collection in handle_get_blueprint

- Declare `warnings: list[dict] = []` before the components section
- On exception in any section, append to `warnings` instead of polluting the domain array:
  - `warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "section": "components"})` (or `"variables"`, `"functions"`)
- Ensure partial data is preserved: e.g. if variables fails halfway, keep whatever was extracted (current behavior replaces all with debug_error; we keep valid items and add a warning)
- Add `"warnings": warnings` to the return dict (always present, empty list if none)

### 2. Update components section

- **Before:** `except Exception as e: components.append({"debug_error": str(e)})`
- **After:** `except Exception as e: warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "section": "components"})`
- Keep `components` as the list of valid component dicts only (no append of debug object)

### 3. Update variables section

- **Before:** `except Exception as e: variables = [{"debug_error": str(e)}]`
- **After:** `except Exception as e: warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "section": "variables"})`
- Leave `variables` as `[]` or whatever was successfully built before the exception (variables are built in one pass, so it will typically be `[]`)

### 4. Update functions section

- **Before:** `except Exception as e: functions = [{"debug_error": str(e)}]`
- **After:** `except Exception as e: warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "section": "functions"})`
- Leave `functions` as `[]` or the list of functions successfully parsed before the exception

### 5. Downstream tools (list_components, get_variables)

- These return `result.get("components", [])` and `result.get("variables", [])` from `handle_get_blueprint`
- No changes needed: they already receive clean arrays
- Warnings are only on the full `get_blueprint` response; slice tools remain minimal

### 6. Add warnings to find_event_bindings

- Declare `warnings: list[dict] = []` at the start of the handler
- In the per-blueprint inner `try` (around line 566), change `except Exception: continue` to:
  - `except Exception as e: warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "blueprint": bp_name}); continue`
- Add `"warnings": warnings` to the return dict
- Ensures partial scan results (successful blueprints) are preserved while failures are surfaced

### 7. Schema documentation alignment

- Ensure [GRIMOIRE-Schema.md](GRIMOIRE-Schema.md) Section 5 "Recommended Refactor" is marked as implemented (optional note in the doc)
- No code changes to the schema doc unless you want an explicit "Implemented" badge

## Data Flow

```mermaid
flowchart TD
    subgraph handle_get_blueprint [handle_get_blueprint]
        W[warnings = []]
        C[components section]
        V[variables section]
        F[functions section]
        R[return dict]
    end
    C -->|exception| W
    V -->|exception| W
    F -->|exception| W
    W --> R
    C -->|success| R
    V -->|success| R
    F -->|success| R
```



## Validation

- Call `get_blueprint` on a Blueprint that triggers an error (e.g. missing Json Blueprint Utilities plugin) and confirm:
  - `components`, `variables`, `functions` contain only valid objects (or empty arrays)
  - `warnings` contains `{"code": "PARTIAL_PARSE", "message": "...", "section": "..."}`
- Call `find_event_bindings` and, if any blueprint fails during scan, confirm:
  - `event_bindings` and `interface_implementations` contain only successful results
  - `warnings` contains `{"code": "PARTIAL_PARSE", "message": "...", "blueprint": "BP_Name"}`

## Out of Scope (per schema doc)

- Variable metadata (defaults, exposure) — Medium priority, deferred
- Component hierarchy — Medium priority, deferred
- Full graph topology — V3+

