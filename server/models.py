"""Pydantic request/response schemas."""
from pydantic import BaseModel


class UserCreate(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    token: str
    user: dict


class ProjectCreate(BaseModel):
    title: str | None = None
    idea: str


class GenerateReq(BaseModel):
    project_id: int


class RefineReq(BaseModel):
    project_id: int
    message: str


class RaceReq(BaseModel):
    project_id: int
    models: list[str]


class SelectVersionReq(BaseModel):
    version_id: int
