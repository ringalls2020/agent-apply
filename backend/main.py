import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .cloud_client import CloudAutomationClient, CloudClientError
from .db import (
    Base,
    create_db_engine,
    create_session_factory,
    get_database_url,
    redact_database_url,
)
from .logging_config import (
    bind_request_logging_context,
    configure_logging,
    reset_request_logging_context,
)
from .models import (
    AgentRunRequest,
    AgentRunResponse,
    ApplyAttemptCallback,
    ApplyRunStartRequest,
    ApplyRunStartResponse,
    ApplyRunStatusResponse,
    CallbackAckResponse,
    MatchRunStartRequest,
    MatchRunStartResponse,
    MatchRunStatusResponse,
    PreferenceResponse,
    PreferenceUpsertRequest,
    ResumeResponse,
    ResumeUpsertRequest,
    UserResponse,
    UserUpsertRequest,
)
from .security import SecurityError, verify_body_signature, verify_hs256_jwt
from .services import (
    CloudOrchestrationService,
    MainPlatformStore,
    OpportunityAgent,
    PostgresStore,
)

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
logger = logging.getLogger(__name__)


def _parse_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _extract_bearer_token(auth_header: str | None) -> str:
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    parts = auth_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )
    return parts[1].strip()


def create_app(
    database_url: str | None = None,
    cloud_client: CloudAutomationClient | None = None,
) -> FastAPI:
    configure_logging()
    resolved_database_url = get_database_url(database_url)
    logger.info(
        "app_initializing",
        extra={"database_url": redact_database_url(resolved_database_url)},
    )
    engine = create_db_engine(resolved_database_url)
    session_factory = create_session_factory(engine)

    fastapi_app = FastAPI(title="Agent Apply", version="0.2.0")
    fastapi_app.state.engine = engine

    # Legacy agent stack kept for backward compatibility.
    fastapi_app.state.store = PostgresStore(session_factory=session_factory)
    fastapi_app.state.agent = OpportunityAgent(store=fastapi_app.state.store)

    # New main-platform stack.
    fastapi_app.state.main_store = MainPlatformStore(session_factory=session_factory)
    fastapi_app.state.cloud_client = cloud_client or CloudAutomationClient.from_env()
    fastapi_app.state.orchestrator = CloudOrchestrationService(
        store=fastapi_app.state.main_store,
        cloud_client=fastapi_app.state.cloud_client,
        default_daily_cap=_parse_int_env("DEFAULT_APPLY_DAILY_CAP", 25),
    )

    fastapi_app.state.callback_signing_secret = os.getenv(
        "CLOUD_CALLBACK_SIGNING_SECRET",
        os.getenv("CLOUD_AUTOMATION_SIGNING_SECRET", "dev-cloud-signing-secret"),
    )
    fastapi_app.state.callback_signature_secret = os.getenv(
        "CLOUD_CALLBACK_SIGNATURE_SECRET",
        fastapi_app.state.callback_signing_secret,
    )
    fastapi_app.state.callback_audience = os.getenv("CLOUD_CALLBACK_AUDIENCE", "main-api")
    fastapi_app.state.callback_issuer = os.getenv("CLOUD_CALLBACK_ISSUER", "job-intel-api")
    fastapi_app.state.callback_max_clock_skew_seconds = _parse_int_env(
        "CLOUD_CALLBACK_MAX_CLOCK_SKEW_SECONDS", 300
    )
    fastapi_app.state.required_client_subject = os.getenv(
        "CLOUD_CALLBACK_REQUIRED_CLIENT_SUBJECT", ""
    ).strip()

    @fastapi_app.middleware("http")
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
            extra={
                "client_ip": request.client.host if request.client else None,
            },
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

    @fastapi_app.on_event("startup")
    def init_db_schema() -> None:
        logger.info("db_schema_initialization_started")
        Base.metadata.create_all(bind=fastapi_app.state.engine)
        logger.info("db_schema_initialization_completed")

    @fastapi_app.get("/health")
    def health() -> dict:
        logger.debug("health_checked")
        return {"status": "ok"}

    # Legacy compatibility routes.
    @fastapi_app.post("/agent/run", response_model=AgentRunResponse)
    def run_agent(payload: AgentRunRequest) -> AgentRunResponse:
        logger.info(
            "run_agent_requested",
            extra={
                "max_opportunities": payload.max_opportunities,
                "interest_count": len(payload.profile.interests),
            },
        )
        applications = fastapi_app.state.agent.run(payload)
        logger.info(
            "run_agent_completed",
            extra={"application_count": len(applications)},
        )
        return AgentRunResponse(applications=applications)

    @fastapi_app.get("/applications", response_model=AgentRunResponse)
    def list_applications() -> AgentRunResponse:
        applications = fastapi_app.state.store.list_all()
        logger.info(
            "applications_listed",
            extra={"application_count": len(applications)},
        )
        return AgentRunResponse(applications=applications)

    @fastapi_app.get("/admin", response_class=HTMLResponse)
    def admin_dashboard(request: Request) -> HTMLResponse:
        apps = fastapi_app.state.store.list_all()
        stats = {
            "total": len(apps),
            "applied": len([a for a in apps if a.submitted_at]),
            "notified": len([a for a in apps if a.notified_at]),
        }
        logger.debug(
            "admin_dashboard_rendered",
            extra={
                "total": stats["total"],
                "applied": stats["applied"],
                "notified": stats["notified"],
            },
        )
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"applications": apps, "stats": stats},
        )

    # New system-of-record routes.
    @fastapi_app.put("/v1/users/{user_id}", response_model=UserResponse)
    def upsert_user(user_id: str, payload: UserUpsertRequest) -> UserResponse:
        return fastapi_app.state.orchestrator.upsert_user(user_id=user_id, payload=payload)

    @fastapi_app.get("/v1/users/{user_id}", response_model=UserResponse)
    def get_user(user_id: str) -> UserResponse:
        user = fastapi_app.state.orchestrator.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return user

    @fastapi_app.put("/v1/users/{user_id}/preferences", response_model=PreferenceResponse)
    def upsert_preferences(user_id: str, payload: PreferenceUpsertRequest) -> PreferenceResponse:
        try:
            return fastapi_app.state.orchestrator.upsert_preferences(
                user_id=user_id,
                payload=payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @fastapi_app.get("/v1/users/{user_id}/preferences", response_model=PreferenceResponse)
    def get_preferences(user_id: str) -> PreferenceResponse:
        preferences = fastapi_app.state.orchestrator.get_preferences(user_id)
        if preferences is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Preferences not found",
            )
        return preferences

    @fastapi_app.put("/v1/users/{user_id}/resume", response_model=ResumeResponse)
    def upsert_resume(user_id: str, payload: ResumeUpsertRequest) -> ResumeResponse:
        try:
            return fastapi_app.state.orchestrator.upsert_resume(user_id=user_id, payload=payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @fastapi_app.get("/v1/users/{user_id}/resume", response_model=ResumeResponse)
    def get_resume(user_id: str) -> ResumeResponse:
        resume = fastapi_app.state.orchestrator.get_resume(user_id)
        if resume is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resume not found",
            )
        return resume

    @fastapi_app.post("/v1/users/{user_id}/match-runs", response_model=MatchRunStartResponse)
    def start_match_run(user_id: str, payload: MatchRunStartRequest) -> MatchRunStartResponse:
        try:
            return fastapi_app.state.orchestrator.start_match_run(
                user_id=user_id,
                payload=payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except CloudClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @fastapi_app.get(
        "/v1/users/{user_id}/match-runs/{run_id}",
        response_model=MatchRunStatusResponse,
    )
    def get_match_run(user_id: str, run_id: str) -> MatchRunStatusResponse:
        try:
            return fastapi_app.state.orchestrator.get_match_run(user_id=user_id, run_id=run_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except CloudClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @fastapi_app.post("/v1/users/{user_id}/apply-runs", response_model=ApplyRunStartResponse)
    def start_apply_run(user_id: str, payload: ApplyRunStartRequest) -> ApplyRunStartResponse:
        try:
            return fastapi_app.state.orchestrator.start_apply_run(
                user_id=user_id,
                payload=payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except CloudClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @fastapi_app.get(
        "/v1/users/{user_id}/apply-runs/{run_id}",
        response_model=ApplyRunStatusResponse,
    )
    def get_apply_run(user_id: str, run_id: str) -> ApplyRunStatusResponse:
        try:
            return fastapi_app.state.orchestrator.get_apply_run(user_id=user_id, run_id=run_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except CloudClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @fastapi_app.post(
        "/internal/cloud/callbacks/apply-result",
        response_model=CallbackAckResponse,
    )
    async def apply_result_callback(request: Request) -> CallbackAckResponse:
        raw_body = await request.body()
        auth_header = request.headers.get("authorization")
        timestamp = request.headers.get("x-cloud-timestamp", "")
        nonce = request.headers.get("x-cloud-nonce", "")
        signature = request.headers.get("x-cloud-signature", "")
        idempotency_key = request.headers.get("x-idempotency-key", "")
        client_subject = request.headers.get("x-client-cert-subject", "")

        if not idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing x-idempotency-key header",
            )

        if fastapi_app.state.required_client_subject:
            if fastapi_app.state.required_client_subject != client_subject:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="mTLS client subject mismatch",
                )

        try:
            token = _extract_bearer_token(auth_header)
            verify_hs256_jwt(
                token=token,
                secret=fastapi_app.state.callback_signing_secret,
                audience=fastapi_app.state.callback_audience,
                issuer=fastapi_app.state.callback_issuer,
            )
        except (HTTPException, SecurityError) as exc:
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid callback token: {exc}",
            ) from exc

        if not timestamp or not nonce or not signature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing callback signature headers",
            )

        try:
            timestamp_int = int(timestamp)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid x-cloud-timestamp",
            ) from exc

        now = int(datetime.now(timezone.utc).timestamp())
        if abs(now - timestamp_int) > fastapi_app.state.callback_max_clock_skew_seconds:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Callback timestamp outside allowed skew",
            )

        if not verify_body_signature(
            body=raw_body,
            timestamp=timestamp,
            nonce=nonce,
            secret=fastapi_app.state.callback_signature_secret,
            signature=signature,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid callback signature",
            )

        try:
            payload = ApplyAttemptCallback.model_validate_json(raw_body)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid callback payload: {exc}",
            ) from exc

        if payload.idempotency_key != idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Idempotency key mismatch between header and payload",
            )

        is_new = fastapi_app.state.orchestrator.register_webhook_event_if_new(
            idempotency_key=idempotency_key,
            event_type=payload.event_type,
            external_run_id=payload.run_id,
            raw_body=raw_body,
        )

        if not is_new:
            return CallbackAckResponse(accepted=True, idempotency_key=idempotency_key)

        fastapi_app.state.orchestrator.process_apply_attempt_callback(payload)
        fastapi_app.state.orchestrator.mark_webhook_processed(
            idempotency_key=idempotency_key
        )
        return CallbackAckResponse(accepted=True, idempotency_key=idempotency_key)

    @fastapi_app.on_event("shutdown")
    def dispose_db_engine() -> None:
        logger.info("db_engine_disposal_started")
        fastapi_app.state.engine.dispose()
        logger.info("db_engine_disposal_completed")

    logger.info("app_initialized")

    return fastapi_app


app = create_app()
