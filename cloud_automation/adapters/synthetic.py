from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlparse

from cloud_automation.models import NormalizedJob


class SyntheticAdapter:
    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        self._cursor = 0

    def discover(self, seeds: list[str], cursor: str | None = None) -> list[str]:
        start = int(cursor) if cursor is not None else self._cursor
        urls = [f"https://{self.source_name}.jobs.example/listing/{start + idx}" for idx in range(3)]
        self._cursor = start + len(urls)
        return urls

    def fetch(self, url: str) -> str:
        return f"raw<{self.source_name}>:{url}"

    def parse(self, raw: str, url: str) -> NormalizedJob:
        listing_id = url.rstrip("/").split("/")[-1]
        seed = f"{self.source_name}:{listing_id}"
        hashed = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        base_title = {
            "linkedin": "Software Engineer",
            "indeed": "Machine Learning Engineer",
            "greenhouse": "Backend Engineer",
            "lever": "Platform Engineer",
            "workday": "Data Engineer",
            "smartrecruiters": "Security Engineer",
            "ashby": "AI Product Engineer",
            "ziprecruiter": "Site Reliability Engineer",
            "wellfound": "Founding Engineer",
            "careers": "Automation Engineer",
        }.get(self.source_name, "Software Engineer")

        host = urlparse(url).hostname or "jobs.example"
        company_name = host.split(".")[0].title()
        posted_at = datetime.utcnow() - timedelta(hours=int(listing_id) % 72)

        return NormalizedJob(
            id=f"{self.source_name}-{hashed}",
            title=f"{base_title} {int(listing_id) + 1}",
            company=company_name,
            location="United States",
            salary=None,
            apply_url=url,
            source=self.source_name,
            posted_at=posted_at,
            description=(
                f"Auto-ingested synthetic listing from {self.source_name} for listing {listing_id}."
            ),
        )

    def next_cursor(self) -> str | None:
        return str(self._cursor)


def build_default_adapters() -> list[SyntheticAdapter]:
    return [
        SyntheticAdapter("linkedin"),
        SyntheticAdapter("indeed"),
        SyntheticAdapter("greenhouse"),
        SyntheticAdapter("lever"),
        SyntheticAdapter("workday"),
        SyntheticAdapter("smartrecruiters"),
        SyntheticAdapter("ashby"),
        SyntheticAdapter("ziprecruiter"),
        SyntheticAdapter("wellfound"),
        SyntheticAdapter("careers"),
    ]
