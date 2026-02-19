from backend.db import DEFAULT_DATABASE_URL, get_database_url


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
