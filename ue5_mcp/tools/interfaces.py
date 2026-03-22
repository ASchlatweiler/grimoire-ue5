"""Interface and event-binding tool schemas."""

from typing import Optional

from pydantic import BaseModel, Field


class GetInterfaceParams(BaseModel):
    interface_name: str = Field(..., description="Blueprint Interface name or path")


class FindEventBindingsParams(BaseModel):
    event_name: Optional[str] = Field(None, description="Filter by event name")
    interface_name: Optional[str] = Field(None, description="Filter by interface name")
    use_live_scan: bool = Field(True, description="Bypass index, query live project state")
    refresh_index: bool = Field(False, description="Force full index rebuild")


class AssetSearchParams(BaseModel):
    asset_class: Optional[str] = Field(None, description="Filter by asset class (e.g. Blueprint, StaticMesh)")
    name_filter: Optional[str] = Field(None, description="Filter by name substring")
