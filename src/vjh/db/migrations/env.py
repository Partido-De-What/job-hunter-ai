"""Alembic environment. Called by vjh.db.migrate, never with an alembic.ini."""

from alembic import context
from sqlalchemy import create_engine

from vjh.db.tables import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as conn:
        _run(conn)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,   # SQLite-safe ALTER TABLE
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
