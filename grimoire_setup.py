"""
Grimoire Setup — auto-generates claude_desktop_config.json entries
for all Grimoire MCP servers.

Run: python grimoire_setup.py
"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _find_all_claude_config_paths() -> list[str]:
    """Find all Claude config files that exist on this system."""
    paths = []
    # Traditional Roaming path
    roaming = os.path.expandvars(r"%APPDATA%\Claude\claude_desktop_config.json")
    if os.path.exists(os.path.dirname(roaming)):
        paths.append(roaming)
    # UWP / Microsoft Store path
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        packages_dir = os.path.join(local_appdata, "Packages")
        if os.path.isdir(packages_dir):
            for name in os.listdir(packages_dir):
                if name.startswith("Claude_"):
                    uwp_path = os.path.join(
                        packages_dir, name, "LocalCache", "Roaming", "Claude",
                        "claude_desktop_config.json"
                    )
                    if os.path.exists(os.path.dirname(uwp_path)):
                        paths.append(uwp_path)
    return paths

CONFIG_TOML = os.path.join(PROJECT_ROOT, "config.toml")
# Normalize paths for config compatibility (forward slashes work everywhere)
PROJECT_ROOT_NORM = PROJECT_ROOT.replace("\\", "/")
CONFIG_TOML_NORM = CONFIG_TOML.replace("\\", "/")

SERVERS = {
    "ue5-context": {
        "command": "python",
        "args": ["-m", "ue5_mcp.mcp_server"],
        "cwd": PROJECT_ROOT_NORM,
        "env": {
            "UE5_MCP_CONFIG": CONFIG_TOML_NORM,
            "PYTHONPATH": PROJECT_ROOT_NORM,
        },
    },
    "grimoire-query": {
        "command": "python",
        "args": ["-m", "grimoire_query.mcp_server"],
        "cwd": PROJECT_ROOT_NORM,
        "env": {
            "GRIMOIRE_CONFIG": CONFIG_TOML_NORM,
            "PYTHONPATH": PROJECT_ROOT_NORM,
        },
    },
}


def run():
    config_paths = _find_all_claude_config_paths()
    if not config_paths:
        print("ERROR: No Claude config paths found.")
        return

    for config_path in config_paths:
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
        else:
            config = {}

        if "mcpServers" not in config:
            config["mcpServers"] = {}

        config["mcpServers"].pop("grimoire", None)
        config["mcpServers"].pop("grimoire-blueprint", None)
        config["mcpServers"].pop("grimoire-blueprint-b", None)
        config["mcpServers"].pop("grimoire-blueprint-c", None)

        for name, server_config in SERVERS.items():
            config["mcpServers"][name] = server_config

        config_dir = os.path.dirname(config_path)
        os.makedirs(config_dir, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        print(f"  Written: {config_path}")

    print("\nAll configs updated. Restart Claude Desktop to apply.")


if __name__ == "__main__":
    print("Grimoire Setup")
    print("==============")
    run()
