from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, status

from .db import (
    create_db_engine,
    create_session_factory,
    ensure_runtime_indexes,
    get_database_url,
)
from .db_models import Base
from .logging_config import (
    bind_request_logging_context,
    configure_logging,
    reset_request_logging_context,
)
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
from .services import (
    ApplyService,
    CallbackEmitter,
    CommonCrawlCoordinator,
    DiscoveryCoordinator,
    JobIntelStore,
    MatchingService,
)

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
    configure_logging()
    logger.info("cloud_app_initializing")

    engine = create_db_engine(get_database_url(database_url))
    session_factory = create_session_factory(engine)
    app_env = os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower()
    schema_create_default = app_env in {"local", "dev", "development", "test"}
    enable_schema_create = (
        os.getenv("ENABLE_CLOUD_SCHEMA_CREATE", str(schema_create_default).lower())
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    http_timeout = float(os.getenv("CLOUD_HTTP_TIMEOUT_SECONDS", "20"))
    shared_http_client = httpx.Client(timeout=http_timeout)
    ttl_raw = os.getenv("JOB_LISTING_TTL_DAYS", "21")
    try:
        job_listing_ttl_days = max(int(ttl_raw), 1)
    except ValueError:
        job_listing_ttl_days = 21

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.enable_schema_create:
            logger.info("cloud_db_schema_initialization_started")
            Base.metadata.create_all(bind=app.state.engine)
            logger.info("cloud_db_schema_initialization_completed")
        ensure_runtime_indexes(app.state.engine)
        if app.state.enable_embedded_discovery_loop:
            app.state.discovery_task = asyncio.create_task(discovery_loop())
        try:
            yield
        finally:
            task = getattr(app.state, "discovery_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            close_apply = getattr(app.state.apply, "close", None)
            if callable(close_apply):
                close_apply()
            app.state.http_client.close()
            logger.info("cloud_db_engine_disposal_started")
            app.state.engine.dispose()
            logger.info("cloud_db_engine_disposal_completed")

    app = FastAPI(title="Job Intel API", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.http_client = shared_http_client
    app.state.enable_schema_create = enable_schema_create
    app.state.job_listing_ttl_days = job_listing_ttl_days
    app.state.store = JobIntelStore(
        session_factory,
        job_listing_ttl_days=job_listing_ttl_days,
    )
    app.state.discovery = DiscoveryCoordinator(
        store=app.state.store,
        http_client=app.state.http_client,
    )
    app.state.common_crawl = CommonCrawlCoordinator(
        store=app.state.store,
        http_client=app.state.http_client,
    )
    app.state.matching = MatchingService(store=app.state.store)
    app.state.apply = ApplyService(
        store=app.state.store,
        callback_emitter=CallbackEmitter(http_client=app.state.http_client),
        http_client=app.state.http_client,
    )

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
        os.getenv("ENABLE_EMBEDDED_DISCOVERY_LOOP", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    logger.info("token_registry_discovery_enabled")
    logger.info("cloud_app_initialized")

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        request_id_header = request.headers.get("x-request-id")
        request_id = request_id_header.strip() if request_id_header else str(uuid4())
        if not request_id:
            request_id = str(uuid4())

        context_tokens = bind_request_logging_context(
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
        )
        request.state.request_id = request_id
        started_at = perf_counter()

        logger.info(
            "request_started",
            extra={"client_ip": request.client.host if request.client else None},
        )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            logger.exception("request_failed", extra={"duration_ms": duration_ms})
            raise
        else:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            response.headers["x-request-id"] = request_id
            logger.info(
                "request_completed",
                extra={
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response
        finally:
            reset_request_logging_context(context_tokens)

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
        include_archived: bool = False,
    ) -> JobSearchResponse:
        authorize(request)
        keywords = [item.strip() for item in q.split(",") if item.strip()]
        jobs = app.state.store.search_jobs(
            keywords=keywords,
            location=location,
            limit=min(max(limit, 1), 100),
            include_archived=include_archived,
        )
        return JobSearchResponse(jobs=jobs)

    @app.post("/v1/match-runs", response_model=MatchRunCreated)
    async def create_match_run(request: Request, payload: MatchRunRequest) -> MatchRunCreated:
        authorize(request)
        run_id = app.state.store.create_match_run(payload)
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
