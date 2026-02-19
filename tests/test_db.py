from backend.db import DEFAULT_DATABASE_URL, get_database_url
from cloud_automation.db import (
    DEFAULT_DATABASE_URL as CLOUD_DEFAULT_DATABASE_URL,
    get_database_url as get_cloud_database_url,
)


def test_get_database_url_prefers_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres@localhost:5432/from_env",
    )

    result = get_database_url(
        "postgresql+psycopg://postgres@localhost:5432/from_override",
    )

    assert result == "postgresql+psycopg://postgres@localhost:5432/from_override"


def test_get_database_url_uses_default_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = get_database_url()

    assert result == DEFAULT_DATABASE_URL


def test_cloud_get_database_url_prefers_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "JOBS_DATABASE_URL",
        "postgresql+psycopg://postgres@localhost:5432/from_jobs_env",
    )

    result = get_cloud_database_url(
        "postgresql+psycopg://postgres@localhost:5432/from_jobs_override",
    )

    assert result == "postgresql+psycopg://postgres@localhost:5432/from_jobs_override"


def test_cloud_get_database_url_uses_default_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("JOBS_DATABASE_URL", raising=False)

    result = get_cloud_database_url()

    assert result == CLOUD_DEFAULT_DATABASE_URL
