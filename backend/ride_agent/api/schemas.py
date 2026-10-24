"""Validated API input contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NewCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trip_id: str = Field(pattern="^trip-[a-z0-9-]+$", max_length=100)
    description: str = Field(min_length=5, max_length=4000)
    topic: Literal["cancellation", "fare", "payment", "other"] = "other"
    agent_mode: Literal["single", "multi"] | None = None


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["message", "handoff", "confirm", "close", "evidence"]
    text: str = Field(default="", max_length=4000)
    agent_mode: Literal["single", "multi"] | None = None


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=1, max_length=100)
    document_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=12000)


class MCPProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trip_id: str = Field(pattern="^trip-[a-z0-9-]+$", max_length=100)


class SkillDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=20, max_length=12000)


class StaffAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "reply", "resolve", "propose_adjustment"]
    text: str = Field(default="", max_length=4000)
