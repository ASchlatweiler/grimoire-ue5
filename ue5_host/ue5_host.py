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

        return handle_get_variables(blueprint_name=params.get("blueprint_name", ""))
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


def start_host(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """Start the UE5 host. Call from game thread (e.g. at editor startup)."""
    global _tick_handle

    # Register tick callback to drain request queue (fallback when call_on_game_thread unavailable)
    if unreal is not None and _tick_handle is None and hasattr(unreal, "register_slate_post_tick_callback"):
        _tick_handle = unreal.register_slate_post_tick_callback(_tick_callback)

    server_thread = threading.Thread(
        target=_run_server,
        args=(host, port),
        daemon=True,
        name="UE5ContextBridge",
    )
    server_thread.start()


# Auto-start when loaded in UE5 editor (e.g. via Python Startup Scripts)
if unreal is not None:
    start_host()
