from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal[
    "draft", "validating", "queued", "running", "pausing", "paused",
    "completed", "failed", "cancelled",
]


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    config: dict[str, Any]
    requested_cores: int = Field(default=1, ge=1)
    queue: bool = True


class ResourceUpdate(BaseModel):
    requested_cores: int = Field(ge=1)
    apply_now: bool = False


class GlobalResourceUpdate(BaseModel):
    global_core_limit: int = Field(ge=1)


class FileRequest(BaseModel):
    path: str


class FileWriteRequest(BaseModel):
    path: str
    content: str


class OptionDocument(BaseModel):
    path: str | None = None
    mission: dict[str, Any]
    journeys: list[dict[str, Any]] = Field(default_factory=list)


class OptionField(BaseModel):
    scope: Literal["mission", "journey"]
    group: str
    name: str
    data_type: str
    default: Any = None
    lower: Any = None
    upper: Any = None
    units: str | None = None
    description: str = ""
    choices: list[dict[str, Any]] = Field(default_factory=list)


class TrajectorySeries(BaseModel):
    solution_id: str
    detail: Literal["events", "dense"]
    frame: str = "J2000"
    source_frame: str | None = None
    central_body: str | None = None
    time_system: str = "MJD"
    source_time_system: str | None = None
    transformation_applied: str | None = None
    units: dict[str, str] = Field(default_factory=lambda: {"position": "km", "velocity": "km/s"})
    samples: list[dict[str, Any]]
    original_count: int
    returned_count: int
    materialization_status: str = "available"
