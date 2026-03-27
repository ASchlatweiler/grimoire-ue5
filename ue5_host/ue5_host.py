"""
UE5 Context Bridge — Unreal Host

Runs inside the UE5 editor as a persistent background script.
Listens on TCP (127.0.0.1:65432), dispatches tool requests to the game thread,
returns JSON responses.

Add to: Project Settings > Plugins > Python > Startup Scripts
Path: ue5_host (or full path to this module)
"""

import json
import os
import socket
import threading
import time
from queue import Empty, Queue

# Unreal module — only available when running inside UE5 editor
try:
    import unreal
except ImportError:
    unreal = None

# Default port — override via UE5_MCP_PORT env var for multi-editor
try:
    DEFAULT_PORT = int(os.environ.get("UE5_MCP_PORT", "65432"))
except (TypeError, ValueError):
    DEFAULT_PORT = 65432
DEFAULT_HOST = "127.0.0.1"

# Request queue: (request_dict, result_holder, done_event)
_pending_requests: "Queue[tuple]" = Queue()
_tick_handle = None
_cache_freshness_tick_handle = None

_last_cache_check = [0.0]  # mutable container for closure
CACHE_CHECK_INTERVAL = 15.0  # seconds


def _run_handler(tool: str, params: dict) -> dict:
    """Dispatch to the appropriate handler. Runs on game thread."""
    if tool == "ping":
        from ue5_host.handlers import handle_ping

        return handle_ping()
    if tool == "list_blueprints":
        from ue5_host.handlers import handle_list_blueprints

        return handle_list_blueprints(
            path_prefix=params.get("path_prefix"),
            name_substring=params.get("name_substring"),
        )
    if tool == "get_blueprint":
        from ue5_host.handlers import handle_get_blueprint

        return handle_get_blueprint(blueprint_name=params.get("blueprint_name", ""))
    if tool == "list_components":
        from ue5_host.handlers import handle_list_components

        return handle_list_components(blueprint_name=params.get("blueprint_name", ""))
    if tool == "get_variables":
        from ue5_host.handlers import handle_get_variables

        return handle_get_variables(
            blueprint_name=params.get("blueprint_name", ""),
            include_locals=params.get("include_locals", False),
        )
    if tool == "list_interfaces":
        from ue5_host.handlers import handle_list_interfaces

        return handle_list_interfaces()
    if tool == "get_interface":
        from ue5_host.handlers import handle_get_interface

        return handle_get_interface(interface_name=params.get("interface_name", ""))
    if tool == "find_event_bindings":
        from ue5_host.handlers import handle_find_event_bindings

        return handle_find_event_bindings(
            event_name=params.get("event_name"),
            interface_name=params.get("interface_name"),
            use_live_scan=params.get("use_live_scan", True),
            refresh_index=params.get("refresh_index", False),
        )
    if tool == "asset_search":
        from ue5_host.handlers import handle_asset_search

        return handle_asset_search(
            asset_class=params.get("asset_class"),
            name_filter=params.get("name_filter"),
        )
    if tool == "get_data_asset":
        from ue5_host.handlers import handle_get_data_asset

        return handle_get_data_asset(asset_name=params.get("asset_name", ""))
    if tool == "get_struct":
        from ue5_host.handlers import handle_get_struct

        return handle_get_struct(struct_name=params.get("struct_name", ""))
    if tool == "query_blueprints":
        from ue5_host.handlers import handle_query_blueprints

        return handle_query_blueprints(
            parent_class=params.get("parent_class"),
            has_function=params.get("has_function"),
            has_variable=params.get("has_variable"),
            references_type=params.get("references_type"),
        )
    if tool == "query_functions":
        from ue5_host.handlers import handle_query_functions

        return handle_query_functions(
            name_filter=params.get("name_filter"),
            input_type=params.get("input_type"),
            output_type=params.get("output_type"),
        )
    if tool == "query_references":
        from ue5_host.handlers import handle_query_references

        return handle_query_references(type_name=params.get("type_name", ""))

    return {
        "error": True,
        "type": "RUNTIME",
        "code": "UNKNOWN_TOOL",
        "message": f"Unknown tool: {tool}",
        "tool": tool,
    }


