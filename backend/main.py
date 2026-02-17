from pathlib import Path
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

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
from .models import AgentRunRequest, AgentRunResponse
from .services import OpportunityAgent, PostgresStore

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
logger = logging.getLogger(__name__)


def create_app(database_url: str | None = None) -> FastAPI:
    configure_logging()
    resolved_database_url = get_database_url(database_url)
    logger.info(
        "app_initializing",
        extra={"database_url": redact_database_url(resolved_database_url)},
    )
    engine = create_db_engine(resolved_database_url)
    session_factory = create_session_factory(engine)

    fastapi_app = FastAPI(title="Agent Apply", version="0.1.0")
    fastapi_app.state.engine = engine
    fastapi_app.state.store = PostgresStore(session_factory=session_factory)
    fastapi_app.state.agent = OpportunityAgent(store=fastapi_app.state.store)

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

    @fastapi_app.on_event("shutdown")
    def dispose_db_engine() -> None:
        logger.info("db_engine_disposal_started")
        fastapi_app.state.engine.dispose()
        logger.info("db_engine_disposal_completed")

    logger.info("app_initialized")

    return fastapi_app


app = create_app()
