"""Pydantic schemas for sn_llama_counsel."""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional


class CounselMember(BaseModel):
    model: str
    role: str
    system: str


class CounselChairperson(BaseModel):
    model: str
    system: str


class CounselConfig(BaseModel):
    name: str
    description: str = ""
    chairperson: CounselChairperson
    members: list[CounselMember]


class ContentPart(BaseModel):
    """OpenAI-compatible content part (text or image_url)."""
    type: str
    text: Optional[str] = None
    image_url: Optional[dict] = None


class RunRequest(BaseModel):
    task: str = Field(..., min_length=1)
    counsel: CounselConfig
    files: Optional[list[ContentPart]] = None


class CreateCounselRequest(BaseModel):
    description: str = Field(..., min_length=10)


class AutoSelectRequest(BaseModel):
    task: str = Field(..., min_length=1)


class AutoSelectResponse(BaseModel):
    counsel: CounselConfig
