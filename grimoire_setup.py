"""
Grimoire Setup — auto-generates claude_desktop_config.json entries
for all Grimoire MCP servers.

Run: python grimoire_setup.py
"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _find_claude_config_path() -> str:
    """Detect Claude config path. Microsoft Store (UWP) and traditional installs use different locations."""
    # Microsoft Store / UWP: %LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        packages_dir = os.path.join(local_appdata, "Packages")
        if os.path.isdir(packages_dir):
            for name in os.listdir(packages_dir):
                if name.startswith("Claude_"):
                    uwp_path = os.path.join(
                        packages_dir, name, "LocalCache", "Roaming", "Claude", "claude_desktop_config.json"
                    )
                    if os.path.exists(os.path.dirname(uwp_path)):
                        return uwp_path
    # Traditional desktop: %APPDATA%\Claude\claude_desktop_config.json
    return os.path.expandvars(os.path.join("%APPDATA%", "Claude", "claude_desktop_config.json"))


CLAUDE_CONFIG_PATH = _find_claude_config_path()
CONFIG_TOML = os.path.join(PROJECT_ROOT, "config.toml")

SERVERS = {
    "grimoire-blueprint": {
        "command": "python",
        "args": ["-m", "ue5_mcp.mcp_server"],
        "cwd": PROJECT_ROOT,
        "env": {
            "UE5_MCP_CONFIG": CONFIG_TOML,
            "PYTHONPATH": PROJECT_ROOT,
        },
    },
    "grimoire-query": {
        "command": "python",
        "args": ["-m", "grimoire_query.mcp_server"],
        "cwd": PROJECT_ROOT,
        "env": {
            "GRIMOIRE_CONFIG": CONFIG_TOML,
            "PYTHONPATH": PROJECT_ROOT,
        },
    },
}


def run():
    # Load existing config or create new
    if os.path.exists(CLAUDE_CONFIG_PATH):
        with open(CLAUDE_CONFIG_PATH, "r") as f:
            config = json.load(f)
    else:
        config = {}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # Remove old single grimoire entry if present
    config["mcpServers"].pop("grimoire", None)
    config["mcpServers"].pop("ue5-context", None)

    # Add all Grimoire servers
    for name, server_config in SERVERS.items():
        config["mcpServers"][name] = server_config
        print(f"  registered: {name}")

    config_dir = os.path.dirname(CLAUDE_CONFIG_PATH)
    if config_dir:
        os.makedirs(config_dir, exist_ok=True)
    with open(CLAUDE_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\nConfig written to: {CLAUDE_CONFIG_PATH}")
    print("Restart Claude Desktop to apply changes.")


if __name__ == "__main__":
    print("Grimoire Setup")
    print("==============")
    print(f"Using config: {CLAUDE_CONFIG_PATH}")
    run()
