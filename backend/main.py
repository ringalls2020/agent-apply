import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter, sleep
from uuid import NAMESPACE_URL, uuid4, uuid5

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
    ApplicationProfileResponse,
    ApplicationProfileUpsertRequest,
    ApplicationRecord,
    ApplicationStatus,
    AgentRunResponse,
    AuthLoginRequest,
    AuthResponse,
    AuthSignupRequest,
    AuthUserProfile,
    ApplyAttemptCallback,
    ApplyRunStartRequest,
    ApplyRunStartResponse,
    ApplyRunStatusResponse,
    ApplyTargetJob,
    CallbackAckResponse,
    MatchRunStartRequest,
    MatchRunStartResponse,
    MatchRunStatus,
    MatchRunStatusResponse,
    Opportunity,
    PreferenceResponse,
    PreferenceUpsertRequest,
    ResumeResponse,
    ResumeUpsertRequest,
    UserResponse,
    UserUpsertRequest,
)
from .security import (
    SecurityError,
    create_hs256_jwt,
    hash_password,
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
    app_env = os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower()
    require_profile_encryption = app_env not in {"dev", "development", "local", "test"}
    validate_profile_encryption_config(required=require_profile_encryption)
    resolved_database_url = get_database_url(database_url)
    logger.info(
        "app_initializing",
        extra={"database_url": redact_database_url(resolved_database_url)},
    )
    engine = create_db_engine(resolved_database_url)
    session_factory = create_session_factory(engine)

    fastapi_app = FastAPI(title="Agent Apply", version="0.2.0")
    fastapi_app.state.engine = engine

    # Legacy application records store (used for /v1/applications).
    fastapi_app.state.store = PostgresStore(session_factory=session_factory)

    # New main-platform stack.
    fastapi_app.state.main_store = MainPlatformStore(session_factory=session_factory)
    fastapi_app.state.cloud_client = cloud_client or CloudAutomationClient.from_env()
    fastapi_app.state.orchestrator = CloudOrchestrationService(
        store=fastapi_app.state.main_store,
        cloud_client=fastapi_app.state.cloud_client,
        application_store=fastapi_app.state.store,
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
    fastapi_app.state.user_auth_signing_secret = os.getenv(
        "USER_AUTH_SIGNING_SECRET", "dev-user-auth-secret"
    )
    fastapi_app.state.user_auth_issuer = os.getenv("USER_AUTH_ISSUER", "main-api")
    fastapi_app.state.user_auth_audience = os.getenv(
        "USER_AUTH_AUDIENCE", "agent-apply-frontend"
    )
    fastapi_app.state.user_auth_token_ttl_seconds = max(
        1, _parse_int_env("USER_AUTH_TOKEN_TTL_SECONDS", 7 * 24 * 60 * 60)
    )

    def _create_user_access_token(user_id: str) -> str:
        return create_hs256_jwt(
            payload={"sub": user_id},
            secret=fastapi_app.state.user_auth_signing_secret,
            issuer=fastapi_app.state.user_auth_issuer,
            audience=fastapi_app.state.user_auth_audience,
            expires_in_seconds=fastapi_app.state.user_auth_token_ttl_seconds,
        )

    def _authenticated_user_id_from_request(request: Request) -> str:
        token = _extract_bearer_token(request.headers.get("authorization"))
        try:
            claims = verify_hs256_jwt(
                token=token,
                secret=fastapi_app.state.user_auth_signing_secret,
                audience=fastapi_app.state.user_auth_audience,
                issuer=fastapi_app.state.user_auth_issuer,
            )
        except SecurityError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid user auth token: {exc}",
            ) from exc

        user_id = str(claims.get("sub", "")).strip()
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User auth token missing subject",
            )
        return user_id

    def _build_auth_user_profile(user_id: str) -> AuthUserProfile:
        user = fastapi_app.state.main_store.get_user(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found for token subject",
            )
        preferences = fastapi_app.state.main_store.get_preferences(user_id)
        resume = fastapi_app.state.main_store.get_resume(user_id)
        profile = fastapi_app.state.main_store.get_application_profile(user_id)
        return AuthUserProfile(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            interests=preferences.interests if preferences else [],
            applications_per_day=preferences.applications_per_day if preferences else 25,
            resume_filename=resume.filename if resume else None,
            autosubmit_enabled=profile.autosubmit_enabled if profile else False,
        )

    def _authenticated_user_id_must_match(request: Request, user_id: str) -> None:
        authenticated_user_id = _authenticated_user_id_from_request(request)
        if authenticated_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot access another user's profile",
            )

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

    # Legacy compatibility routes kept as explicit deprecations.
    @fastapi_app.post("/agent/run")
    def run_agent_legacy() -> None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Deprecated endpoint. Use POST /v1/agent/run with Authorization bearer token.",
        )

    @fastapi_app.get("/applications")
    def list_applications_legacy() -> None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Deprecated endpoint. Use GET /v1/applications with Authorization bearer token.",
        )

    @fastapi_app.post(
        "/v1/auth/signup",
        response_model=AuthResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def auth_signup(payload: AuthSignupRequest) -> AuthResponse:
        existing_user = fastapi_app.state.main_store.get_user_by_email(payload.email)
        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account with this email already exists.",
            )

        user_id = str(uuid4())
        user = fastapi_app.state.orchestrator.upsert_user(
            user_id=user_id,
            payload=UserUpsertRequest(
                full_name=payload.full_name,
                email=payload.email,
            ),
        )
        password_salt, password_hash = hash_password(payload.password)
        fastapi_app.state.main_store.set_user_password(
            user_id=user.id,
            password_salt=password_salt,
            password_hash=password_hash,
        )

        return AuthResponse(
            token=_create_user_access_token(user.id),
            user=_build_auth_user_profile(user.id),
        )

    @fastapi_app.post("/v1/auth/login", response_model=AuthResponse)
    def auth_login(payload: AuthLoginRequest) -> AuthResponse:
        user = fastapi_app.state.main_store.verify_user_credentials(
            email=payload.email,
            password=payload.password,
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            )
        return AuthResponse(
            token=_create_user_access_token(user.id),
            user=_build_auth_user_profile(user.id),
        )

    @fastapi_app.get("/v1/auth/me", response_model=AuthUserProfile)
    def auth_me(request: Request) -> AuthUserProfile:
        user_id = _authenticated_user_id_from_request(request)
        return _build_auth_user_profile(user_id)

    @fastapi_app.post("/v1/agent/run", response_model=AgentRunResponse)
    def run_agent_for_authenticated_user(request: Request) -> AgentRunResponse:
        user_id = _authenticated_user_id_from_request(request)
        preferences = fastapi_app.state.main_store.get_preferences(user_id)
        if preferences is None or not preferences.interests:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User preferences not found",
            )

        resume = fastapi_app.state.main_store.get_resume(user_id)
        if resume is None or not resume.resume_text.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User resume not found",
            )
        profile = fastapi_app.state.main_store.get_application_profile(user_id)
        autosubmit_enabled = profile.autosubmit_enabled if profile else False

        match_limit = min(max(preferences.applications_per_day, 1), 100)
        poll_interval_seconds = float(os.getenv("AGENT_RUN_MATCH_POLL_INTERVAL_SECONDS", "0.5"))
        poll_max_attempts = max(1, _parse_int_env("AGENT_RUN_MATCH_POLL_MAX_ATTEMPTS", 40))

        try:
            fastapi_app.state.cloud_client.run_discovery_now()
        except CloudClientError:
            logger.warning(
                "agent_run_discovery_trigger_failed",
                extra={"user_id": user_id},
            )

        try:
            started = fastapi_app.state.orchestrator.start_match_run(
                user_id=user_id,
                payload=MatchRunStartRequest(
                    limit=match_limit,
                    location=preferences.locations[0] if preferences.locations else None,
                    seniority=preferences.seniority,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except CloudClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

        latest_status: MatchRunStatusResponse | None = None
        for _ in range(poll_max_attempts):
            try:
                latest_status = fastapi_app.state.orchestrator.get_match_run(
                    user_id=user_id,
                    run_id=started.run_id,
                )
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except CloudClientError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

            if latest_status.status in {
                MatchRunStatus.completed,
                MatchRunStatus.partial,
                MatchRunStatus.failed,
            }:
                break
            sleep(max(0.05, poll_interval_seconds))

        if latest_status is None or latest_status.status not in {
            MatchRunStatus.completed,
            MatchRunStatus.partial,
            MatchRunStatus.failed,
        }:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Timed out waiting for match run completion",
            )

        if latest_status.status == MatchRunStatus.failed:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=latest_status.error or "Match run failed",
            )

        now = datetime.utcnow()
        applications: list[ApplicationRecord] = []
        for match in latest_status.results:
            record = ApplicationRecord(
                id=str(uuid5(NAMESPACE_URL, f"{user_id}:{match.external_job_id}")),
                opportunity=Opportunity(
                    id=match.external_job_id,
                    title=match.title,
                    company=match.company,
                    url=match.apply_url,
                    reason=f"{match.reason} (source={match.source}, score={match.score:.2f})",
                    discovered_at=match.posted_at or now,
                ),
                status=(
                    ApplicationStatus.applying
                    if autosubmit_enabled
                    else ApplicationStatus.review
                ),
            )
            applications.append(
                fastapi_app.state.store.upsert_for_user(user_id, record)
            )

        jobs_to_apply = [
            item
            for item in applications
            if item.status == ApplicationStatus.applying
        ]

        if autosubmit_enabled and jobs_to_apply:
            try:
                fastapi_app.state.orchestrator.start_apply_run(
                    user_id=user_id,
                    payload=ApplyRunStartRequest(
                        jobs=[
                            ApplyTargetJob(
                                external_job_id=item.opportunity.id,
                                title=item.opportunity.title,
                                company=item.opportunity.company,
                                apply_url=item.opportunity.url,
                            )
                            for item in jobs_to_apply
                        ]
                    ),
                )
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
            except CloudClientError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

        logger.info(
            "agent_run_completed_from_match_results",
            extra={
                "user_id": user_id,
                "match_run_id": started.run_id,
                "application_count": len(applications),
                "autosubmit_enabled": autosubmit_enabled,
            },
        )
        return AgentRunResponse(applications=applications)

    @fastapi_app.get("/v1/applications", response_model=AgentRunResponse)
    def list_user_applications(request: Request) -> AgentRunResponse:
        user_id = _authenticated_user_id_from_request(request)
        user = fastapi_app.state.main_store.get_user(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found for token subject",
            )
        applications = fastapi_app.state.store.list_for_user(user_id)
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
            detail = str(exc)
            status_code = (
                status.HTTP_404_NOT_FOUND
                if detail == "User not found"
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code=status_code, detail=detail) from exc

    @fastapi_app.get("/v1/users/{user_id}/resume", response_model=ResumeResponse)
    def get_resume(user_id: str) -> ResumeResponse:
        resume = fastapi_app.state.orchestrator.get_resume(user_id)
        if resume is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resume not found",
            )
        return resume

    @fastapi_app.put(
        "/v1/users/{user_id}/profile",
        response_model=ApplicationProfileResponse,
    )
    def upsert_application_profile(
        user_id: str,
        payload: ApplicationProfileUpsertRequest,
        request: Request,
    ) -> ApplicationProfileResponse:
        _authenticated_user_id_must_match(request, user_id)
        try:
            return fastapi_app.state.orchestrator.upsert_application_profile(
                user_id=user_id,
                payload=payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @fastapi_app.get(
        "/v1/users/{user_id}/profile",
        response_model=ApplicationProfileResponse,
    )
    def get_application_profile(user_id: str, request: Request) -> ApplicationProfileResponse:
        _authenticated_user_id_must_match(request, user_id)
        profile = fastapi_app.state.orchestrator.get_application_profile(user_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
        return profile

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
