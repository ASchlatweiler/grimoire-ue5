import os
import json
import sqlite3
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("grimoire-query")


def _get_cache_path() -> str:
    import tomllib
    config_path = os.environ.get("GRIMOIRE_CONFIG", "config.toml")
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    root = config["project"]["root"]
    # Normalize ALL possible path separators
    root = root.replace("\\\\", "/").replace("\\", "/").rstrip("/")
    return root + "/Saved/Grimoire/cache.db"


def _query(sql: str, params: tuple = ()) -> list:
    db_path = _get_cache_path()
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


@mcp.tool()
def query_blueprints(
    parent_class: str | None = None,
    has_function: str | None = None,
    has_variable: str | None = None,
    references_type: str | None = None,
) -> str:
    """Search cached blueprints by parent class, function name, variable name, or type reference."""
    rows = _query("SELECT asset_path, result_json FROM blueprint_cache")
    results = []
    for asset_path, result_json in rows:
        try:
            bp = json.loads(result_json)
        except Exception:
            continue
        if parent_class and parent_class.lower() not in (bp.get("parent_class") or "").lower():
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
    return json.dumps({"count": len(results), "results": results})


@mcp.tool()
def query_functions(
    name_filter: str | None = None,
    input_type: str | None = None,
    output_type: str | None = None,
) -> str:
    """Search all cached function signatures across the project by name, input type, or output type."""
    rows = _query("SELECT asset_path, result_json FROM blueprint_cache")
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
    return json.dumps({"count": len(results), "results": results})


@mcp.tool()
def query_references(type_name: str) -> str:
    """Find all cached blueprints that reference a given type, struct, asset, or class name."""
    rows = _query("SELECT asset_path, result_json FROM blueprint_cache")
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
    return json.dumps({"count": len(results), "results": results})


if __name__ == "__main__":
    mcp.run(transport="stdio")
