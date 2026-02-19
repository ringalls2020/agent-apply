from __future__ import annotations

import logging

from ..models import MatchRunRequest, MatchRunStatus, MatchedJob, NormalizedJob
from .job_store import JobIntelStore

logger = logging.getLogger(__name__)


class MatchingService:
    def __init__(self, *, store: JobIntelStore) -> None:
        self.store = store

    async def execute(self, run_id: str, *, assume_claimed: bool = False) -> None:
        if not assume_claimed and not self.store.claim_match_run(run_id):
            logger.info("match_run_not_claimed", extra={"run_id": run_id})
            return
        try:
            request = self.store.get_match_run_request(run_id)
            interests = [str(item).lower() for item in request.preferences.get("interests", [])]
            jobs = self.store.search_jobs(
                keywords=interests,
                location=request.location,
                limit=max(request.limit * 2, 40),
            )

            matches = [self._score_job(job=job, request=request) for job in jobs]
            matches = sorted(matches, key=lambda item: item.score, reverse=True)
            top_matches = [item for item in matches if item.score > 0.0][: request.limit]

            self.store.replace_match_results(run_id=run_id, matches=top_matches)
            self.store.set_match_run_status(run_id=run_id, status=MatchRunStatus.completed)
        except Exception as exc:
            logger.exception("match_run_failed", extra={"run_id": run_id})
            self.store.set_match_run_status(
                run_id=run_id,
                status=MatchRunStatus.failed,
                error=str(exc),
            )

    @staticmethod
    def _score_job(job: NormalizedJob, request: MatchRunRequest) -> MatchedJob:
        interests = [str(item).lower() for item in request.preferences.get("interests", [])]
        haystack = f"{job.title} {job.description}".lower()
        overlap = len([interest for interest in interests if interest in haystack])
        max_score = max(len(interests), 1)
        base_score = overlap / max_score

        reason = (
            f"Matched {overlap} preference keyword(s) from resume/preferences with source {job.source}."
        )

        return MatchedJob(
            external_job_id=job.id,
            title=job.title,
            company=job.company,
            location=job.location,
            apply_url=job.apply_url,
            source=job.source,
            reason=reason,
            score=min(max(base_score, 0.0), 1.0),
            posted_at=job.posted_at,
        )


__all__ = ["MatchingService"]
