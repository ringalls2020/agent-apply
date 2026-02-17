from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .models import ApplicationRecord


class InMemoryStore:
    def __init__(self) -> None:
        self.applications: Dict[str, ApplicationRecord] = {}

    def upsert(self, record: ApplicationRecord) -> ApplicationRecord:
        self.applications[record.id] = record
        return record

    def get(self, application_id: str) -> Optional[ApplicationRecord]:
        return self.applications.get(application_id)

    def delete(self, application_id: str) -> bool:
        if application_id in self.applications:
            del self.applications[application_id]
            return True
        return False

    def list_all(self) -> List[ApplicationRecord]:
        return sorted(
            self.applications.values(),
            key=lambda item: item.opportunity.discovered_at,
            reverse=True,
        )


class JsonFileStore(InMemoryStore):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        with self.path.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        for item in payload.get("applications", []):
            record = ApplicationRecord.model_validate(item)
            self.applications[record.id] = record

    def _flush(self) -> None:
        payload = {"applications": [item.model_dump(mode="json") for item in self.list_all()]}
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def upsert(self, record: ApplicationRecord) -> ApplicationRecord:
        result = super().upsert(record)
        self._flush()
        return result

    def delete(self, application_id: str) -> bool:
        deleted = super().delete(application_id)
        if deleted:
            self._flush()
        return deleted
