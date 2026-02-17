import os
import logging

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./agent_apply.db"
logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def redact_database_url(database_url: str) -> str:
    try:
        parsed = make_url(database_url)
    except Exception:
        return "<unparseable_database_url>"

    if parsed.password:
        parsed = parsed.set(password="***")

    return str(parsed)


def get_database_url(override: str | None = None) -> str:
    env_database_url = os.getenv("DATABASE_URL")
    if override:
        source = "override"
        resolved = override
    elif env_database_url:
        source = "env"
        resolved = env_database_url
    else:
        source = "default"
        resolved = DEFAULT_DATABASE_URL

    logger.info(
        "database_url_resolved",
        extra={
            "source": source,
            "database_url": redact_database_url(resolved),
        },
    )
    return resolved


def create_db_engine(database_url: str) -> Engine:
    sanitized_url = redact_database_url(database_url)
    if database_url.startswith("sqlite"):
        if ":memory:" in database_url:
            engine = create_engine(
                database_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            logger.info(
                "database_engine_created",
                extra={
                    "database_url": sanitized_url,
                    "dialect": "sqlite",
                    "in_memory": True,
                },
            )
            return engine

        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
        )
        logger.info(
            "database_engine_created",
            extra={
                "database_url": sanitized_url,
                "dialect": "sqlite",
                "in_memory": False,
            },
        )
        return engine

    parsed = make_url(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    logger.info(
        "database_engine_created",
        extra={
            "database_url": sanitized_url,
            "dialect": parsed.get_backend_name(),
            "driver": parsed.get_driver_name(),
            "pool_pre_ping": True,
        },
    )
    return engine


def create_session_factory(engine: Engine) -> sessionmaker:
    logger.debug("session_factory_created")
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
