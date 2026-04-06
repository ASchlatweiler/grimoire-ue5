"""
Handler functions for MCP tools. All handlers run on the Unreal game thread.
"""

import hashlib
import json
import re
import sqlite3
import time
import unreal

_cache_db_path = None
_GUID_SUFFIX = re.compile(r'_\d+_[A-F0-9]{32}$')


def _clean_name(name: str) -> str:
    """Strip UE5 internal GUID suffixes from variable and node names."""
    return _GUID_SUFFIX.sub('', name)


def _extract_class_variable_names(t3d: str) -> set:
    """Extract authoritative class-level variable names from T3D NewVariables section."""
    names: set[str] = set()
    for m in re.finditer(r'NewVariables\(\d+\)=\(VarName="([^"]+)"', t3d):
        names.add(_clean_name(m.group(1)))
    return names


def _resolve_pin_type(pin_category: str, pin_subcat_object: str) -> str:
    """Resolve a pin's display type from category and subcat object path."""
    if pin_subcat_object:
        m = re.search(r'[./]([A-Za-z_][A-Za-z0-9_]+)\'?"?\s*$', pin_subcat_object)
        if m:
            resolved = _clean_name(m.group(1))
            if resolved:
                return resolved
    out = pin_category if pin_category else "unknown"
    return out if out else "unknown"


def _collect_bpgc_hierarchy_variable_names(gen_class) -> set:
    """VarNames from new_variables on this Blueprint and each BlueprintGeneratedClass parent.

    T3D NewVariables often lists only variables introduced on that asset; inherited Blueprint
    members are missing and were incorrectly tagged local and filtered out.
    """
    names: set[str] = set()
    if not gen_class:
        return names
    g = gen_class
    for _ in range(64):
        bp_asset = None
        try:
            bp_asset = g.get_editor_property("class_generated_by")
        except Exception:
            pass
        if bp_asset:
            nvars = None
            try:
                nvars = bp_asset.get_editor_property("new_variables")
            except Exception:
                pass
            if nvars:
                for vd in nvars:
                    try:
                        vn = vd.get_editor_property("var_name")
                        if vn:
                            names.add(_clean_name(str(vn)))
                    except Exception:
                        pass
        try:
            g = g.get_super_class()
        except Exception:
            break
        if not g:
            break
    return names


