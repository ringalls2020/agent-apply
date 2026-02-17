from __future__ import annotations

import os

from .live import GreenhouseLiveAdapter, LeverLiveAdapter, SmartRecruitersLiveAdapter
from .synthetic import SyntheticAdapter


def _csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def build_configured_adapters() -> list[object]:
    """Build adapters with live connectors when configured, otherwise synthetic fallbacks."""
    adapters: list[object] = []

    greenhouse_boards = _csv_env("GREENHOUSE_BOARD_TOKENS")
    lever_companies = _csv_env("LEVER_COMPANIES")
    smartrecruiters_companies = _csv_env("SMARTRECRUITERS_COMPANIES")

    if greenhouse_boards:
        adapters.append(GreenhouseLiveAdapter(greenhouse_boards))
    else:
        adapters.append(SyntheticAdapter("greenhouse"))

    if lever_companies:
        adapters.append(LeverLiveAdapter(lever_companies))
    else:
        adapters.append(SyntheticAdapter("lever"))

    if smartrecruiters_companies:
        adapters.append(SmartRecruitersLiveAdapter(smartrecruiters_companies))
    else:
        adapters.append(SyntheticAdapter("smartrecruiters"))

    # Remaining source families use synthetic placeholders until live connectors are configured.
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
