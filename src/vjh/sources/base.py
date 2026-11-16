"""Collector plugin interface."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..models.job import Job
from ..models.settings import CollectionSettings
from ..models.source import SourceConfig
from .http import PoliteClient


@dataclass
class CollectContext:
    client: PoliteClient
    settings: CollectionSettings
    now: datetime
    data_dir: Path
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("vjh.collect"))
    known_urls: set[str] = field(default_factory=set)   # already stored: don't fetch again
    skipped_known: int = 0                               # links skipped because already stored
    search_terms: dict = field(default_factory=dict)     # config/search_terms.yaml


@dataclass
class CollectedJob:
    """A job plus the identifier it has at its source (for 'seen before' checks)."""

    job: Job
    external_id: str | None = None


Collector = Callable[[SourceConfig, CollectContext], list[CollectedJob]]

_REGISTRY: dict[str, Collector] = {}


def register(name: str) -> Callable[[Collector], Collector]:
    def deco(fn: Collector) -> Collector:
        _REGISTRY[name] = fn
        return fn
    return deco


def get_collector(name: str) -> Collector | None:
    from . import collectors  # noqa: F401  (imports register every collector)
    return _REGISTRY.get(name)
