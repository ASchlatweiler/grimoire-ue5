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
            result_json TEXT
        )
    """
    )
    conn.commit()
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
            "SELECT modified_time, result_json FROM blueprint_cache WHERE asset_path = ?",
            (asset_path,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        cached_modified, result_json = row
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
               (asset_path, modified_time, cached_at, result_json)
               VALUES (?, ?, ?, ?)""",
            (asset_path, modified_time, time.time(), json.dumps(result)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def handle_query_blueprints(
    parent_class: str | None = None,
    has_function: str | None = None,
    has_variable: str | None = None,
    references_type: str | None = None,
) -> dict:
    """Search cached blueprints by parent class, function name, variable name, or type reference."""
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
                if not any(references_type.lower() in t.lower() for t in all_types):
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
        chunk = t3d[pos : pos + 5000]
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
        ref_match = re.search(r'(?:Variable|Function)Reference=\([^)]*MemberName="([^"]+)"', pre_pins)
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
        node_type = re.sub(r"_\d+$", "", node_name)
        pin_starts = [m2.start() for m2 in re.finditer(r"CustomProperties Pin \(", chunk)]
        pins = []
        for i, start in enumerate(pin_starts):
            end = pin_starts[i + 1] if i + 1 < len(pin_starts) else start + 1500
            pin_chunk = chunk[start:end]
            name_m = re.search(r'PinName="([^"]+)"', pin_chunk)
            cat_m = re.search(r'PinType\.PinCategory="([^"]+)"', pin_chunk)
            dir_m = re.search(r'Direction="([^"]+)"', pin_chunk)
            linked_m = re.search(r'LinkedTo=\(([^)]+)\)', pin_chunk)
            if not name_m:
                continue
            linked_nodes = (
                re.findall(r"(K2Node_\w+)\s+[A-F0-9]+", linked_m.group(1))
                if linked_m
                else []
            )
            pins.append({
                "name": name_m.group(1),
                "type": cat_m.group(1) if cat_m else "unknown",
                "direction": dir_m.group(1) if dir_m else "EGPD_Input",
                "linked_to": linked_nodes,
            })
        nodes[node_name] = {"type": node_type, "ref": ref, "pins": pins}
    return nodes


def _summarize_graph(nodes):
    by_name = {n: d for n, d in nodes.items()}
    entry = next(
        (n for n, d in by_name.items() if d["type"] == "K2Node_FunctionEntry"),
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
        elif ntype == "K2Node_FunctionResult":
            inputs = [
                p
                for p in node["pins"]
                if p["direction"] == "EGPD_Input"
                and p["name"] not in ("execute", "self")
            ]
            for inp in inputs:
                source = next(
                    (
                        p["linked_to"][0]
                        for p in node["pins"]
                        if p["name"] == inp["name"] and p["linked_to"]
                    ),
                    None,
                )
                source_ref = (
                    by_name[source]["ref"]
                    if source and source in by_name
                    else source
                )
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
        else:
            steps.append(f"{_clean_name(ntype.replace('K2Node_', '').lower())} {_clean_name(ref) if ref else ''}")
        for pin in node["pins"]:
            if pin["type"] == "exec" and pin["direction"] == "EGPD_Output" and pin["linked_to"]:
                for target in pin["linked_to"]:
                    walk(target)

    walk(entry)
    return steps


def handle_get_blueprint(blueprint_name: str) -> dict:
    """Full inspection of a Blueprint: components, variables, functions, interfaces."""
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
        # Check cache first
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

                found = set()
                walk(data, found)
                variables = [{"name": n, "type": t} for n, t in sorted(found)]
        except Exception as e:
            warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "section": "variables"})

        # Functions from T3D export
        functions = []
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

            if os.path.exists(t3d_path):
                with open(t3d_path, "r") as f:
                    t3d = f.read()

                class_vars = _extract_class_variable_names(t3d)
                for v in variables:
                    if class_vars:
                        v["scope"] = "component" if v["name"] in class_vars else "local"
                    else:
                        v["scope"] = "component"

                all_graphs = _re.findall(
                    r'Begin Object Class=/Script/Engine\.EdGraph Name="([^"]+)"',
                    t3d,
                )
                func_graphs = [g for g in all_graphs if g != "EventGraph"]

                for graph in func_graphs:
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
                                m.start() for m in _re.finditer(r'K2Node_\w+_\d+" ExportPath=', echunk)
                            ]
                            if len(next_boundaries) > 1:
                                echunk = echunk[: next_boundaries[1]]
                            # UserDefinedPin EGPD_Output on FunctionEntry = function input parameter
                            input_pins = _re.findall(
                                r'UserDefinedPin \(PinName="([^"]+)",PinType=\(PinCategory="([^"]+)"(?:,PinSubCategoryObject="[^"]*\.([^\'".]+)\'?")?[^)]*\),DesiredPinDirection=EGPD_Output',
                                echunk,
                            )
                            EXCLUDE_INPUT_NAMES = {"Object", "self", "execute", "then", "ReturnValue"}
                            for pin_name, pin_cat, pin_subtype in input_pins:
                                if pin_name in EXCLUDE_INPUT_NAMES:
                                    continue
                                type_str = pin_subtype if pin_subtype else pin_cat
                                inputs.append({"name": _clean_name(pin_name), "type": type_str})
                            if inputs or "UserDefinedPin" in echunk:
                                break
                    except Exception:
                        pass

                    pattern = rf"K2Node_FunctionResult[^\n]*{_re.escape(name)}:{_re.escape(graph)}\."
                    positions = [m.start() for m in _re.finditer(pattern, t3d)]
                    if len(positions) < 2:
                        graph_nodes = _parse_function_graph(t3d, graph)
                        body = _summarize_graph(graph_nodes)
                        functions.append({"name": graph, "inputs": inputs, "outputs": [], "body": body})
                        continue

                    chunk = t3d[positions[-1] : positions[-1] + 2000]
                    pins = _re.findall(
                        r'PinName="([^"]+)".*?PinType\.PinCategory="([^"]+)"',
                        chunk,
                        _re.DOTALL,
                    )

                    outputs = []
                    for pin_name, cat in pins:
                        if pin_name in ("execute", "then", "self"):
                            continue
                        outputs.append({"name": _clean_name(pin_name), "type": cat})

                    graph_nodes = _parse_function_graph(t3d, graph)
                    body = _summarize_graph(graph_nodes)
                    functions.append({"name": graph, "inputs": inputs, "outputs": outputs, "body": body})
        except Exception as e:
            warnings.append({"code": "PARTIAL_PARSE", "message": str(e), "section": "functions"})

        for v in variables:
            if "scope" not in v:
                v["scope"] = "component"

        # Interfaces
        interfaces = []
        if hasattr(gen_class, "interfaces") and gen_class.interfaces:
            for iface in gen_class.interfaces:
                if iface and hasattr(iface, "get_name"):
                    interfaces.append(iface.get_name())

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
    """Inspect a Blueprint Interface: function signatures, input/output pins."""
    try:
        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        if not ar:
            return {"error": True, "type": "RUNTIME", "code": "ASSET_REGISTRY_UNAVAILABLE", "message": "Asset registry not available", "tool": "get_interface"}
        assets = ar.get_assets_by_path("/Game", recursive=True)
        if not assets:
            assets = []
        path = None
        for ad in assets:
            if not unreal.AssetRegistryHelpers.is_valid(ad):
                continue
            p = ad.to_soft_object_path().export_text()
            if "." in p:
                p = p.split(".")[0]
            name = p.split("/")[-1] if "/" in p else p
            if name == interface_name or (interface_name.startswith("/Game") and p == interface_name):
                path = p
                break
        if not path:
            return {"error": True, "type": "RUNTIME", "code": "ASSET_NOT_FOUND", "message": f"Interface not found: {interface_name}", "tool": "get_interface"}
        obj = unreal.EditorAssetLibrary.load_asset(path)
        if not obj:
            return {"error": True, "type": "RUNTIME", "code": "LOAD_FAILED", "message": f"Failed to load: {path}", "tool": "get_interface"}
        functions = []
        if hasattr(obj, "functions") and obj.functions:
            for f in obj.functions:
                if not f:
                    continue
                fn_name = getattr(f, "get_name", lambda: "Unknown")()
                inputs = []
                outputs = []
                if hasattr(f, "get_input_pins"):
                    for pin in f.get_input_pins() or []:
                        inputs.append({"name": getattr(pin, "pin_name", str(pin)), "type": str(getattr(pin, "pin_type", ""))})
                elif hasattr(f, "inputs"):
                    for pin in f.inputs or []:
                        inputs.append({"name": str(getattr(pin, "pin_name", pin)), "type": "any"})
                if hasattr(f, "get_output_pins"):
                    for pin in f.get_output_pins() or []:
                        outputs.append({"name": getattr(pin, "pin_name", str(pin)), "type": str(getattr(pin, "pin_type", ""))})
                elif hasattr(f, "outputs"):
                    for pin in f.outputs or []:
                        outputs.append({"name": str(getattr(pin, "pin_name", pin)), "type": "any"})
                functions.append({"name": str(fn_name), "inputs": inputs, "outputs": outputs})
        name = path.split("/")[-1] if "/" in path else path
        return {"name": name, "functions": functions}
    except Exception as e:
        return {"error": True, "type": "RUNTIME", "code": "HANDLER_ERROR", "message": str(e), "tool": "get_interface"}


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


def handle_get_variables(blueprint_name: str) -> dict:
    """All variables on a Blueprint: name, type, default value, visibility flags."""
    try:
        result = handle_get_blueprint(blueprint_name)
        if isinstance(result, dict) and result.get("error"):
            return result
        return result.get("variables", [])
    except Exception as e:
        return {
            "error": True,
            "type": "RUNTIME",
            "code": "HANDLER_ERROR",
            "message": str(e),
            "tool": "get_variables",
        }