def _get_cache_db():
    global _cache_db_path
    if _cache_db_path is None:
        import os

        project_dir = unreal.SystemLibrary.get_project_directory()
        cache_dir = os.path.join(project_dir, "Saved", "Grimoire")
        os.makedirs(cache_dir, exist_ok=True)
        _cache_db_path = os.path.join(cache_dir, "cache.db")
    conn = sqlite3.connect(_cache_db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS blueprint_cache (
            asset_path TEXT PRIMARY KEY,
            modified_time REAL,
            cached_at REAL,
            result_json TEXT,
            dirty INTEGER DEFAULT 0
        )
    """
    )
    conn.commit()
    try:
        conn.execute("ALTER TABLE blueprint_cache ADD COLUMN dirty INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    return conn


def _get_asset_modified_time(asset_path: str) -> float:
    """Get the last modified timestamp of an asset via Asset Registry."""
    try:
        import os

        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        assets = ar.get_assets_by_path("/Game", recursive=True)
        for ad in assets:
            path = ad.to_soft_object_path().export_text()
            if "." in path:
                path = path.split(".")[0]
            if path == asset_path:
                pkg_name = ad.package_name
                pkg_filename = (
                    unreal.PackageTools.filename_from_package_name(str(pkg_name))
                    if pkg_name
                    else None
                )
                if pkg_filename and os.path.exists(pkg_filename):
                    return os.path.getmtime(pkg_filename)
    except Exception:
        pass
    return time.time()


def _cache_get(asset_path: str):
    """Return cached result if fresh, else None."""
    try:
        conn = _get_cache_db()
        row = conn.execute(
            "SELECT dirty, modified_time, result_json FROM blueprint_cache WHERE asset_path = ?",
            (asset_path,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        dirty, cached_modified, result_json = row
        if dirty:
            return None
        current_modified = _get_asset_modified_time(asset_path)
        if current_modified <= cached_modified:
            return json.loads(result_json)
        return None
    except Exception:
        return None


def _cache_set(asset_path: str, result: dict):
    """Store result in cache with current asset modified time."""
    try:
        conn = _get_cache_db()
        modified_time = _get_asset_modified_time(asset_path)
        conn.execute(
            """INSERT OR REPLACE INTO blueprint_cache
               (asset_path, modified_time, cached_at, result_json, dirty)
               VALUES (?, ?, ?, ?, 0)""",
            (asset_path, modified_time, time.time(), json.dumps(result)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def handle_mark_dirty(asset_path: str) -> dict:
    """Mark a cache entry as dirty, forcing re-parse on next access."""
    try:
        conn = _get_cache_db()
        conn.execute(
            "UPDATE blueprint_cache SET dirty=1 WHERE asset_path=?",
            (asset_path,),
        )
        conn.commit()
        conn.close()
        return {"marked_dirty": asset_path}
    except Exception as e:
        return {"error": str(e)}


def handle_query_blueprints(
    parent_class: str | None = None,
    has_function: str | None = None,
    has_variable: str | None = None,
    references_type: str | None = None,
) -> dict:
    """Search cached blueprints by parent class, function name, variable name, type reference, or implemented interface name."""
    try:
        conn = _get_cache_db()
        rows = conn.execute(
            "SELECT asset_path, result_json FROM blueprint_cache"
        ).fetchall()
        conn.close()

        results = []
        for asset_path, result_json in rows:
            try:
                bp = json.loads(result_json)
            except Exception:
                continue

            if parent_class:
                if parent_class.lower() not in (bp.get("parent_class") or "").lower():
                    continue

            if has_function:
                func_names = [f.get("name", "") for f in bp.get("functions", [])]
                if not any(has_function.lower() in n.lower() for n in func_names):
                    continue

            if has_variable:
                var_names = [v.get("name", "") for v in bp.get("variables", [])]
                if not any(has_variable.lower() in n.lower() for n in var_names):
                    continue

            if references_type:
                all_types = []
                for v in bp.get("variables", []):
                    all_types.append(v.get("type", ""))
                for f in bp.get("functions", []):
                    for p in f.get("inputs", []) + f.get("outputs", []):
                        all_types.append(p.get("type", ""))
                iface_names = bp.get("interfaces") or []
                ref_l = references_type.lower()
                type_hit = any(ref_l in (t or "").lower() for t in all_types)
                iface_hit = any(ref_l in (iface or "").lower() for iface in iface_names)
                if not type_hit and not iface_hit:
                    continue

            results.append({
                "name": bp.get("name"),
                "path": bp.get("path"),
                "parent_class": bp.get("parent_class"),
            })

        return {"count": len(results), "results": results}
    except Exception as e:
        return {"error": True, "message": str(e)}


def handle_query_functions(
    name_filter: str | None = None,
    input_type: str | None = None,
    output_type: str | None = None,
) -> dict:
    """Search all cached function signatures across the project."""
    try:
        conn = _get_cache_db()
        rows = conn.execute(
            "SELECT asset_path, result_json FROM blueprint_cache"
        ).fetchall()
        conn.close()

        results = []
        for asset_path, result_json in rows:
            try:
                bp = json.loads(result_json)
            except Exception:
                continue

            for f in bp.get("functions", []):
                fname = f.get("name", "")

                if name_filter and name_filter.lower() not in fname.lower():
                    continue

                if input_type:
                    input_types = [p.get("type", "") for p in f.get("inputs", [])]
                    if not any(input_type.lower() in t.lower() for t in input_types):
                        continue

                if output_type:
                    output_types = [p.get("type", "") for p in f.get("outputs", [])]
                    if not any(output_type.lower() in t.lower() for t in output_types):
                        continue

                results.append({
                    "blueprint": bp.get("name"),
                    "path": bp.get("path"),
                    "function": fname,
                    "inputs": f.get("inputs", []),
                    "outputs": f.get("outputs", []),
                })

        return {"count": len(results), "results": results}
    except Exception as e:
        return {"error": True, "message": str(e)}


def handle_query_references(
    type_name: str,
) -> dict:
    """Find all cached blueprints that reference a given type, asset, or struct name."""
    try:
        conn = _get_cache_db()
        rows = conn.execute(
            "SELECT asset_path, result_json FROM blueprint_cache"
        ).fetchall()
        conn.close()

        results = []
        for asset_path, result_json in rows:
            try:
                bp = json.loads(result_json)
            except Exception:
                continue

            hits = []

            for v in bp.get("variables", []):
                if type_name.lower() in v.get("type", "").lower() or type_name.lower() in v.get("name", "").lower():
                    hits.append(f"variable: {v['name']} ({v['type']})")

            for f in bp.get("functions", []):
                for p in f.get("inputs", []):
                    if type_name.lower() in p.get("type", "").lower():
                        hits.append(f"function {f['name']} input: {p['name']} ({p['type']})")
                for p in f.get("outputs", []):
                    if type_name.lower() in p.get("type", "").lower():
                        hits.append(f"function {f['name']} output: {p['name']} ({p['type']})")

            for c in bp.get("components", []):
                if type_name.lower() in c.get("class", "").lower():
                    hits.append(f"component: {c['name']} ({c['class']})")

            if hits:
                results.append({
                    "name": bp.get("name"),
                    "path": bp.get("path"),
                    "references": hits,
                })

        return {"count": len(results), "results": results}
    except Exception as e:
        return {"error": True, "message": str(e)}


def _find_blueprint_path(name: str) -> str | None:
    """Find full asset path for a Blueprint by name."""
    ar_filter = unreal.ARFilter(package_paths=["/Game"], recursive_paths=True)
    assets = unreal.AssetRegistryHelpers.get_blueprint_assets(ar_filter)
    if not assets:
        return None
    for ad in assets:
        if not unreal.AssetRegistryHelpers.is_valid(ad):
            continue
        path = ad.to_soft_object_path().export_text()
        if "." in path:
            path = path.split(".")[0]
        asset_name = path.split("/")[-1] if "/" in path else path
        if asset_name == name:
            return path
    return None


def handle_ping() -> dict:
    """Respond to ping — validates connection and threading."""
    return {"ok": True, "pong": True}


def handle_list_blueprints(path_prefix: str | None = None, name_substring: str | None = None) -> dict:
    """List all Blueprint assets, optionally filtered by path or name."""
    try:
        # Use ARFilter with /Game path to get Blueprint assets
        ar_filter = unreal.ARFilter(
            package_paths=["/Game"],
            recursive_paths=True,
        )
        assets = unreal.AssetRegistryHelpers.get_blueprint_assets(ar_filter)
        if assets is None:
            assets = []

        results = []
        for asset_data in assets:
            if not unreal.AssetRegistryHelpers.is_valid(asset_data):
                continue
            path = asset_data.to_soft_object_path().export_text()
            if "." in path:
                path = path.split(".")[0]
            name = path.split("/")[-1] if "/" in path else path
            class_name = "Blueprint"
            if hasattr(asset_data, "asset_class_path"):
                class_name = str(asset_data.asset_class_path.asset_name) or class_name

            if path_prefix and not path.startswith(path_prefix):
                continue
            if name_substring and name_substring.lower() not in name.lower():
                continue

            results.append({"name": name, "path": path, "class": class_name})

        return results
    except Exception as e:
        return {
            "error": True,
            "type": "RUNTIME",
            "code": "HANDLER_ERROR",
            "message": str(e),
            "tool": "list_blueprints",
        }


def _parse_function_graph(t3d, graph_name):
    import re

    pattern = rf'(K2Node_\w+_\d+)" ExportPath="[^"]*:{re.escape(graph_name)}\.'
    matches = list(re.finditer(pattern, t3d))
    nodes = {}
    for m in matches:
        pos = m.start()
        node_name = m.group(1)
        chunk = t3d[pos : pos + 12000]  # large K2Node_FunctionEntry may include many LocalVariables
        # Second-pass nodes have either VariableReference, FunctionReference, or CustomProperties
        # within the first 600 chars. First-pass nodes have none of these.
        is_second_pass = (
            "CustomProperties" in chunk[:600]
            or "VariableReference=" in chunk[:600]
            or "FunctionReference=" in chunk[:600]
            or "ExtraFlags=" in chunk[:600]
            or "LocalVariables" in chunk[:600]
            or "NodePosX" in chunk[:600]
            or "NodePosY" in chunk[:600]
            or "bMadeA" in chunk[:600]
        )
        if not is_second_pass:
            continue
        # Only search for ref before CustomProperties pins start
        pre_pins = chunk[:chunk.find("CustomProperties Pin")] if "CustomProperties Pin" in chunk else chunk[:600]
        node_type = re.sub(r"_\d+$", "", node_name)

        # Extract ref — MacroInstance first so MacroGraphReference wins over stray Var/Func refs in chunk
        ref = None
        if node_type == "K2Node_MacroInstance":
            ref_m = re.search(
                r'MacroGraphReference=\([^)]*MacroGraph="[^"]*:([^\']+)\'',
                chunk,
            )
            ref = ref_m.group(1).strip() if ref_m else None
        else:
            ref_match = re.search(
                r'(?:Variable|Function)Reference=\([^)]*MemberName="([^"]+)"',
                pre_pins,
            )
            ref = ref_match.group(1) if ref_match else None
            if ref is None and ("MakeStruct" in node_name or "BreakStruct" in node_name):
                struct_match = re.search(
                    r'StructType="[^"]*[/\.]([A-Za-z_][A-Za-z0-9_]+)\'?"',
                    chunk,
                )
                if not struct_match:
                    struct_match = re.search(
                        r'PinName="([A-Z][A-Za-z0-9_]+)"[^)]*Direction="EGPD_Output"',
                        chunk,
                    )
                if not struct_match:
                    struct_match = re.search(
                        r'PinSubCategoryObject="[^"]*UserDefinedStruct\'[^\']*\.([^\']+)\'',
                        chunk,
                    )
                if struct_match:
                    ref = struct_match.group(1)
        if node_type == "K2Node_Select" and ref is None:
            ref = "select"
        if node_type == "K2Node_Self" and ref is None:
            ref = "self"
        if node_type == "K2Node_AddDelegate":
            ref_m = re.search(r'DelegateReference=\([^)]*MemberName="([^"]+)"', chunk)
            if ref_m:
                ref = ref_m.group(1)
            elif ref is None:
                ref = "delegate"
        pin_starts = [m2.start() for m2 in re.finditer(r"CustomProperties Pin \(", chunk)]
        pins = []
        for i, start in enumerate(pin_starts):
            end = pin_starts[i + 1] if i + 1 < len(pin_starts) else start + 1500
            pin_chunk = chunk[start:end]
            pinid_m = re.search(r'PinId=([A-F0-9]{32})', pin_chunk)
            name_m = re.search(r'PinName="([^"]+)"', pin_chunk)
            cat_m = re.search(r'PinType\.PinCategory="([^"]+)"', pin_chunk)
            subcat_m = re.search(r'PinType\.PinSubCategoryObject="([^"]*)"', pin_chunk)
            dir_m = re.search(r'Direction="([^"]+)"', pin_chunk)
            linked_m = re.search(r'LinkedTo=\(([^)]+)\)', pin_chunk)
            if not name_m:
                continue
            linked_pairs = []
            if linked_m:
                linked_pairs = [
                    {"node": ln, "pin": lp}
                    for ln, lp in re.findall(r"(K2Node_\w+)\s+([A-F0-9]{32})", linked_m.group(1))
                ]
            cat = cat_m.group(1) if cat_m else "unknown"
            subcat = subcat_m.group(1) if subcat_m else ""
            pins.append({
                "pin_id": pinid_m.group(1) if pinid_m else "",
                "name": name_m.group(1),
                "type": _resolve_pin_type(cat, subcat) if cat_m else "unknown",
                "direction": dir_m.group(1) if dir_m else "EGPD_Input",
                "linked_to": linked_pairs,
            })
        node_data: dict = {"type": node_type, "ref": ref, "pins": pins}
        if node_type == "K2Node_Select":
            idx_m = re.search(r'IndexPinType=\(PinCategory="([^"]+)"', chunk)
            node_data["select_index_type"] = idx_m.group(1) if idx_m else None
            option_pairs = re.findall(
                r'PinName="(Option \d+)"[^)]*?PinFriendlyName=NSLOCTEXT\s*\(\s*"[^"]*"\s*,\s*"[^"]*"\s*,\s*"([^"]*)"\s*\)',
                chunk,
            )
            if option_pairs:
                node_data["select_options"] = sorted(
                    option_pairs,
                    key=lambda t: int(t[0].split()[1]),
                )
        nodes[node_name] = node_data
    return nodes


def _build_pin_lookup(t3d: str, graph_name: str) -> dict:
    """Map output pin IDs to their source node ref for pure function resolution."""
    pin_to_node: dict[str, str] = {}
    try:
        nodes = _parse_function_graph(t3d, graph_name)
        for node_name, node in nodes.items():
            ref = _clean_name(node.get("ref") or "")
            ntype = node.get("type") or "unknown"
            label = ref if ref else _clean_name(ntype.replace("K2Node_", "").lower())
            for p in node.get("pins", []):
                if p.get("direction") == "EGPD_Output" and p.get("pin_id"):
                    pin_to_node[p["pin_id"]] = label
    except Exception:
        pass
    return pin_to_node


def _clean_body(body: list[str]) -> list[str]:
    """Remove noise entries and clean up body step strings."""
    cleaned = []
    for step in body:
        if step.strip() == "setvariableonpersistentframe":
            continue
        if step.strip() == "adddelegate":
            cleaned.append("bind_delegate")
            continue
        # Drop pure execution wire returns — no semantic content
        if step.startswith("return exec (exec)"):
            continue
        # Drop unresolved condition returns — no info
        if step == "return Condition (bool) <- ?":
            continue
        # Drop internal NewEnumerator noise
        if "NewEnumerator" in step and "<- ?" in step:
            continue
        # Trim trailing spaces from node type names
        step = step.strip()
        # Clean up empty-name nodes
        if step in ("calldelegate", "dynamiccast", "setfieldsinstruct",
                    "mapforeach", "switchenum", "macroinstance"):
            cleaned.append(step)
            continue
        # Collapse callarrayfunction verbosity
        if step.startswith("callarrayfunction "):
            op = step.replace("callarrayfunction ", "").strip()
            cleaned.append(f"array.{op.lower()}")
            continue
        cleaned.append(step)
    return cleaned


def _summarize_graph(
    nodes,
    t3d: str | None = None,
    graph_name: str | None = None,
    entry_node_name: str | None = None,
    pin_to_node: dict | None = None,
):
    by_name = {n: d for n, d in nodes.items()}
    entry = None
    if entry_node_name and entry_node_name in by_name:
        entry = entry_node_name
    else:
        entry = next(
            (n for n, d in by_name.items() if d["type"] == "K2Node_FunctionEntry"),
            None,
        )
        if not entry:
            entry = next(
                (n for n, d in by_name.items() if d["type"] == "K2Node_Event"),
                None,
            )
    if not entry:
        return []
    steps = []
    visited = set()

    def walk(node_name):
        if node_name in visited or node_name not in by_name:
            return
        visited.add(node_name)
        node = by_name[node_name]
        ntype = node["type"]
        ref = node["ref"]
        if ntype == "K2Node_FunctionEntry":
            pass
        elif ntype in ("K2Node_Event", "K2Node_CustomEvent"):
            # Treat event nodes as graph roots; just walk their exec outputs.
            pass
        elif ntype == "K2Node_FunctionResult":
            inputs = [
                p
                for p in node["pins"]
                if p["direction"] == "EGPD_Input"
                and p["name"] not in ("execute", "self")
            ]
            for inp in inputs:
                pin_obj = next(
                    (p for p in node["pins"] if p["name"] == inp["name"]),
                    None,
                )
                first_link = pin_obj["linked_to"][0] if pin_obj and pin_obj.get("linked_to") else None
                source_node = (
                    first_link["node"]
                    if isinstance(first_link, dict)
                    else first_link
                )
                linked_pin = first_link.get("pin") if isinstance(first_link, dict) else None
                source_ref = None
                if source_node and source_node in by_name:
                    source_ref = by_name[source_node].get("ref") or source_node
                else:
                    source_ref = source_node
                if (not source_ref or source_ref == "?") and pin_to_node and linked_pin:
                    source_ref = pin_to_node.get(linked_pin, source_ref)
                steps.append(
                    f"return {_clean_name(inp['name'])} ({inp['type']}) <- {_clean_name(source_ref) if source_ref else '?'}"
                )
        elif ntype == "K2Node_VariableGet":
            steps.append(f"get {_clean_name(ref) if ref else ''}")
        elif ntype == "K2Node_VariableSet":
            steps.append(f"set {_clean_name(ref) if ref else ''}")
        elif ntype == "K2Node_IfThenElse":
            steps.append("branch")
        elif ntype == "K2Node_CallFunction":
            steps.append(f"call {_clean_name(ref) if ref else ''}")
        elif ntype == "K2Node_MakeStruct":
            steps.append(f"make {_clean_name(ref) if ref else 'struct'}")
        elif ntype == "K2Node_BreakStruct":
            steps.append(f"break {_clean_name(ref) if ref else 'struct'}")
        elif ntype == "K2Node_Select":
            idx_type = node.get("select_index_type") or "?"
            options = node.get("select_options") or []
            if options:
                opts_str = ", ".join(f"{name}: {fname}" for name, fname in options)
                steps.append(f"select ({idx_type}) [{opts_str}]")
            else:
                steps.append(f"select ({idx_type})")
        elif ntype == "K2Node_AddDelegate":
            delegate_name = _clean_name(ref) if ref else ""
            steps.append(f"bind_delegate({delegate_name})" if delegate_name else "bind_delegate")
        elif ntype == "K2Node_MacroInstance":
            steps.append(f"macro:{_clean_name(ref)}" if ref else "macroinstance")
        else:
            steps.append(f"{_clean_name(ntype.replace('K2Node_', '').lower())} {_clean_name(ref) if ref else ''}")
        for pin in node["pins"]:
            is_exec_out = (
                pin["type"] == "exec" and pin["direction"] == "EGPD_Output"
            ) or (
                pin["name"] == "then" and pin.get("linked_to")
            )
            if is_exec_out and pin.get("linked_to"):
                for link in pin["linked_to"]:
                    target_node = link["node"] if isinstance(link, dict) else link
                    if target_node:
                        walk(target_node)

    walk(entry)

    if t3d and graph_name:
        select_pattern = rf'K2Node_Select_\d+" ExportPath="[^"]*:{re.escape(graph_name)}\.'
        for sm in re.finditer(select_pattern, t3d):
            spos = sm.start()
            schunk = t3d[spos : spos + 2000]
            if "CustomProperties" not in schunk[:600]:
                continue
            idx_match = re.search(r'IndexPinType=\(PinCategory="([^"]+)"', schunk)
            idx_type = idx_match.group(1) if idx_match else "?"
            options = []
            for opt_m in re.finditer(
                r'PinName="(Option \d+)",PinFriendlyName=NSLOCTEXT\("[^"]*",\s*"[^"]*",\s*"([^"]*)"\)',
                schunk,
            ):
                options.append((opt_m.group(1), opt_m.group(2)))
            options.sort(key=lambda x: int(x[0].split()[1]))
            if options:
                opts_str = ", ".join(fname for _, fname in options)
                steps.append(f"select ({idx_type}) [{opts_str}]")
            else:
                steps.append(f"select ({idx_type})")

    body = _clean_body(steps)
    return body


def handle_get_blueprint(blueprint_name: str, *, include_local_variables: bool = False) -> dict:
    """Full inspection of a Blueprint: components, variables, functions (interfaces list empty; see warnings).

    When include_local_variables is True, skips cache read/write and returns all variables
    (component + local) after stripping scope. Default False: component variables only, cached.
    """
    try:
        path = blueprint_name if blueprint_name.startswith("/Game") else _find_blueprint_path(blueprint_name)
        if not path:
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "ASSET_NOT_FOUND",
                "message": f"Blueprint not found: {blueprint_name}",
                "tool": "get_blueprint",
            }
        # Check cache first (component-scoped variables only are stored)
        if not include_local_variables:
            cached = _cache_get(path)
            if cached is not None:
                return cached
        asset = unreal.EditorAssetLibrary.load_asset(path)
        if not asset:
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "LOAD_FAILED",
                "message": f"Failed to load: {path}",
                "tool": "get_blueprint",
            }
        bp = unreal.BlueprintEditorLibrary.get_blueprint_asset(asset)
        if not bp:
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "NOT_A_BLUEPRINT",
                "message": f"Asset is not a Blueprint: {path}",
                "tool": "get_blueprint",
            }
        gen_class = unreal.BlueprintEditorLibrary.generated_class(bp)

        # Parent class from AssetRegistry ParentClass tag
        parent_class = "Actor"
        try:
            ar = unreal.AssetRegistryHelpers.get_asset_registry()
            ar_assets = ar.get_assets_by_path("/Game", recursive=True)
            for ar_ad in ar_assets:
                ad_path = ar_ad.to_soft_object_path().export_text()
                if "." in ad_path:
                    ad_path = ad_path.split(".")[0]
                if ad_path == path:
                    raw = ar_ad.get_tag_value("ParentClass")
                    if raw:
                        parent_class = raw.split(".")[-1].strip("'")
                    break
        except Exception:
            pass

        warnings: list[dict] = []

        # Interface implementation data is not accessible via UE5 Python API
        # The 'interfaces' field will always be empty — use query_cache with
        # has_function to infer interface implementors by shared function names
        interfaces = []
        warnings.append({
            "code": "INTERFACE_DATA_UNAVAILABLE",
            "message": "Interface implementation list not accessible via UE5 Python API. To find implementors of an interface, use query_cache(tool='query_blueprints', has_function='FunctionName') matching known interface function names.",
        })

        # Components from SubobjectDataSubsystem
        components = []
        try:
            import re
            sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
            if sds:
                handles = sds.k2_gather_subobject_data_for_blueprint(bp)
                # Components from handles[1:] (skip CDO root)
                for h in handles[1:]:
                    data = sds.k2_find_subobject_data_from_handle(h)
                    if not data:
                        continue
                    text = data.export_text()
                    if not text:
                        continue
                    ptr_match = re.search(
                        r'WeakObjectPtr="(?:[^.]+\.)([^\']+)\'[^:]+:([^\']+)\'',
                        text,
                    )
                    if not ptr_match:
                        continue
                    comp_class = ptr_match.group(1)
                    comp_name = ptr_match.group(2).replace("_GEN_VARIABLE", "")
                    node_match = re.search(r'(SCS_Node_\w+)\'\"', text)
                    node_id = node_match.group(1) if node_match else ""
                    components.append({
                        "name": comp_name,
                        "class": comp_class,
                        "scs_node": node_id,
                    })
        except Exception as e:
            warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "section": "components"})

        seen_nodes = set()
        deduped = []
        for c in components:
            if c.get("scs_node") and c["scs_node"] not in seen_nodes:
                seen_nodes.add(c["scs_node"])
                deduped.append(c)
        components = deduped

        name = path.split("/")[-1] if "/" in path else path

        # Variables from JsonObjectGraphFunctionLibrary
        variables = []
        try:
            import json
            import os
            import re

            jog = unreal.JsonObjectGraphFunctionLibrary
            opts = unreal.JsonStringifyOptions()
            filename = jog.write_blueprint_class_to_temp_file(bp, name, opts)
            if filename and os.path.exists(filename):
                with open(filename, "r") as f:
                    content = f.read()
                data = json.loads(content)
                PROP_TYPES = {
                    "BoolProperty",
                    "FloatProperty",
                    "IntProperty",
                    "StrProperty",
                    "NameProperty",
                    "TextProperty",
                    "ObjectProperty",
                    "StructProperty",
                    "ArrayProperty",
                    "MapProperty",
                    "SetProperty",
                    "ByteProperty",
                    "SoftObjectProperty",
                    "ClassProperty",
                    "EnumProperty",
                }
                EXCLUDE_PREFIXES = (
                    "Temp_",
                    "K2Node",
                    "CallFunc_",
                    "CallMath_",
                    "CallLocal_",
                    "K2_",
                )
                EXCLUDE_EXACT = {
                    "None",
                    "self",
                    "execute",
                    "then",
                    "Object",
                    "ReturnValue",
                    "Component",
                    "Actor",
                    "Controller",
                    "Pawn",
                    "Character",
                    "UberGraphFrame",
                    "EntryPoint",
                    "NewLocalVar",
                    "NewLocalVar_0",
                    "NewLocalVar_1",
                    "NewLocalVar_2",
                    "DeltaSeconds",
                    "TriggeredTime",
                    "ElapsedTime",
                }
                def walk(obj, found):
                    if isinstance(obj, list):
                        for i, item in enumerate(obj):
                            if isinstance(item, str) and item in PROP_TYPES:
                                if i + 1 < len(obj) and isinstance(obj[i + 1], str):
                                    var_name = obj[i + 1]
                                    clean_var_name = _clean_name(var_name)
                                    if (
                                        not any(clean_var_name.startswith(p) for p in EXCLUDE_PREFIXES)
                                        and clean_var_name not in EXCLUDE_EXACT
                                    ):
                                        found.add((clean_var_name, item))
                            elif isinstance(item, (dict, list)):
                                walk(item, found)
                    elif isinstance(obj, dict):
                        for v in obj.values():
                            walk(v, found)

                # Get NewVariables from stringify (different format than write_blueprint_class_to_temp_file)
                import json

                stringify_data = None
                try:
                    bp_obj2 = unreal.EditorAssetLibrary.load_asset(path)
                    if bp_obj2:
                        opts2 = unreal.JsonStringifyOptions()
                        stringify_result = unreal.JsonObjectGraphFunctionLibrary.stringify(
                            [bp_obj2], opts2
                        )
                        if stringify_result:
                            stringify_data = json.loads(stringify_result)
                        else:
                            warnings.append({
                                "code": "STRINGIFY_EMPTY",
                                "message": "stringify returned empty result",
                                "section": "NewVariables",
                            })
                    else:
                        warnings.append({
                            "code": "STRINGIFY_NO_ASSET",
                            "message": f"Could not load asset at path: {path}",
                            "section": "NewVariables",
                        })
                except Exception as e:
                    warnings.append({
                        "code": "STRINGIFY_FAIL",
                        "message": str(e),
                        "section": "NewVariables",
                    })
                    stringify_data = None

                # Prefer authoritative NewVariables list when present
                extracted = []
                try:
                    root = None
                    roots = (
                        stringify_data.get("__RootObjects")
                        if isinstance(stringify_data, dict)
                        else None
                    )
                    if isinstance(roots, list) and roots and isinstance(roots[0], dict):
                        root = roots[0]
                    new_vars = root.get("NewVariables") if isinstance(root, dict) else None
                    if isinstance(new_vars, list) and new_vars:
                        for var in new_vars:
                            if not isinstance(var, dict):
                                continue
                            var_name = _clean_name(str(var.get("VarName", "") or ""))
                            if (
                                not var_name
                                or any(var_name.startswith(p) for p in EXCLUDE_PREFIXES)
                                or var_name in EXCLUDE_EXACT
                            ):
                                continue
                            var_type_obj = var.get("VarType", {}) or {}
                            pin_category = str(var_type_obj.get("PinCategory", "unknown") or "unknown")
                            pin_subcat = str(var_type_obj.get("PinSubCategoryObject", "") or "")
                            resolved_type = _resolve_pin_type(pin_category, pin_subcat)
                            extracted.append({"name": var_name, "type": resolved_type})
                    if not extracted and new_vars:
                        warnings.append({
                            "code": "VAR_EXTRACT_EMPTY",
                            "message": f"NewVariables had {len(new_vars)} entries but extracted 0 — check EXCLUDE filters",
                            "section": "NewVariables",
                        })
                except Exception as e:
                    warnings.append({"code": "VAR_EXTRACT_FAIL", "message": str(e), "section": "NewVariables"})
                    extracted = []

                if extracted:
                    variables = extracted
                else:
                    found = set()
                    walk(data, found)
                    variables = [{"name": n, "type": t} for n, t in sorted(found)]
        except Exception as e:
            warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "section": "variables"})

        # Functions from T3D export
        functions = []
        blueprint_t3d = None
        try:
            import os
            import re as _re
            import tempfile

            tmp = tempfile.gettempdir().replace("\\", "/")
            t3d_path = tmp + f"/{name}_funcs.T3D"
            task = unreal.AssetExportTask()
            task.object = asset
            task.filename = t3d_path
            task.selected = False
            task.replace_identical = True
            task.prompt = False
            task.automated = True
            unreal.Exporter.run_asset_export_task(task)

            class_vars_t3d: set[str] = set()
            if os.path.exists(t3d_path):
                with open(t3d_path, "r") as f:
                    t3d = f.read()
                blueprint_t3d = t3d

                class_vars_t3d = _extract_class_variable_names(t3d)

                all_graphs = _re.findall(
                    r'Begin Object Class=/Script/Engine\.EdGraph Name="([^"]+)"',
                    t3d,
                )

                graph_candidates = [
                    g for g in all_graphs if not g.startswith("ExecuteUbergraph_")
                ]

                for graph in graph_candidates:
                    has_function_entry = bool(
                        _re.search(
                            rf'K2Node_FunctionEntry_\d+" ExportPath="[^"]*:{_re.escape(graph)}\.',
                            t3d,
                        )
                    )
                    has_event_entry = bool(
                        _re.search(
                            rf'K2Node_Event_\d+" ExportPath="[^"]*:{_re.escape(graph)}\.',
                            t3d,
                        )
                    )
                    has_custom_event_entry = bool(
                        _re.search(
                            rf'K2Node_CustomEvent_\d+" ExportPath="[^"]*:{_re.escape(graph)}\.',
                            t3d,
                        )
                    )

                    # Regular function graphs (FunctionEntry -> FunctionResult)
                    if has_function_entry:
                        inputs = []
                        try:
                            entry_pattern = rf'K2Node_FunctionEntry_\d+" ExportPath="[^"]*:{_re.escape(graph)}\.'
                            entry_matches = list(_re.finditer(entry_pattern, t3d))
                            for em in entry_matches:
                                epos = em.start()
                                echunk = t3d[epos : epos + 4000]
                                is_second = any(
                                    k in echunk[:600]
                                    for k in [
                                        "CustomProperties",
                                        "VariableReference=",
                                        "FunctionReference=",
                                        "ExtraFlags=",
                                        "LocalVariables",
                                        "NodePosX",
                                        "NodePosY",
                                        "bMadeA",
                                    ]
                                )
                                if not is_second:
                                    continue
                                # Trim to just this node — stop at the next K2Node boundary
                                next_boundaries = [
                                    m.start()
                                    for m in _re.finditer(r'K2Node_\w+_\d+" ExportPath=', echunk)
                                ]
                                if len(next_boundaries) > 1:
                                    echunk = echunk[: next_boundaries[1]]
                                # UserDefinedPin EGPD_Output on FunctionEntry = function input parameter
                                input_pins = _re.findall(
                                    r'UserDefinedPin \(PinName="([^"]+)",PinType=\(PinCategory="([^"]+)"(?:,PinSubCategoryObject="([^"]*)")?[^)]*\),DesiredPinDirection=EGPD_Output',
                                    echunk,
                                )
                                EXCLUDE_INPUT_NAMES = {"Object", "self", "execute", "then", "ReturnValue"}
                                for pin_name, pin_cat, pin_subcat in input_pins:
                                    if pin_name in EXCLUDE_INPUT_NAMES:
                                        continue
                                    type_str = _resolve_pin_type(pin_cat, pin_subcat or "")
                                    inputs.append({"name": _clean_name(pin_name), "type": type_str})
                                if inputs or "UserDefinedPin" in echunk:
                                    break
                        except Exception:
                            pass

                        pattern = rf"K2Node_FunctionResult[^\n]*{_re.escape(name)}:{_re.escape(graph)}\."
                        positions = [m.start() for m in _re.finditer(pattern, t3d)]
                        if not positions:
                            graph_nodes = _parse_function_graph(t3d, graph)
                            pin_to_node = _build_pin_lookup(t3d, graph)
                            body = _summarize_graph(graph_nodes, t3d, graph, pin_to_node=pin_to_node)
                            functions.append(
                                {"name": graph, "inputs": inputs, "outputs": [], "body": body}
                            )
                            continue

                        rpos = positions[-1]
                        chunk = t3d[rpos : rpos + 4000]
                        next_boundaries = [
                            m.start() for m in _re.finditer(r'K2Node_\w+_\d+" ExportPath=', chunk)
                        ]
                        if len(next_boundaries) > 1:
                            chunk = chunk[: next_boundaries[1]]
                        # UserDefinedPin EGPD_Input on FunctionResult = function output parameter
                        output_pins = _re.findall(
                            r'UserDefinedPin \(PinName="([^"]+)",PinType=\(PinCategory="([^"]+)"(?:,PinSubCategoryObject="([^"]*)")?[^)]*\),DesiredPinDirection=EGPD_Input',
                            chunk,
                        )
                        outputs = []
                        for pin_name, pin_cat, pin_subcat in output_pins:
                            if pin_name in ("execute", "then", "self"):
                                continue
                            type_str = _resolve_pin_type(pin_cat, pin_subcat or "")
                            outputs.append({"name": _clean_name(pin_name), "type": type_str})
                        if not outputs:
                            pins = _re.findall(
                                r'PinName="([^"]+)".*?PinType\.PinCategory="([^"]+)"(?:.*?PinType\.PinSubCategoryObject="([^"]*)")?',
                                chunk,
                                _re.DOTALL,
                            )
                            for pin_name, cat, subcat in pins:
                                if pin_name in ("execute", "then", "self"):
                                    continue
                                outputs.append({
                                    "name": _clean_name(pin_name),
                                    "type": _resolve_pin_type(cat, subcat or ""),
                                })

                        graph_nodes = _parse_function_graph(t3d, graph)
                        pin_to_node = _build_pin_lookup(t3d, graph)
                        body = _summarize_graph(graph_nodes, t3d, graph, pin_to_node=pin_to_node)
                        functions.append(
                            {"name": graph, "inputs": inputs, "outputs": outputs, "body": body}
                        )

                    # Event graphs (K2Node_Event -> exec chain)
                    elif has_event_entry or has_custom_event_entry:
                        graph_nodes = _parse_function_graph(t3d, graph)
                        pin_to_node = _build_pin_lookup(t3d, graph)
                        graph_events = []
                        event_starts = []
                        event_pattern = rf'(K2Node_Event_\d+)" ExportPath="[^"]*:{_re.escape(graph)}\.'
                        for em in _re.finditer(event_pattern, t3d):
                            event_starts.append(("event", em))
                        custom_pattern = rf'(K2Node_CustomEvent_\d+)" ExportPath="[^"]*:{_re.escape(graph)}\.'
                        for cm in _re.finditer(custom_pattern, t3d):
                            event_starts.append(("custom", cm))
                        event_starts.sort(key=lambda item: item[1].start())

                        for event_kind, em in event_starts:
                            event_node_name = em.group(1)
                            epos = em.start()
                            echunk = t3d[epos : epos + 4000]
                            if "NodePosX" not in echunk[:600]:
                                continue
                            enabled_m = _re.search(r"EnabledState=(\w+)", echunk)
                            if enabled_m and enabled_m.group(1) == "Disabled":
                                continue

                            if event_kind == "custom":
                                name_m = _re.search(r'CustomFunctionName="([^"]+)"', echunk)
                                if not name_m:
                                    continue
                                event_name = name_m.group(1)
                                event_parent = "custom"
                            else:
                                event_name_m = _re.search(r'MemberName="([^"]+)"', echunk)
                                event_name = event_name_m.group(1) if event_name_m else event_node_name
                                parent_m = _re.search(
                                    r'MemberParent="[^"]*[/\.]([A-Za-z_][A-Za-z0-9_]+)\'',
                                    echunk,
                                )
                                event_parent = parent_m.group(1) if parent_m else parent_class

                            export_path_m = _re.search(r'ExportPath="([^"]+)"', echunk)
                            export_path = export_path_m.group(1) if export_path_m else ""
                            graph_name_m = _re.search(r':([^.]+)\.K2Node_(?:Event|CustomEvent)', export_path)
                            graph_name = graph_name_m.group(1) if graph_name_m else graph

                            body = _summarize_graph(
                                graph_nodes,
                                t3d,
                                graph,
                                entry_node_name=event_node_name,
                                pin_to_node=pin_to_node,
                            )
                            if not body:
                                body = ["[exec chain not traceable — macro or custom node has no LinkedTo in T3D export]"]
                            graph_events.append({
                                "name": graph_name,
                                "kind": "event",
                                "event": event_name,
                                "parent": event_parent,
                                "inputs": [],
                                "outputs": [],
                                "body": body,
                            })
                        # Second pass: surface any enabled events not captured by the exec walker.
                        already_captured = {e.get("event") for e in graph_events}
                        fallback_body = ["[exec chain not traceable — no LinkedTo on exec pins in T3D export]"]

                        for em in _re.finditer(event_pattern, t3d):
                            echunk = t3d[em.start() : em.start() + 400]
                            if "NodePosX" not in echunk:
                                continue
                            enabled_m = _re.search(r"EnabledState=(\w+)", echunk)
                            if enabled_m and enabled_m.group(1) == "Disabled":
                                continue
                            member_m = _re.search(r'MemberName="([^"]+)"', echunk)
                            event_name = member_m.group(1) if member_m else None
                            if not event_name or event_name in already_captured:
                                continue
                            parent_m = _re.search(
                                r'MemberParent="[^"]*[/\.]([A-Za-z_][A-Za-z0-9_]+)\'',
                                echunk,
                            )
                            graph_events.append({
                                "name": graph,
                                "kind": "event",
                                "event": event_name,
                                "parent": parent_m.group(1) if parent_m else "unknown",
                                "inputs": [],
                                "outputs": [],
                                "body": fallback_body,
                            })
                            already_captured.add(event_name)

                        for cm in _re.finditer(custom_pattern, t3d):
                            cchunk = t3d[cm.start() : cm.start() + 400]
                            if "NodePosX" not in cchunk:
                                continue
                            enabled_m = _re.search(r"EnabledState=(\w+)", cchunk)
                            if enabled_m and enabled_m.group(1) == "Disabled":
                                continue
                            member_m = _re.search(r'CustomFunctionName="([^"]+)"', cchunk)
                            event_name = member_m.group(1) if member_m else None
                            if not event_name or event_name in already_captured:
                                continue
                            graph_events.append({
                                "name": graph,
                                "kind": "event",
                                "event": event_name,
                                "parent": "custom",
                                "inputs": [],
                                "outputs": [],
                                "body": fallback_body,
                            })
                            already_captured.add(event_name)

                        functions.extend(graph_events)

            class_vars = class_vars_t3d | _collect_bpgc_hierarchy_variable_names(gen_class)
            for v in variables:
                if class_vars:
                    v["scope"] = "component" if v["name"] in class_vars else "local"
                else:
                    v["scope"] = "component"
        except Exception as e:
            warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "section": "functions"})

        for v in variables:
            if "scope" not in v:
                v["scope"] = "component"

        if not include_local_variables:
            variables = [v for v in variables if v.get("scope") == "component"]
        for v in variables:
            v.pop("scope", None)

        def _is_ubergraph_stub(func: dict) -> bool:
            # Never filter event functions — they are real events.
            if func.get("kind") == "event":
                return False
            body = func.get("body", [])
            if not body:
                return False
            return all(
                step.startswith("call ExecuteUbergraph_")
                or step == "setvariableonpersistentframe"
                for step in body
            )

        filtered_functions = []
        for func in functions:
            if _is_ubergraph_stub(func):
                continue
            filtered_functions.append(func)
        functions = filtered_functions

        # Warn if any event bodies are untraceable in exported T3D.
        untraceable = [
            f.get("event") for f in functions
            if f.get("kind") == "event" and (
                not f.get("body")
                or any("not traceable" in s for s in f.get("body", []))
            )
        ]
        if untraceable:
            warnings.append({
                "code": "EVENTGRAPH_PARTIAL",
                "message": f"EventGraph exec chains not fully traceable for: {', '.join(untraceable)}. UE5 T3D export does not serialize exec pin LinkedTo data for macro/custom event nodes. Actual body may differ from what is shown.",
            })

        result = {
            "name": name,
            "path": path,
            "parent_class": parent_class,
            "interfaces": interfaces,
            "components": components,
            "variables": variables,
            "functions": functions,
            "warnings": warnings,
        }
        if not include_local_variables:
            _cache_set(path, result)
        return result
    except Exception as e:
        return {
            "error": True,
            "type": "RUNTIME",
            "code": "HANDLER_ERROR",
            "message": str(e),
            "tool": "get_blueprint",
        }


def handle_list_components(blueprint_name: str) -> dict:
    """List all components on a Blueprint actor."""
    try:
        result = handle_get_blueprint(blueprint_name)
        if isinstance(result, dict) and result.get("error"):
            return result
        return result.get("components", [])
    except Exception as e:
        return {
            "error": True,
            "type": "RUNTIME",
            "code": "HANDLER_ERROR",
            "message": str(e),
            "tool": "list_components",
        }


def handle_list_interfaces() -> dict:
    """List all Blueprint Interfaces in the project."""
    try:
        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        if not ar:
            return {"error": True, "type": "RUNTIME", "code": "ASSET_REGISTRY_UNAVAILABLE", "message": "Asset registry not available", "tool": "list_interfaces"}
        assets = ar.get_assets_by_path("/Game", recursive=True)
        if not assets:
            assets = []
        results = []
        for ad in assets:
            if not unreal.AssetRegistryHelpers.is_valid(ad):
                continue
            blueprint_type = ad.get_tag_value("BlueprintType")
            if blueprint_type != "BPTYPE_Interface":
                continue
            path = ad.to_soft_object_path().export_text()
            if "." in path:
                path = path.split(".")[0]
            name = path.split("/")[-1] if "/" in path else path
            functions = []
            try:
                obj = unreal.AssetRegistryHelpers.get_asset(ad)
                if obj and hasattr(obj, "functions") and obj.functions:
                    for f in obj.functions:
                        if f and hasattr(f, "get_name"):
                            functions.append(f.get_name())
            except Exception:
                pass
            results.append({"name": name, "path": path, "functions": functions})
        return results
    except Exception as e:
        return {"error": True, "type": "RUNTIME", "code": "HANDLER_ERROR", "message": str(e), "tool": "list_interfaces"}


def handle_get_interface(interface_name: str) -> dict:
    """Inspect a Blueprint Interface — delegates to handle_get_blueprint since interfaces are Blueprint assets."""
    return handle_get_blueprint(blueprint_name=interface_name)


def handle_find_event_bindings(
    event_name: str | None = None,
    interface_name: str | None = None,
    use_live_scan: bool = True,
    refresh_index: bool = False,
) -> dict:
    """Find Blueprints that bind or implement a given event or interface. V1: live-scan only."""
    try:
        event_bindings = {}
        interface_implementations = {}
        warnings: list[dict] = []
        ar_filter = unreal.ARFilter(package_paths=["/Game"], recursive_paths=True)
        assets = unreal.AssetRegistryHelpers.get_blueprint_assets(ar_filter)
        if not assets:
            assets = []
        for ad in assets:
            if not unreal.AssetRegistryHelpers.is_valid(ad):
                continue
            path = ad.to_soft_object_path().export_text()
            if "." in path:
                path = path.split(".")[0]
            bp_name = path.split("/")[-1] if "/" in path else path
            try:
                obj = unreal.EditorAssetLibrary.load_asset(path)
                if not obj:
                    continue
                bp = unreal.BlueprintEditorLibrary.get_blueprint_asset(obj)
                if not bp:
                    continue
                gen_class = unreal.BlueprintEditorLibrary.generated_class(bp)
                if gen_class and hasattr(gen_class, "interfaces") and gen_class.interfaces:
                    for iface in gen_class.interfaces:
                        if iface and hasattr(iface, "get_name"):
                            iname = iface.get_name()
                            if interface_name and interface_name not in iname:
                                continue
                            if iname not in interface_implementations:
                                interface_implementations[iname] = []
                            interface_implementations[iname].append({"blueprint": bp_name, "implementationType": "Full"})
            except Exception as e:
                warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "blueprint": bp_name})
                continue
        return {
            "source": "live_scan",
            "event_bindings": event_bindings,
            "interface_implementations": interface_implementations,
            "warnings": warnings,
        }
    except Exception as e:
        return {"error": True, "type": "RUNTIME", "code": "HANDLER_ERROR", "message": str(e), "tool": "find_event_bindings"}


def handle_asset_search(
    asset_class: str | None = None,
    name_filter: str | None = None,
) -> dict:
    """Search asset registry by class type and optional name filter."""
    try:
        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        if not ar:
            return {"error": True, "type": "RUNTIME", "code": "ASSET_REGISTRY_UNAVAILABLE", "message": "Asset registry not available", "tool": "asset_search"}
        assets = ar.get_assets_by_path("/Game", recursive=True)
        if not assets:
            assets = []
        results = []
        for ad in assets:
            if not unreal.AssetRegistryHelpers.is_valid(ad):
                continue
            class_name = str(ad.asset_class_path.asset_name) if hasattr(ad, "asset_class_path") and ad.asset_class_path else "Object"
            if asset_class and asset_class.lower() not in class_name.lower():
                continue
            path = ad.to_soft_object_path().export_text()
            if "." in path:
                path = path.split(".")[0]
            name = path.split("/")[-1] if "/" in path else path
            if name_filter and name_filter.lower() not in name.lower():
                continue
            size = 0
            if hasattr(ad, "get_tag_value") and ad.get_tag_value("AssetBundleData"):
                pass
            results.append({"name": name, "path": path, "assetClass": class_name, "size": size})
        return results
    except Exception as e:
        return {"error": True, "type": "RUNTIME", "code": "HANDLER_ERROR", "message": str(e), "tool": "asset_search"}


def handle_get_data_asset(asset_name: str) -> dict:
    """Read property values from a Blueprint Data Asset instance."""
    try:
        def _clean_value_keys(value):
            if isinstance(value, dict):
                cleaned = {}
                for k, v in value.items():
                    if k in {"__UObject", "NativeClass"}:
                        continue
                    key = _clean_name(str(k))
                    cleaned[key] = _clean_value_keys(v)
                return cleaned
            if isinstance(value, list):
                return [_clean_value_keys(v) for v in value]
            return value

        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        if not ar:
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "ASSET_REGISTRY_UNAVAILABLE",
                "message": "Asset registry not available",
                "tool": "get_data_asset",
            }

        assets = ar.get_assets_by_path("/Game", recursive=True) or []
        matches = []
        for ad in assets:
            if not unreal.AssetRegistryHelpers.is_valid(ad):
                continue
            path = ad.to_soft_object_path().export_text()
            if "." in path:
                path = path.split(".")[0]
            name = path.split("/")[-1] if "/" in path else path
            class_name = str(ad.asset_class_path.asset_name) if hasattr(ad, "asset_class_path") and ad.asset_class_path else "Object"
            if asset_name.lower() in name.lower():
                matches.append({"path": path, "name": name, "class": class_name})

        expanded_matches = list(matches)
        for match in matches:
            if match.get("class") != "Blueprint":
                continue
            generated_class = f"{match['name']}_C"
            for ad in assets:
                if not unreal.AssetRegistryHelpers.is_valid(ad):
                    continue
                class_name = str(ad.asset_class_path.asset_name) if hasattr(ad, "asset_class_path") and ad.asset_class_path else "Object"
                if class_name != generated_class:
                    continue
                path = ad.to_soft_object_path().export_text()
                if "." in path:
                    path = path.split(".")[0]
                name = path.split("/")[-1] if "/" in path else path
                expanded_matches.append({"path": path, "name": name, "class": class_name})

        for match in expanded_matches:
            class_name = match.get("class", "")
            if not class_name.endswith("_C") or class_name == "Blueprint":
                continue
            path = match["path"]
            obj = unreal.EditorAssetLibrary.load_asset(path)
            if not obj:
                continue
            opts = unreal.JsonStringifyOptions()
            result = unreal.JsonObjectGraphFunctionLibrary.stringify([obj], opts)
            if not result:
                continue
            data = json.loads(result)
            root = data.get("__RootObjects", [{}])[0] if isinstance(data, dict) else {}
            if not isinstance(root, dict):
                root = {}
            properties = _clean_value_keys(root)
            return {
                "name": asset_name,
                "path": path,
                "class": class_name,
                "properties": properties,
            }

        return {
            "error": True,
            "type": "NOT_FOUND",
            "code": "DATA_ASSET_NOT_FOUND",
            "message": f"No matching Data Asset instance found for: {asset_name}",
            "tool": "get_data_asset",
        }
    except Exception as e:
        return {
            "error": True,
            "type": "RUNTIME",
            "code": "HANDLER_ERROR",
            "message": str(e),
            "tool": "get_data_asset",
        }


def handle_get_struct(struct_name: str) -> dict:
    """Inspect a UserDefinedStruct asset and return field names/types."""
    try:
        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        if not ar:
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "ASSET_REGISTRY_UNAVAILABLE",
                "message": "Asset registry not available",
                "tool": "get_struct",
            }

        assets = ar.get_assets_by_path("/Game", recursive=True) or []
        target_path = None
        target_name = None
        for ad in assets:
            if not unreal.AssetRegistryHelpers.is_valid(ad):
                continue
            class_name = str(ad.asset_class_path.asset_name) if hasattr(ad, "asset_class_path") and ad.asset_class_path else ""
            if class_name != "UserDefinedStruct":
                continue
            path = ad.to_soft_object_path().export_text()
            if "." in path:
                path = path.split(".")[0]
            name = path.split("/")[-1] if "/" in path else path
            if struct_name.lower() in name.lower():
                target_path = path
                target_name = name
                break

        if not target_path:
            return {
                "error": True,
                "type": "NOT_FOUND",
                "code": "STRUCT_NOT_FOUND",
                "message": f"No matching UserDefinedStruct found for: {struct_name}",
                "tool": "get_struct",
            }

        obj = unreal.EditorAssetLibrary.load_asset(target_path)
        if not obj:
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "LOAD_FAILED",
                "message": f"Failed to load struct asset: {target_path}",
                "tool": "get_struct",
            }

        opts = unreal.JsonStringifyOptions()
        result = unreal.JsonObjectGraphFunctionLibrary.stringify([obj], opts)
        if not result:
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "STRINGIFY_EMPTY",
                "message": "Json stringify returned empty result",
                "tool": "get_struct",
            }

        data = json.loads(result)
        root = data.get("__RootObjects", [{}])[0] if isinstance(data, dict) else {}
        if not isinstance(root, dict):
            root = {}
        editor_data = root.get("EditorData", {})
        if not isinstance(editor_data, dict):
            editor_data = {}
        var_descs = editor_data.get("VariablesDescriptions", [])
        if not isinstance(var_descs, list):
            var_descs = []

        fields = []
        for var in var_descs:
            if not isinstance(var, dict):
                continue
            friendly_name = var.get("FriendlyName") or _clean_name(str(var.get("VarName", "") or ""))
            category = var.get("Category", "unknown")
            subcat = var.get("SubCategoryObject", "") or ""
            resolved_type = _resolve_pin_type(str(category), str(subcat))
            fields.append({
                "name": friendly_name,
                "type": resolved_type,
            })

        return {
            "name": target_name,
            "path": target_path,
            "fields": fields,
        }
    except Exception as e:
        return {
            "error": True,
            "type": "RUNTIME",
            "code": "HANDLER_ERROR",
            "message": str(e),
            "tool": "get_struct",
        }


def handle_get_material(material_name: str) -> dict:
    """Inspect a Material or MaterialFunction via T3D export: parameters, outputs, function calls."""
    try:
        import os
        import tempfile

        name_q = (material_name or "").strip()
        if not name_q:
            return {
                "error": True,
                "type": "VALIDATION",
                "code": "INVALID_INPUT",
                "message": "material_name is required",
                "tool": "get_material",
            }

        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        if not ar:
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "ASSET_REGISTRY_UNAVAILABLE",
                "message": "Asset registry not available",
                "tool": "get_material",
            }

        assets = ar.get_assets_by_path("/Game", recursive=True) or []
        target_path = None
        target_name = None
        target_class = None
        for ad in assets:
            if not unreal.AssetRegistryHelpers.is_valid(ad):
                continue
            class_name = str(ad.asset_class_path.asset_name) if hasattr(ad, "asset_class_path") and ad.asset_class_path else ""
            if class_name not in ("Material", "MaterialFunction"):
                continue
            path = ad.to_soft_object_path().export_text()
            if "." in path:
                path = path.split(".")[0]
            asset_basename = path.split("/")[-1] if "/" in path else path
            if name_q.lower() in asset_basename.lower():
                target_path = path
                target_name = asset_basename
                target_class = class_name
                break

        if not target_path:
            return {
                "error": True,
                "type": "NOT_FOUND",
                "code": "MATERIAL_NOT_FOUND",
                "message": f"No matching Material or MaterialFunction found for: {material_name}",
                "tool": "get_material",
            }

        asset = unreal.EditorAssetLibrary.load_asset(target_path)
        if not asset:
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "LOAD_FAILED",
                "message": f"Failed to load material asset: {target_path}",
                "tool": "get_material",
            }

        tmp = tempfile.gettempdir().replace("\\", "/")
        safe_stub = re.sub(r"[^\w\-]+", "_", target_name)[:80] or "material"
        t3d_path = tmp + f"/{safe_stub}_mat.T3D"
        task = unreal.AssetExportTask()
        task.object = asset
        task.filename = t3d_path
        task.selected = False
        task.replace_identical = True
        task.prompt = False
        task.automated = True
        unreal.Exporter.run_asset_export_task(task)

        if not os.path.exists(t3d_path):
            return {
                "error": True,
                "type": "RUNTIME",
                "code": "EXPORT_FAILED",
                "message": "T3D export did not produce a file",
                "tool": "get_material",
            }

        try:
            with open(t3d_path, "r", encoding="utf-8", errors="replace") as f:
                t3d = f.read()
        finally:
            try:
                os.unlink(t3d_path)
            except OSError:
                pass

        outputs: dict = {}
        editor_data_m = re.search(
            r'Begin Object Name="\w+EditorOnlyData"(.*?)End Object',
            t3d,
            re.DOTALL,
        )
        if editor_data_m:
            editor_chunk = editor_data_m.group(1)
            for slot in (
                "EmissiveColor",
                "Opacity",
                "BaseColor",
                "Roughness",
                "Metallic",
                "Normal",
                "WorldPositionOffset",
            ):
                slot_m = re.search(
                    rf'{slot}=\(Expression="[^"]*\'[^:]+:(\w+)\'"\)',
                    editor_chunk,
                )
                if slot_m:
                    outputs[slot] = slot_m.group(1)

        scalar_params = []
        for m in re.finditer(r'Begin Object Name="(MaterialExpressionScalarParameter_\d+)"', t3d):
            chunk = t3d[m.start() : m.start() + 600]
            param_name_m = re.search(r'ParameterName="([^"]+)"', chunk)
            default_m = re.search(r"DefaultValue=([0-9.\-]+)", chunk)
            if param_name_m:
                scalar_params.append({
                    "name": param_name_m.group(1),
                    "default": float(default_m.group(1)) if default_m else None,
                })

        vector_params = []
        for m in re.finditer(r'Begin Object Name="(MaterialExpressionVectorParameter_\d+)"', t3d):
            chunk = t3d[m.start() : m.start() + 600]
            param_name_m = re.search(r'ParameterName="([^"]+)"', chunk)
            default_m = re.search(r"DefaultValue=\(([^)]+)\)", chunk)
            if param_name_m:
                vector_params.append({
                    "name": param_name_m.group(1),
                    "default": default_m.group(1) if default_m else None,
                })

        texture_params = []
        for m in re.finditer(
            r'Begin Object Name="(MaterialExpressionTextureSampleParameter\w*_\d+)"',
            t3d,
        ):
            chunk = t3d[m.start() : m.start() + 600]
            param_name_m = re.search(r'ParameterName="([^"]+)"', chunk)
            texture_m = re.search(r'Texture="/[^"]*[/\.]([A-Za-z_][A-Za-z0-9_]+)\'', chunk)
            if param_name_m:
                texture_params.append({
                    "name": param_name_m.group(1),
                    "default_texture": texture_m.group(1) if texture_m else None,
                })

        function_calls = []
        for m in re.finditer(
            r'Begin Object Name="(MaterialExpressionMaterialFunctionCall_\d+)"',
            t3d,
        ):
            chunk = t3d[m.start() : m.start() + 600]
            func_m = re.search(
                r'MaterialFunction="/[^"]*[/\.]([A-Za-z_][A-Za-z0-9_]+)\'',
                chunk,
            )
            if func_m and func_m.group(1) not in function_calls:
                function_calls.append(func_m.group(1))

        return {
            "name": target_name,
            "path": target_path,
            "class": target_class,
            "outputs": outputs,
            "scalar_parameters": scalar_params,
            "vector_parameters": vector_params,
            "texture_parameters": texture_params,
            "material_functions": function_calls,
        }
    except Exception as e:
        return {
            "error": True,
            "type": "RUNTIME",
            "code": "HANDLER_ERROR",
            "message": str(e),
            "tool": "get_material",
        }


def handle_get_variables(blueprint_name: str, include_locals: bool = False) -> dict:
    """All variables on a Blueprint: name, type, default value, visibility flags."""
    try:
        result = handle_get_blueprint(
            blueprint_name, include_local_variables=include_locals
        )
        if isinstance(result, dict) and result.get("error"):
            return result
        return {"variables": list(result.get("variables", []))}
    except Exception as e:
        return {
            "error": True,
            "type": "RUNTIME",
            "code": "HANDLER_ERROR",
            "message": str(e),
            "tool": "get_variables",
        }
