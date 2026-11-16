"""Generated CVs and cover letters."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .common import ApprovalStatus, DocumentType, Language


class GeneratedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    job_id: int
    doc_type: DocumentType
    language: Language
    file_path: str
    created_at: datetime
    version: int = Field(default=1, ge=1)
    approval_status: ApprovalStatus = ApprovalStatus.DRAFT
    evidence_ids: list[str] = []          # every claim must trace to evidence
