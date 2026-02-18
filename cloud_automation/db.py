import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./jobs_intel.db"


class Base(DeclarativeBase):
    pass


def get_database_url(override: str | None = None) -> str:
    if override:
        return override
    return os.getenv("JOBS_DATABASE_URL", DEFAULT_DATABASE_URL)


def create_db_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        if ":memory:" in database_url:
            return create_engine(
                database_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        return create_engine(database_url, connect_args={"check_same_thread": False})
    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def ensure_runtime_indexes(engine: Engine) -> None:
    inspector = inspect(engine)
    statements_by_table = {
        "match_runs": [
            "CREATE INDEX IF NOT EXISTS ix_match_runs_status_started_at ON match_runs (status, started_at)"
        ],
        "apply_runs": [
            "CREATE INDEX IF NOT EXISTS ix_apply_runs_status_started_at ON apply_runs (status, started_at)"
        ],
        "apply_attempts": [
            "CREATE INDEX IF NOT EXISTS ix_apply_attempts_run_id ON apply_attempts (run_id)"
        ],
    }
    with engine.begin() as connection:
        for table_name, statements in statements_by_table.items():
            if not inspector.has_table(table_name):
                continue
            for statement in statements:
                connection.execute(text(statement))
