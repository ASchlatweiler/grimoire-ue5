"""
Load configuration from config.toml or UE5_MCP_CONFIG env var.
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProjectConfig:
    name: str
    root: str


@dataclass
class IPCConfig:
    host: str
    port: int
    timeout_sec: float


@dataclass
class ServerConfig:
    log_level: str


@dataclass
class Config:
    project: ProjectConfig
    ipc: IPCConfig
    server: ServerConfig

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        if path is None:
            path = os.environ.get("UE5_MCP_CONFIG")
        if path is None:
            # Default: config.toml next to this package
            path = str(Path(__file__).parent.parent / "config.toml")

        try:
            import tomli

            with open(path, "rb") as f:
                data = tomli.load(f)
        except FileNotFoundError:
            return cls._default()
        except Exception:
            return cls._default()

        project = data.get("project", {})
        ipc = data.get("ipc", {})
        server = data.get("server", {})

        return cls(
            project=ProjectConfig(
                name=project.get("name", "Unnamed"),
                root=project.get("root", ""),
            ),
            ipc=IPCConfig(
                host=ipc.get("host", "127.0.0.1"),
                port=int(ipc.get("port", 65432)),
                timeout_sec=float(ipc.get("timeout_sec", 5)),
            ),
            server=ServerConfig(
                log_level=server.get("log_level", "info"),
            ),
        )

    @classmethod
    def _default(cls) -> "Config":
        return cls(
            project=ProjectConfig(name="Unnamed", root=""),
            ipc=IPCConfig(host="127.0.0.1", port=65432, timeout_sec=5),
            server=ServerConfig(log_level="info"),
        )
