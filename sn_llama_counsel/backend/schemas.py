"""Pydantic schemas for sn_llama_counsel."""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional


class CounselMember(BaseModel):
    model: str
    role: str
    system: str
    ctx_tokens: int = 8192  # model context window; used to size file chunks
    # Optional per-member sampling — when None, runner uses defaults.
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # When True, this member runs ONCE in a retrieval pre-phase and its
    # output is prepended as retrieved context to every other member and
    # to the chairperson. Used for RAG-style lookups that should happen
    # once per run rather than being duplicated per member.
    retrieval: bool = False


class CounselChairperson(BaseModel):
    model: str
    system: str
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


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


class MemberOverride(BaseModel):
    """Per-run overrides applied on top of a counsel's member definitions.

    Keyed by role in RunRequest.member_overrides. Any field left None
    means "use the counsel's original value for this member".
    """
    model: Optional[str] = None
    system: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class RunRequest(BaseModel):
    task: str = Field(..., min_length=1)
    counsel: CounselConfig
    files: Optional[list[ContentPart]] = None
    # New persistence + follow-up fields (all optional for backward compat).
    session_id: Optional[str] = None
    parent_run_id: Optional[str] = None
    member_overrides: Optional[dict[str, MemberOverride]] = None


class CreateCounselRequest(BaseModel):
    description: str = Field(..., min_length=10)


class AutoSelectRequest(BaseModel):
    task: str = Field(..., min_length=1)


class AutoSelectResponse(BaseModel):
    counsel: CounselConfig


# ── Session/run history payloads ──────────────────────────────────────────

class SessionCreate(BaseModel):
    title: str = ""


class SessionInfo(BaseModel):
    id: str
    title: str = ""
    created_at: int
    updated_at: int
    run_count: int = 0


class MemberResponseRecord(BaseModel):
    role: str
    model: str
    content: str = ""
    error: Optional[str] = None


class UsageRecord(BaseModel):
    role: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class RunRecord(BaseModel):
    id: str
    session_id: str
    parent_run_id: Optional[str] = None
    task: str
    counsel_snapshot: dict
    synthesis: str = ""
    status: str = "running"
    created_at: int
    finished_at: Optional[int] = None
    members: list[MemberResponseRecord] = Field(default_factory=list)
    usage: list[UsageRecord] = Field(default_factory=list)
