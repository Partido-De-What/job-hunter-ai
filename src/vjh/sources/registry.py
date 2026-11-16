"""Load config/sources.yaml and mirror it into the `sources` table."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import load_yaml
from ..db.tables import SourceRow
from ..models.source import SourceRegistry


def load_registry(root: Path) -> SourceRegistry:
    return SourceRegistry.model_validate(load_yaml(root / "config" / "sources.yaml"))


def sync_sources(session: Session, registry: SourceRegistry) -> int:
    """Insert/update configuration fields. Monitoring fields (last_checked, last_success,
    error_status) are never touched here, and rows are never deleted: a source removed
    from the YAML is marked inactive so its history stays visible."""
    existing = {r.name: r for r in session.scalars(select(SourceRow))}
    for src in registry.sources:
        row = existing.pop(src.name, None) or SourceRow(name=src.name)
        row.url = src.url
        row.kind = src.kind.value
        row.collector = src.collector.value
        row.collection_method = src.collection_method.value
        row.access_restrictions = src.access_restrictions
        row.update_frequency = src.update_frequency
        row.automatic_supported = src.automatic_supported
        row.active = src.active
        session.add(row)
    for orphan in existing.values():
        orphan.active = False
    return len(registry.sources)