def _process_request_queue():
    """Drain one request from the queue. Runs on game thread."""
    try:
        request_dict, result_holder, done_event = _pending_requests.get_nowait()
        tool = request_dict.get("tool", "")
        params = request_dict.get("params", {})
        try:
            result = _run_handler(tool, params)
            result_holder.append(result)
        except Exception as e:
            result_holder.append({
                "error": True,
                "type": "RUNTIME",
                "code": "HANDLER_ERROR",
                "message": str(e),
                "tool": tool,
            })
        finally:
            done_event.set()
    except Empty:
        pass


def _tick_callback(delta_time: float) -> bool:
    """Slate tick callback — drain pending requests. Returns True to keep receiving ticks."""
    _process_request_queue()
    return True


def _check_cache_freshness(delta_time: float) -> bool:
    """Periodic tick: scan for modified .uasset files and mark dirty cache entries."""
    now = time.time()
    if now - _last_cache_check[0] < CACHE_CHECK_INTERVAL:
        return True
    _last_cache_check[0] = now
    if unreal is None:
        return True
    try:
        from ue5_host.handlers import _get_asset_modified_time, _get_cache_db

        conn = _get_cache_db()
        rows = conn.execute(
            "SELECT asset_path, modified_time FROM blueprint_cache WHERE dirty=0"
        ).fetchall()
        dirty_paths = []
        for asset_path, cached_modified in rows:
            current = _get_asset_modified_time(asset_path)
            if current > cached_modified:
                dirty_paths.append(asset_path)
        if dirty_paths:
            for path in dirty_paths:
                conn.execute(
                    "UPDATE blueprint_cache SET dirty=1 WHERE asset_path=?",
                    (path,),
                )
            conn.commit()
            if hasattr(unreal, "log"):
                unreal.log(f"[Grimoire] Marked {len(dirty_paths)} cache entries dirty")
        conn.close()
    except Exception as e:
        if hasattr(unreal, "log_warning"):
            unreal.log_warning(f"[Grimoire] Cache check error: {e}")
    return True


def _register_cache_freshness_watcher():
    """After IPC bridge is up: poll disk mtimes vs SQLite and mark stale rows dirty."""
    global _cache_freshness_tick_handle
    if unreal is None or not hasattr(unreal, "register_slate_post_tick_callback"):
        return
    if _cache_freshness_tick_handle is not None:
        return
    try:
        _cache_freshness_tick_handle = unreal.register_slate_post_tick_callback(_check_cache_freshness)
        if hasattr(unreal, "log"):
            unreal.log(f"[Grimoire] Cache freshness watcher registered ({CACHE_CHECK_INTERVAL:g}s interval)")
    except Exception as e:
        if hasattr(unreal, "log_warning"):
            unreal.log_warning(f"[Grimoire] Cache freshness watcher registration failed: {e}")


def _schedule_on_game_thread(request_dict: dict, result_holder: list, done_event: threading.Event):
    """Schedule handler execution on the game thread."""
    _pending_requests.put((request_dict, result_holder, done_event))

    # Try call_on_game_thread for immediate dispatch (if available)
    if unreal is not None:
        if hasattr(unreal, "call_on_game_thread"):
            unreal.call_on_game_thread(lambda: _process_request_queue())
            return
        if hasattr(unreal, "execute_on_game_thread"):
            unreal.execute_on_game_thread(lambda: _process_request_queue())
            return

    # Fallback: tick callback (registered at startup) will drain the queue next frame


