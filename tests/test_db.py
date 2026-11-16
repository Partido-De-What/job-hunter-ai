"""Database round trip and migration safety."""

import re
from datetime import date, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vjh.db import migrate
from vjh.db.tables import (
    ApplicationEventRow,
    ApplicationRow,
    Base,
    JobEvaluationRow,
    JobRow,
)
from vjh.models import Salary, content_hash

NOW = datetime(2026, 11, 15, 9, 0)


def _seed(db_path: Path) -> None:
    engine = migrate.make_engine(db_path)
    with Session(engine) as s, s.begin():
        salary = Salary(amount_min=3000, period="monthly", source_text="ab EUR 3.000 brutto")
        job = JobRow(title="Pflanzenzüchter/in", employer="Example Saatgut GmbH", source="manual",
                     discovered_at=NOW, retrieved_at=NOW, published_at=date(2026, 11, 10),
                     description="Feldversuche, Bonituren.", status="new",
                     salary=salary.model_dump(mode="json"), salary_annual_min=salary.annual_gross_min(),
                     content_hash=content_hash("Pflanzenzüchter/in", "Example Saatgut GmbH", "x"))
        s.add(job)
        s.flush()
        s.add(JobEvaluationRow(job_id=job.id, overall_score=82, category="strong",
                               potentially_ineligible=False, eligibility_result="eligible",
                               cv_profile="A", recommendation="apply", ai_output={"k": "v"},
                               model="test-model", profile_version=1, content_hash=job.content_hash,
                               evaluated_at=NOW, strengths=["field trials"], missing_qualifications=[],
                               mandatory_requirements=[], potential_obstacles=[]))
        app = ApplicationRow(job_id=job.id, status="new", documents_submitted=[], notes="")
        s.add(app)
        s.flush()
        s.add(ApplicationEventRow(application_id=app.id, to_status="new", occurred_at=NOW, note="created"))
    engine.dispose()


def _counts(db_path: Path) -> dict[str, int]:
    engine = migrate.make_engine(db_path)
    with engine.connect() as c:
        out = {t.name: c.execute(text(f"SELECT COUNT(*) FROM {t.name}")).scalar_one()
               for t in Base.metadata.sorted_tables}
    engine.dispose()
    return out


def test_fresh_database_reaches_head(db_path):
    migrate.upgrade(db_path)
    assert migrate.current_revision(db_path) == migrate.head_revision()


def test_migration_matches_table_definitions(db_path):
    """Every table/column in tables.py exists in the migrated database, and vice versa."""
    migrate.upgrade(db_path)
    engine = migrate.make_engine(db_path)
    with engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diffs == []


def test_round_trip(db_path):
    migrate.upgrade(db_path)
    _seed(db_path)
    engine = migrate.make_engine(db_path)
    with Session(engine) as s:
        job = s.scalars(select(JobRow)).one()
        assert job.title == "Pflanzenzüchter/in"            # unicode survives
        assert job.salary["payments_per_year"] is None      # no 14-payment assumption
        assert job.salary_annual_min is None
        ev = s.scalars(select(JobEvaluationRow)).one()
        assert ev.strengths == ["field trials"] and ev.ai_output == {"k": "v"}
        assert s.scalars(select(ApplicationEventRow)).one().note == "created"
    engine.dispose()


def test_rerunning_migration_keeps_all_data(db_path):
    migrate.upgrade(db_path)
    _seed(db_path)
    before = _counts(db_path)
    assert migrate.upgrade(db_path) is None   # already at head: no change, no backup
    assert _counts(db_path) == before


def test_backup_made_when_existing_db_is_behind(db_path):
    """Simulate an older database: an existing file that is not at head gets a backup first."""
    import sqlite3
    sqlite3.connect(db_path).execute("CREATE TABLE legacy (x INTEGER)").connection.commit()
    backup = migrate.upgrade(db_path)
    assert backup is not None and backup.exists()
    assert migrate.current_revision(db_path) == migrate.head_revision()


def test_upgrade_0001_to_0002_keeps_every_row_and_makes_backup(db_path):
    migrate.upgrade(db_path, target="0001_initial")
    engine = migrate.make_engine(db_path)
    with engine.begin() as c:
        c.execute(text("INSERT INTO jobs (title, employer, source, discovered_at, retrieved_at, "
                       "description, status, content_hash) VALUES ('T', 'E', 's', '2026-11-15', "
                       "'2026-11-15', 'd', 'new', 'h')"))
        c.execute(text("INSERT INTO applications (job_id, status, documents_submitted, notes) "
                       "VALUES (1, 'applied', '[]', '')"))
        c.execute(text("INSERT INTO application_events (application_id, to_status, occurred_at, note) "
                       "VALUES (1, 'applied', '2026-11-15', 'sent')"))
    engine.dispose()
    before = {t: n for t, n in _counts_raw(db_path).items()}
    backup = migrate.upgrade(db_path)
    assert backup is not None and backup.exists()
    assert migrate.current_revision(db_path) == migrate.head_revision()
    after = _counts_raw(db_path)
    assert all(after[t] == n for t, n in before.items())


def _counts_raw(db_path: Path) -> dict[str, int]:
    import sqlite3
    con = sqlite3.connect(db_path)
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version'")]
    out = {n: con.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}
    con.close()
    return out


@pytest.mark.parametrize("sql", [
    "UPDATE application_events SET note = 'changed'",
    "DELETE FROM application_events",
])
def test_application_history_is_append_only(db_path, sql):
    migrate.upgrade(db_path)
    _seed(db_path)
    engine = migrate.make_engine(db_path)
    with pytest.raises(IntegrityError, match="append-only"), engine.begin() as c:
        c.execute(text(sql))
    engine.dispose()
    assert _counts(db_path)["application_events"] == 1


def test_evaluation_cache_key_is_unique(db_path):
    migrate.upgrade(db_path)
    _seed(db_path)
    engine = migrate.make_engine(db_path)
    with pytest.raises(IntegrityError), Session(engine) as s, s.begin():
        first = s.scalars(select(JobEvaluationRow)).one()
        s.add(JobEvaluationRow(**{c.name: getattr(first, c.name)
                                  for c in JobEvaluationRow.__table__.columns if c.name != "id"}))
    engine.dispose()


def test_no_migration_drops_or_deletes():
    """Static guard: upgrade() bodies must not drop tables/columns or delete rows."""
    versions = migrate.MIGRATIONS_DIR / "versions"
    files = list(versions.glob("*.py"))
    assert files
    forbidden = re.compile(r"drop_table|drop_column|DROP\s+TABLE|DELETE\s+FROM", re.IGNORECASE)
    for f in files:
        upgrade_body = f.read_text(encoding="utf-8").split("def upgrade", 1)[1].split("def downgrade", 1)[0]
        assert not forbidden.search(upgrade_body), f"{f.name} upgrade() would delete data"
