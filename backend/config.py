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


def parse_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class BackendConfig:
    app_env: str
    job_listing_ttl_days: int
    default_apply_daily_cap: int
    agent_run_match_poll_interval_seconds: float
    agent_run_match_poll_max_attempts: int
    enable_dev_run_agent: bool
    enable_run_agent_discovery_kick: bool
    use_preference_graph_matching: bool
    enable_preference_graph_shadow_scoring: bool
    eval_default_window_days: int
    eval_gate_min_impressions: int
    eval_gate_min_runs: int
    eval_gate_precision_at_5_min: float
    eval_gate_precision_at_10_min: float
    eval_gate_ndcg_at_10_min: float
    eval_gate_hard_constraint_violation_max: float
    eval_gate_ctr_min: float
    eval_gate_apply_through_min: float
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
    dev_like_env = app_env in {"dev", "development", "local", "test"}

    return BackendConfig(
        app_env=app_env,
        job_listing_ttl_days=max(1, parse_int_env("JOB_LISTING_TTL_DAYS", 21)),
        default_apply_daily_cap=parse_int_env("DEFAULT_APPLY_DAILY_CAP", 25),
        agent_run_match_poll_interval_seconds=max(0.05, poll_interval_seconds),
        agent_run_match_poll_max_attempts=max(
            1, parse_int_env("AGENT_RUN_MATCH_POLL_MAX_ATTEMPTS", 40)
        ),
        enable_dev_run_agent=parse_bool_env(
            "ENABLE_DEV_RUN_AGENT",
            default=dev_like_env,
        ),
        enable_run_agent_discovery_kick=parse_bool_env(
            "ENABLE_RUN_AGENT_DISCOVERY_KICK",
            default=dev_like_env,
        ),
        use_preference_graph_matching=parse_bool_env(
            "USE_PREFERENCE_GRAPH_MATCHING",
            default=False,
        ),
        enable_preference_graph_shadow_scoring=parse_bool_env(
            "ENABLE_PREFERENCE_GRAPH_SHADOW_SCORING",
            default=True,
        ),
        eval_default_window_days=max(1, parse_int_env("EVAL_DEFAULT_WINDOW_DAYS", 14)),
        eval_gate_min_impressions=max(1, parse_int_env("EVAL_GATE_MIN_IMPRESSIONS", 50)),
        eval_gate_min_runs=max(1, parse_int_env("EVAL_GATE_MIN_RUNS", 10)),
        eval_gate_precision_at_5_min=max(
            0.0, min(1.0, parse_float_env("EVAL_GATE_PRECISION_AT_5_MIN", 0.35))
        ),
        eval_gate_precision_at_10_min=max(
            0.0, min(1.0, parse_float_env("EVAL_GATE_PRECISION_AT_10_MIN", 0.25))
        ),
        eval_gate_ndcg_at_10_min=max(
            0.0, min(1.0, parse_float_env("EVAL_GATE_NDCG_AT_10_MIN", 0.45))
        ),
        eval_gate_hard_constraint_violation_max=max(
            0.0,
            min(
                1.0,
                parse_float_env(
                    "EVAL_GATE_HARD_CONSTRAINT_VIOLATION_MAX",
                    0.01,
                ),
            ),
        ),
        eval_gate_ctr_min=max(
            0.0, min(1.0, parse_float_env("EVAL_GATE_CTR_MIN", 0.10))
        ),
        eval_gate_apply_through_min=max(
            0.0, min(1.0, parse_float_env("EVAL_GATE_APPLY_THROUGH_MIN", 0.03))
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
