"""Safe schema upgrades: back up an existing database, then run Alembic to head."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def make_engine(db_path: Path) -> Engine:
    # timeout: wait up to 30 s for a lock (e.g. dashboard open during the daily run)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # enforce foreign keys in SQLite
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def _alembic_config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(_alembic_config(Path("x.db"))).get_current_head()


def current_revision(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    engine = make_engine(db_path)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def upgrade(db_path: Path, target: str = "head") -> Path | None:
    """Bring the database to `target` (default: latest). Returns the backup path if one was made."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    current = current_revision(db_path)
    wanted = head_revision() if target == "head" else target
    if db_path.exists() and db_path.stat().st_size > 0 and current != wanted:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
        shutil.copy2(db_path, backup)
    # Plain engine (foreign-key enforcement off) so schema steps can never cascade.
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.begin() as conn:
            cfg = _alembic_config(db_path)
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, target)
    finally:
        engine.dispose()
    return backup
