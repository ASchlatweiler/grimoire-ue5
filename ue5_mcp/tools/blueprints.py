"""Blueprint-related tool schemas (Pydantic models for validation)."""

from typing import Optional

from pydantic import BaseModel, Field


class ListBlueprintsParams(BaseModel):
    path_prefix: Optional[str] = Field(None, description="Filter by path prefix (e.g. /Game/MyFolder)")
    name_substring: Optional[str] = Field(None, description="Filter by name containing this string")


class GetBlueprintParams(BaseModel):
    blueprint_name: str = Field(..., description="Blueprint name or full path (e.g. BPC_InteractableBase or /Game/Path/BP_My)")


class ListComponentsParams(BaseModel):
    blueprint_name: str = Field(..., description="Blueprint name or full path")


class GetVariablesParams(BaseModel):
    blueprint_name: str = Field(..., description="Blueprint name or full path")
