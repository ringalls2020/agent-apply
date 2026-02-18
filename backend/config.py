from __future__ import annotations

import os
from dataclasses import dataclass


_TRUE_VALUES = {"1", "true", "yes", "on"}


def parse_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


@dataclass(frozen=True)
class BackendConfig:
    app_env: str
    job_listing_ttl_days: int
    default_apply_daily_cap: int
    agent_run_match_poll_interval_seconds: float
    agent_run_match_poll_max_attempts: int
    admin_enabled: bool
    enable_schema_create: bool


def load_backend_config() -> BackendConfig:
    app_env = os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower()
    admin_enabled_default = app_env in {"dev", "development", "local", "test"}
    poll_interval_raw = os.getenv("AGENT_RUN_MATCH_POLL_INTERVAL_SECONDS", "0.5")
    try:
        poll_interval_seconds = float(poll_interval_raw)
    except ValueError:
        poll_interval_seconds = 0.5

    return BackendConfig(
        app_env=app_env,
        job_listing_ttl_days=max(1, parse_int_env("JOB_LISTING_TTL_DAYS", 21)),
        default_apply_daily_cap=parse_int_env("DEFAULT_APPLY_DAILY_CAP", 25),
        agent_run_match_poll_interval_seconds=max(0.05, poll_interval_seconds),
        agent_run_match_poll_max_attempts=max(
            1, parse_int_env("AGENT_RUN_MATCH_POLL_MAX_ATTEMPTS", 40)
        ),
        admin_enabled=parse_bool_env(
            "ENABLE_ADMIN_DASHBOARD",
            default=admin_enabled_default,
        ),
        enable_schema_create=parse_bool_env(
            "ENABLE_MAIN_SCHEMA_CREATE",
            default=app_env in {"local", "dev", "development", "test"},
        ),
    )