def _handle_client(conn: socket.socket):
    """Handle a single client connection. Runs in background thread."""
    try:
        # Read until newline (simple protocol)
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break

        line = data.decode("utf-8", errors="replace").strip()
        if not line:
            conn.sendall(b'{"error":true,"type":"TRANSPORT","code":"EMPTY_REQUEST","message":"Empty request"}\n')
            return

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            conn.sendall(
                json.dumps({
                    "error": True,
                    "type": "VALIDATION",
                    "code": "INVALID_JSON",
                    "message": str(e),
                }).encode() + b"\n"
            )
            return

        tool = request.get("tool", "ping")
        params = request.get("params", {})

        result_holder: list = []
        done_event = threading.Event()

        _schedule_on_game_thread(request, result_holder, done_event)

        # Wait up to 10 seconds for game thread to process
        done_event.wait(timeout=10.0)

        if result_holder:
            response = result_holder[0]
        else:
            response = {
                "error": True,
                "type": "RUNTIME",
                "code": "TIMEOUT",
                "message": "Game thread did not process request in time",
                "tool": tool,
            }

        conn.sendall(json.dumps(response).encode() + b"\n")

    except Exception as e:
        try:
            conn.sendall(
                json.dumps({
                    "error": True,
                    "type": "RUNTIME",
                    "code": "CONNECTION_ERROR",
                    "message": str(e),
                }).encode() + b"\n"
            )
        except Exception:
            pass
    finally:
        conn.close()


def _run_server(host: str, port: int):
    """Run the TCP server loop. Runs in background thread."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((host, port))
        server.listen(5)
        # Log to Output Log if unreal is available
        if unreal is not None and hasattr(unreal, "log"):
            unreal.log(f"UE5 Context Bridge: listening on {host}:{port}")

        while True:
            conn, addr = server.accept()
            client_thread = threading.Thread(target=_handle_client, args=(conn,), daemon=True)
            client_thread.start()
    except OSError as e:
        if unreal is not None and hasattr(unreal, "log_error"):
            unreal.log_error(f"UE5 Context Bridge: failed to bind {host}:{port} — {e}")
    finally:
        server.close()


def _register_grimoire_cache_dirty_hook():
    """On asset save, mark SQLite cache row dirty so next get_blueprint re-parses."""
    if unreal is None:
        return
    try:
        from ue5_host.handlers import handle_mark_dirty

        def _on_asset_post_save(asset, *args, **kwargs):
            if asset is None:
                return
            try:
                path = str(asset.get_path_name())
                if "." in path:
                    path = path.split(".")[0]
                handle_mark_dirty(path)
                if hasattr(unreal, "log"):
                    unreal.log(f"[Grimoire] Marked dirty: {path}")
            except Exception as e:
                if hasattr(unreal, "log_warning"):
                    unreal.log_warning(f"[Grimoire] post-save hook error: {e}")

        bound = False
        if hasattr(unreal, "EditorDelegates"):
            ed = unreal.EditorDelegates
            for attr in (
                "on_asset_post_save",
                "on_asset_saved",
                "on_asset_post_save_with_context",
            ):
                if not hasattr(ed, attr):
                    continue
                d = getattr(ed, attr)
                for meth in ("add_callable", "add", "bind_callable", "add_dynamic"):
                    if hasattr(d, meth):
                        try:
                            getattr(d, meth)(_on_asset_post_save)
                            bound = True
                            break
                        except Exception:
                            continue
                if bound:
                    break
        if not bound and hasattr(unreal, "log_warning"):
            unreal.log_warning(
                "[Grimoire] Asset post-save delegate not available; "
                "stale cache rows are still marked dirty by the periodic freshness watcher.",
            )
    except Exception as e:
        if hasattr(unreal, "log_warning"):
            unreal.log_warning(f"[Grimoire] Cache dirty hook registration failed: {e}")


def start_host(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """Start the UE5 host. Call from game thread (e.g. at editor startup)."""
    global _tick_handle

    # Register tick callback to drain request queue (fallback when call_on_game_thread unavailable)
    if unreal is not None and _tick_handle is None and hasattr(unreal, "register_slate_post_tick_callback"):
        _tick_handle = unreal.register_slate_post_tick_callback(_tick_callback)

    _register_grimoire_cache_dirty_hook()

    server_thread = threading.Thread(
        target=_run_server,
        args=(host, port),
        daemon=True,
        name="UE5ContextBridge",
    )
    server_thread.start()

    _register_cache_freshness_watcher()


# Auto-start when loaded in UE5 editor (e.g. via Python Startup Scripts)
if unreal is not None:
    start_host()
