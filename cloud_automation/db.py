import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres@localhost:5432/jobs_intel"


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
        "discovery_seeds": [
            "CREATE INDEX IF NOT EXISTS ix_discovery_seeds_domain ON discovery_seeds (domain)",
            "CREATE INDEX IF NOT EXISTS ix_discovery_seeds_status ON discovery_seeds (status)",
        ],
        "domain_robots_cache": [
            "CREATE INDEX IF NOT EXISTS ix_domain_robots_cache_expires_at ON domain_robots_cache (expires_at)",
            "CREATE INDEX IF NOT EXISTS ix_domain_robots_cache_status ON domain_robots_cache (status)",
        ],
        "ats_tokens": [
            "CREATE INDEX IF NOT EXISTS ix_ats_tokens_provider_status ON ats_tokens (provider, status)",
            "CREATE INDEX IF NOT EXISTS ix_ats_tokens_status_seen ON ats_tokens (status, last_seen_at)",
        ],
        "ats_token_evidence": [
            "CREATE INDEX IF NOT EXISTS ix_ats_token_evidence_token_id ON ats_token_evidence (token_id)"
        ],
        "seed_manifest_entries": [
            "CREATE INDEX IF NOT EXISTS ix_seed_manifest_entries_active ON seed_manifest_entries (is_active)",
            "CREATE INDEX IF NOT EXISTS ix_seed_manifest_entries_source_page ON seed_manifest_entries (source_page_url)",
        ],
        "seed_manifest_build_runs": [
            "CREATE INDEX IF NOT EXISTS ix_seed_manifest_build_runs_started_at ON seed_manifest_build_runs (started_at)",
            "CREATE INDEX IF NOT EXISTS ix_seed_manifest_build_runs_status ON seed_manifest_build_runs (status)",
        ],
        "discovery_refresh_requests": [
            "CREATE INDEX IF NOT EXISTS ix_discovery_refresh_requests_status_created_at ON discovery_refresh_requests (status, created_at)",
        ],
        "job_identities": [
            "CREATE INDEX IF NOT EXISTS ix_job_identities_canonical_job_id ON job_identities (canonical_job_id)",
            "CREATE INDEX IF NOT EXISTS ix_job_identities_provider_token ON job_identities (provider, provider_token)",
        ],
    }
    with engine.begin() as connection:
        for table_name, statements in statements_by_table.items():
            if not inspector.has_table(table_name):
                continue
            for statement in statements:
                connection.execute(text(statement))
