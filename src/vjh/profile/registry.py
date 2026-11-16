"""Evidence registry: mirror the profile's evidence items into the database."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.tables import EvidenceRow
from ..models.profile import MasterProfile


def sync_evidence(session: Session, profile: MasterProfile) -> int:
    """Insert or update every evidence item. Rows are never deleted, so a claim
    referenced by an old document stays traceable. Returns the number synced."""
    for item in profile.evidence:
        row = session.get(EvidenceRow, item.id) or EvidenceRow(id=item.id)
        row.claim = item.claim
        row.supporting_experience = item.supporting_experience
        row.source = item.source.value
        row.source_detail = item.source_detail
        row.confidence = item.confidence.value
        row.verification_status = item.verification_status.value
        row.profile_version = profile.profile_version
        row.notes = item.notes
        session.add(row)
    return len(profile.evidence)
