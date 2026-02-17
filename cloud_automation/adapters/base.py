from __future__ import annotations

from typing import List, Optional, Protocol

from cloud_automation.models import NormalizedJob


class SourceAdapter(Protocol):
    source_name: str

    def discover(self, seeds: List[str], cursor: Optional[str] = None) -> List[str]:
        ...

    def fetch(self, url: str) -> str:
        ...

    def parse(self, raw: str, url: str) -> NormalizedJob:
        ...

    def next_cursor(self) -> Optional[str]:
        ...
