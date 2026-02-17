from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from fastapi import FastAPI, HTTPException, Request, status

from .adapters import build_configured_adapters
from .db import create_db_engine, create_session_factory, get_database_url
from .db_models import Base
from .models import (
    ApplyRunCreated,
    ApplyRunRequest,
    ApplyRunStatusResponse,
    JobSearchResponse,
    MatchRunCreated,
    MatchRunRequest,
    MatchRunStatusResponse,
)
from .security import SecurityError, verify_hs256_jwt
from .services import ApplyService, CallbackEmitter, DiscoveryCoordinator, JobIntelStore, MatchingService

logger = logging.getLogger(__name__)


def _extract_bearer(auth_header: str | None) -> str:
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )
    return parts[1].strip()


def create_app(database_url: str | None = None) -> FastAPI:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

    engine = create_db_engine(get_database_url(database_url))
    session_factory = create_session_factory(engine)
    configured_adapters = build_configured_adapters()

    app = FastAPI(title="Job Intel API", version="0.1.0")
    app.state.engine = engine
    app.state.store = JobIntelStore(session_factory)
    app.state.discovery = DiscoveryCoordinator(
        store=app.state.store,
        adapters=configured_adapters,
    )
    app.state.matching = MatchingService(store=app.state.store)
    app.state.apply = ApplyService(store=app.state.store, callback_emitter=CallbackEmitter())

    app.state.auth_secret = os.getenv("CLOUD_AUTOMATION_SIGNING_SECRET", "dev-cloud-signing-secret")
    app.state.auth_audience = os.getenv("CLOUD_AUTOMATION_EXPECTED_AUDIENCE", "job-intel-api")
    app.state.auth_issuer = os.getenv("CLOUD_AUTOMATION_EXPECTED_ISSUER", "main-api")
    app.state.required_client_subject = os.getenv(
        "CLOUD_AUTOMATION_REQUIRED_CLIENT_SUBJECT", ""
    ).strip()

    interval = os.getenv("DISCOVERY_INTERVAL_SECONDS", "21600")
    try:
        app.state.discovery_interval_seconds = max(60, int(interval))
    except ValueError:
        app.state.discovery_interval_seconds = 21600
    app.state.enable_embedded_discovery_loop = (
        os.getenv("ENABLE_EMBEDDED_DISCOVERY_LOOP", "true").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if not configured_adapters:
        logger.warning("discovery_adapters_not_configured")
    else:
        logger.info(
            "discovery_adapters_configured",
            extra={
                "adapter_count": len(configured_adapters),
                "sources": [adapter.source_name for adapter in configured_adapters],
            },
        )

    async def discovery_loop() -> None:
        while True:
            try:
                app.state.discovery.run_discovery_once()
            except Exception:
                logger.exception("discovery_loop_iteration_failed")
            await asyncio.sleep(app.state.discovery_interval_seconds)

    def authorize(request: Request) -> None:
        token = _extract_bearer(request.headers.get("authorization"))
        if app.state.required_client_subject:
            presented_subject = request.headers.get("x-client-cert-subject", "")
            if presented_subject != app.state.required_client_subject:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="mTLS client subject mismatch",
                )
        try:
            verify_hs256_jwt(
                token=token,
                secret=app.state.auth_secret,
                audience=app.state.auth_audience,
                issuer=app.state.auth_issuer,
            )
        except SecurityError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid service token: {exc}",
            ) from exc

    @app.on_event("startup")
    async def startup() -> None:
        Base.metadata.create_all(bind=app.state.engine)
        if app.state.enable_embedded_discovery_loop:
            app.state.discovery_task = asyncio.create_task(discovery_loop())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        task = getattr(app.state, "discovery_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        app.state.engine.dispose()

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/discovery/run")
    async def run_discovery_now(request: Request) -> dict:
        authorize(request)
        app.state.discovery.run_discovery_once()
        return {"accepted": True}

    @app.get("/v1/jobs/search", response_model=JobSearchResponse)
    async def search_jobs(
        request: Request,
        q: str = "",
        location: str | None = None,
        limit: int = 25,
    ) -> JobSearchResponse:
        authorize(request)
        keywords = [item.strip() for item in q.split(",") if item.strip()]
        jobs = app.state.store.search_jobs(
            keywords=keywords,
            location=location,
            limit=min(max(limit, 1), 100),
        )
        return JobSearchResponse(jobs=jobs)

    @app.post("/v1/match-runs", response_model=MatchRunCreated)
    async def create_match_run(request: Request, payload: MatchRunRequest) -> MatchRunCreated:
        authorize(request)
        run_id = app.state.store.create_match_run(payload)
        asyncio.create_task(app.state.matching.execute(run_id))
        return MatchRunCreated(
            run_id=run_id,
            status="queued",
            status_url=f"/v1/match-runs/{run_id}",
        )

    @app.get("/v1/match-runs/{run_id}", response_model=MatchRunStatusResponse)
    async def get_match_run(request: Request, run_id: str) -> MatchRunStatusResponse:
        authorize(request)
        try:
            return app.state.store.get_match_run_status(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/v1/apply-runs", response_model=ApplyRunCreated)
    async def create_apply_run(request: Request, payload: ApplyRunRequest) -> ApplyRunCreated:
        authorize(request)
        run_id = app.state.store.create_apply_run(payload)
        asyncio.create_task(app.state.apply.execute(run_id))
        return ApplyRunCreated(
            run_id=run_id,
            status="queued",
            status_url=f"/v1/apply-runs/{run_id}",
        )

    @app.get("/v1/apply-runs/{run_id}", response_model=ApplyRunStatusResponse)
    async def get_apply_run(request: Request, run_id: str) -> ApplyRunStatusResponse:
        authorize(request)
        try:
            return app.state.store.get_apply_run_status(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return app

app = create_app()
