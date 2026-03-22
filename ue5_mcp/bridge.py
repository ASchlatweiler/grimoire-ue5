"""
IPC bridge: connects to Unreal host over TCP, sends JSON requests, returns responses.
"""

import json
import socket
from typing import Any


def send_request(
    tool: str,
    params: dict[str, Any],
    host: str,
    port: int,
    timeout_sec: float,
) -> dict[str, Any]:
    """
    Send a tool request to the Unreal host and return the JSON response.
    Returns error shape on connection failure or timeout.
    """
    payload = json.dumps({"tool": tool, "params": params}) + "\n"

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_sec)
        sock.connect((host, port))
        sock.sendall(payload.encode("utf-8"))

        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break

        sock.close()

        line = data.decode("utf-8", errors="replace").strip()
        if not line:
            return {
                "error": True,
                "type": "TRANSPORT",
                "code": "EMPTY_RESPONSE",
                "message": "No response from UE5 host",
                "tool": tool,
            }

        return json.loads(line)

    except socket.timeout:
        return {
            "error": True,
            "type": "TRANSPORT",
            "code": "TIMEOUT",
            "message": f"Request timed out after {timeout_sec}s",
            "tool": tool,
        }
    except ConnectionRefusedError:
        return {
            "error": True,
            "type": "TRANSPORT",
            "code": "EDITOR_OFFLINE",
            "message": f"Cannot reach UE5 host on {host}:{port}. Is the editor open?",
            "tool": tool,
        }
    except OSError as e:
        return {
            "error": True,
            "type": "TRANSPORT",
            "code": "CONNECTION_REFUSED",
            "message": str(e),
            "tool": tool,
        }
    except json.JSONDecodeError as e:
        return {
            "error": True,
            "type": "TRANSPORT",
            "code": "INVALID_RESPONSE",
            "message": f"Invalid JSON from host: {e}",
            "tool": tool,
        }
