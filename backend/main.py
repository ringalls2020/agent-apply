import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .cloud_client import CloudAutomationClient
from .graphql_schema import schema as graphql_schema
from .auth import extract_bearer_token
from .config import load_backend_config, parse_int_env
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
from .models import ApplyAttemptCallback, CallbackAckResponse
from .security import (
    SecurityError,
    validate_profile_encryption_config,
    verify_body_signature,
    verify_hs256_jwt,
)
from .services import (
    CloudOrchestrationService,
    MainPlatformStore,
    PostgresStore,
)

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
logger = logging.getLogger(__name__)


def create_app(
    database_url: str | None = None,
    cloud_client: CloudAutomationClient | None = None,
) -> FastAPI:
    configure_logging()
    config = load_backend_config()
    app_env = config.app_env
    require_profile_encryption = app_env not in {"dev", "development", "local", "test"}
    validate_profile_encryption_config(required=require_profile_encryption)
    resolved_database_url = get_database_url(database_url)
    logger.info(
        "app_initializing",
        extra={"database_url": redact_database_url(resolved_database_url)},
    )
    engine = create_db_engine(resolved_database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.enable_schema_create:
            logger.info("db_schema_initialization_started")
            Base.metadata.create_all(bind=app.state.engine)
            logger.info("db_schema_initialization_completed")
        try:
            yield
        finally:
            close_cloud_client = getattr(app.state.cloud_client, "close", None)
            if callable(close_cloud_client):
                close_cloud_client()
            logger.info("db_engine_disposal_started")
            app.state.engine.dispose()
            logger.info("db_engine_disposal_completed")

    fastapi_app = FastAPI(title="Agent Apply", version="0.2.0", lifespan=lifespan)
    fastapi_app.state.engine = engine
    fastapi_app.state.enable_schema_create = config.enable_schema_create
    fastapi_app.state.job_listing_ttl_days = config.job_listing_ttl_days
    fastapi_app.state.agent_run_match_poll_interval_seconds = (
        config.agent_run_match_poll_interval_seconds
    )
    fastapi_app.state.agent_run_match_poll_max_attempts = config.agent_run_match_poll_max_attempts
    fastapi_app.state.enable_dev_run_agent = config.enable_dev_run_agent
    fastapi_app.state.enable_run_agent_discovery_kick = config.enable_run_agent_discovery_kick

    # Application records store shared by GraphQL and orchestration flows.
    fastapi_app.state.store = PostgresStore(
        session_factory=session_factory,
        job_listing_ttl_days=fastapi_app.state.job_listing_ttl_days,
    )

    # New main-platform stack.
    fastapi_app.state.main_store = MainPlatformStore(session_factory=session_factory)
    fastapi_app.state.cloud_client = cloud_client or CloudAutomationClient.from_env()
    fastapi_app.state.orchestrator = CloudOrchestrationService(
        store=fastapi_app.state.main_store,
        cloud_client=fastapi_app.state.cloud_client,
        application_store=fastapi_app.state.store,
        default_daily_cap=config.default_apply_daily_cap,
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
    fastapi_app.state.callback_max_clock_skew_seconds = parse_int_env(
        "CLOUD_CALLBACK_MAX_CLOCK_SKEW_SECONDS", 300
    )
    fastapi_app.state.required_client_subject = os.getenv(
        "CLOUD_CALLBACK_REQUIRED_CLIENT_SUBJECT", ""
    ).strip()
    fastapi_app.state.user_auth_signing_secret = os.getenv(
        "USER_AUTH_SIGNING_SECRET", "dev-user-auth-secret"
    )
    fastapi_app.state.user_auth_issuer = os.getenv("USER_AUTH_ISSUER", "main-api")
    fastapi_app.state.user_auth_audience = os.getenv(
        "USER_AUTH_AUDIENCE", "agent-apply-frontend"
    )
    fastapi_app.state.user_auth_token_ttl_seconds = max(
        1, parse_int_env("USER_AUTH_TOKEN_TTL_SECONDS", 7 * 24 * 60 * 60)
    )
    fastapi_app.state.admin_enabled = config.admin_enabled
    fastapi_app.state.admin_secret = os.getenv("ADMIN_DASHBOARD_SECRET", "").strip()

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

    @fastapi_app.get("/health")
    def health() -> dict:
        logger.debug("health_checked")
        return {"status": "ok"}

    @fastapi_app.post("/graphql")
    async def graphql_endpoint(request: Request) -> JSONResponse:
        payload = await request.json()

        def _execute_graphql():
            return graphql_schema.execute(
                payload.get("query"),
                variable_values=payload.get("variables"),
                operation_name=payload.get("operationName"),
                context_value={"request": request},
            )

        result = await run_in_threadpool(_execute_graphql)
        response_payload: dict[str, object] = {}
        if result.data is not None:
            response_payload["data"] = result.data
        if result.errors:
            response_payload["errors"] = [
                {"message": error.message}
                for error in result.errors
            ]
        return JSONResponse(response_payload)

    @fastapi_app.get("/admin", response_class=HTMLResponse)
    def admin_dashboard(request: Request) -> HTMLResponse:
        if not fastapi_app.state.admin_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        if fastapi_app.state.admin_secret:
            if request.headers.get("x-admin-secret", "").strip() != fastapi_app.state.admin_secret:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin secret mismatch",
                )
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
            token = extract_bearer_token(auth_header)
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

    logger.info("app_initialized")

    return fastapi_app


app = create_app()
