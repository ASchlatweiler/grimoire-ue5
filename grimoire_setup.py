"""
Grimoire Setup — configure Grimoire for Claude Desktop.

Usage:
    python grimoire_setup.py
        Update Claude Desktop config only (fast, idempotent).

    python grimoire_setup.py --full
        Full setup: create venv, install dependencies, write config.toml,
        update Claude Desktop config. Safe to re-run.

    python grimoire_setup.py --full --project "C:/path/to/MyGame"
        Full setup with project path provided (no prompt).
"""

import json
import os
import subprocess
import sys
import shutil

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_NORM = PROJECT_ROOT.replace("\\", "/")
CONFIG_TOML = os.path.join(PROJECT_ROOT, "config.toml")
CONFIG_TOML_EXAMPLE = os.path.join(PROJECT_ROOT, "config.toml.example")
CONFIG_TOML_NORM = CONFIG_TOML.replace("\\", "/")
VENV_DIR = os.path.join(PROJECT_ROOT, ".venv")
REQUIREMENTS = os.path.join(PROJECT_ROOT, "requirements.txt")


# ---------------------------------------------------------------------------
# Claude Desktop config paths
# ---------------------------------------------------------------------------

def _find_all_claude_config_paths() -> list[str]:
    """Find all Claude config files that exist on this system."""
    paths = []
    roaming = os.path.expandvars(r"%APPDATA%\Claude\claude_desktop_config.json")
    if os.path.exists(os.path.dirname(roaming)):
        paths.append(roaming)
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        packages_dir = os.path.join(local_appdata, "Packages")
        if os.path.isdir(packages_dir):
            for name in os.listdir(packages_dir):
                if name.startswith("Claude_"):
                    uwp_path = os.path.join(
                        packages_dir, name, "LocalCache", "Roaming", "Claude",
                        "claude_desktop_config.json",
                    )
                    if os.path.exists(os.path.dirname(uwp_path)):
                        paths.append(uwp_path)
    return paths


# ---------------------------------------------------------------------------
# Venv helpers
# ---------------------------------------------------------------------------

def _venv_python() -> str:
    """Return path to the venv Python executable."""
    if sys.platform == "win32":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def _ensure_venv():
    """Create .venv if it doesn't exist."""
    if os.path.exists(_venv_python()):
        print("  [venv] Already exists — skipping creation.")
        return
    print("  [venv] Creating virtual environment...")
    subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])
    print(f"  [venv] Created at {VENV_DIR}")


def _install_requirements():
    """Install requirements.txt into the venv."""
    if not os.path.exists(REQUIREMENTS):
        print("  [pip] No requirements.txt found — skipping.")
        return
    print("  [pip] Installing dependencies...")
    subprocess.check_call([_venv_python(), "-m", "pip", "install", "-r", REQUIREMENTS, "--quiet"])
    print("  [pip] Done.")


# ---------------------------------------------------------------------------
# config.toml helpers
# ---------------------------------------------------------------------------

def _ensure_config_toml(project_path: str):
    """Write config.toml from example if it doesn't exist, or update project root."""
    if os.path.exists(CONFIG_TOML):
        # Update project root in existing config
        with open(CONFIG_TOML, "r") as f:
            content = f.read()
        # Replace root line
        import re
        norm = project_path.replace("\\", "/")
        new_content = re.sub(
            r'^root\s*=\s*"[^"]*"',
            f'root = "{norm}"',
            content,
            flags=re.MULTILINE,
        )
        if new_content != content:
            with open(CONFIG_TOML, "w") as f:
                f.write(new_content)
            print(f"  [config] Updated project root: {norm}")
        else:
            print(f"  [config] config.toml already set — no changes needed.")
        return

    # Create from example
    if os.path.exists(CONFIG_TOML_EXAMPLE):
        shutil.copy(CONFIG_TOML_EXAMPLE, CONFIG_TOML)
        print(f"  [config] Created config.toml from example.")
    else:
        # Write a minimal default
        with open(CONFIG_TOML, "w") as f:
            f.write(_default_config_toml(project_path))
        print(f"  [config] Created default config.toml.")

    # Now set the project root
    _ensure_config_toml(project_path)


def _default_config_toml(project_path: str) -> str:
    norm = project_path.replace("\\", "/")
    return f"""[project]
name = "Grimoire"
root = "{norm}"

[ipc]
host = "127.0.0.1"
port = 65432
timeout_sec = 5

[server]
log_level = "info"
"""


