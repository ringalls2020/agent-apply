from __future__ import annotations

import os

import httpx

from .live import GreenhouseLiveAdapter, LeverLiveAdapter, SmartRecruitersLiveAdapter
from .synthetic import SyntheticAdapter


def _csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_configured_adapters(*, http_client: httpx.Client | None = None) -> list[object]:
    """Build adapters with live connectors when configured, otherwise synthetic fallbacks."""
    adapters: list[object] = []
    use_only_live_adapters = _env_flag("USE_ONLY_LIVE_ADAPTERS", default=False)

    greenhouse_boards = _csv_env("GREENHOUSE_BOARD_TOKENS")
    lever_companies = _csv_env("LEVER_COMPANIES")
    smartrecruiters_companies = _csv_env("SMARTRECRUITERS_COMPANIES")

    if greenhouse_boards:
        adapters.append(
            GreenhouseLiveAdapter(greenhouse_boards, client=http_client)
        )
    elif not use_only_live_adapters:
        adapters.append(SyntheticAdapter("greenhouse"))

    if lever_companies:
        adapters.append(LeverLiveAdapter(lever_companies, client=http_client))
    elif not use_only_live_adapters:
        adapters.append(SyntheticAdapter("lever"))

    if smartrecruiters_companies:
        adapters.append(
            SmartRecruitersLiveAdapter(
                smartrecruiters_companies,
                client=http_client,
            )
        )
    elif not use_only_live_adapters:
        adapters.append(SyntheticAdapter("smartrecruiters"))

    # Remaining source families use synthetic placeholders until live connectors are configured.
    if not use_only_live_adapters:
        adapters.extend(
            [
                SyntheticAdapter("linkedin"),
                SyntheticAdapter("indeed"),
                SyntheticAdapter("workday"),
                SyntheticAdapter("ashby"),
                SyntheticAdapter("ziprecruiter"),
                SyntheticAdapter("wellfound"),
                SyntheticAdapter("careers"),
            ]
        )

    return adapters


__all__ = [
    "GreenhouseLiveAdapter",
    "LeverLiveAdapter",
    "SmartRecruitersLiveAdapter",
    "SyntheticAdapter",
    "build_configured_adapters",
]