def _prompt_project_path() -> str:
    """Prompt the user for their UE5 project root."""
    print()
    print("  Enter the path to your UE5 project root.")
    print("  This is the folder containing your .uproject file.")
    print("  Example: C:/GameDev/MyGame")
    print()
    while True:
        path = input("  Project path: ").strip().strip('"').strip("'")
        if os.path.isdir(path):
            return path
        print(f"  Directory not found: {path}")
        print("  Please enter a valid path.")


# ---------------------------------------------------------------------------
# Claude Desktop config update
# ---------------------------------------------------------------------------

def _build_server_entry(use_venv: bool) -> dict:
    """Build the MCP server config entry."""
    python_cmd = _venv_python().replace("\\", "/") if use_venv else "python"
    return {
        "command": python_cmd,
        "args": ["-m", "ue5_mcp.mcp_server"],
        "cwd": PROJECT_ROOT_NORM,
        "env": {
            "UE5_MCP_CONFIG": CONFIG_TOML_NORM,
            "PYTHONPATH": PROJECT_ROOT_NORM,
        },
    }


def _update_claude_config(use_venv: bool):
    """Merge Grimoire MCP entry into Claude Desktop config."""
    config_paths = _find_all_claude_config_paths()
    if not config_paths:
        print()
        print("  [claude] No Claude Desktop config found.")
        print("  Add this manually to claude_desktop_config.json:")
        print()
        snippet = {"mcpServers": {"ue5-context": _build_server_entry(use_venv)}}
        print(json.dumps(snippet, indent=2))
        return

    server_entry = _build_server_entry(use_venv)

    for config_path in config_paths:
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
        else:
            config = {}

        if "mcpServers" not in config:
            config["mcpServers"] = {}

        # Clean up old Grimoire entry names
        for old_key in ["grimoire", "grimoire-blueprint", "grimoire-blueprint-b",
                         "grimoire-blueprint-c", "grimoire-query"]:
            config["mcpServers"].pop(old_key, None)

        config["mcpServers"]["ue5-context"] = server_entry

        config_dir = os.path.dirname(config_path)
        os.makedirs(config_dir, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        print(f"  [claude] Written: {config_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_quick():
    """Quick mode — Claude Desktop config only."""
    print()
    print("Updating Claude Desktop config...")
    _update_claude_config(use_venv=os.path.exists(_venv_python()))
    print()
    print("Done. Restart Claude Desktop to apply.")


def run_full(project_path: str | None):
    """Full setup — venv, pip, config.toml, Claude Desktop config."""
    print()

    # 1. Project path
    if not project_path:
        project_path = _prompt_project_path()
    else:
        if not os.path.isdir(project_path):
            print(f"ERROR: Project path not found: {project_path}")
            sys.exit(1)
        print(f"  [project] Using: {project_path}")

    # 2. Venv
    print()
    print("Setting up virtual environment...")
    _ensure_venv()

    # 3. Dependencies
    print()
    print("Installing dependencies...")
    _install_requirements()

    # 4. config.toml
    print()
    print("Configuring config.toml...")
    _ensure_config_toml(project_path)

    # 5. Claude Desktop
    print()
    print("Updating Claude Desktop config...")
    _update_claude_config(use_venv=True)

    print()
    print("=" * 50)
    print("Grimoire setup complete.")
    print()
    print("Remaining manual steps (one-time per project):")
    print()
    print("  1. Enable the Python Script Plugin in UE5")
    print("     Edit > Plugins > search 'Python Script Plugin' > enable")
    print()
    print("  2. Enable Json Blueprint Utilities plugin")
    print("     Edit > Plugins > search 'Json Blueprint Utilities' > enable")
    print()
    print("  3. Add the Unreal Host startup script")
    print("     Edit > Project Settings > Plugins > Python > Startup Scripts")
    print("     Add: ue5_host.ue5_host")
    print("     (or full path to grimoire-ue5/ue5_host/ue5_host.py)")
    print()
    print("  4. Restart the UE5 editor")
    print()
    print("  5. Restart Claude Desktop")
    print()
    print("Then open a chat and try: 'ping the UE5 editor'")
    print("=" * 50)


if __name__ == "__main__":
    print("Grimoire Setup")
    print("==============")

    args = sys.argv[1:]
    full_mode = "--full" in args

    project_path = None
    if "--project" in args:
        idx = args.index("--project")
        if idx + 1 < len(args):
            project_path = args[idx + 1]

    if full_mode:
        run_full(project_path)
    else:
        run_quick()