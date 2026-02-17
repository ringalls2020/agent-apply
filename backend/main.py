from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .db import Base, create_db_engine, create_session_factory, get_database_url
from .models import AgentRunRequest, AgentRunResponse
from .services import OpportunityAgent, PostgresStore

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)


def create_app(database_url: str | None = None) -> FastAPI:
    resolved_database_url = get_database_url(database_url)
    engine = create_db_engine(resolved_database_url)
    session_factory = create_session_factory(engine)

    fastapi_app = FastAPI(title="Agent Apply", version="0.1.0")
    fastapi_app.state.engine = engine
    fastapi_app.state.store = PostgresStore(session_factory=session_factory)
    fastapi_app.state.agent = OpportunityAgent(store=fastapi_app.state.store)

    @fastapi_app.on_event("startup")
    def init_db_schema() -> None:
        Base.metadata.create_all(bind=fastapi_app.state.engine)

    @fastapi_app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @fastapi_app.post("/agent/run", response_model=AgentRunResponse)
    def run_agent(payload: AgentRunRequest) -> AgentRunResponse:
        applications = fastapi_app.state.agent.run(payload)
        return AgentRunResponse(applications=applications)

    @fastapi_app.get("/applications", response_model=AgentRunResponse)
    def list_applications() -> AgentRunResponse:
        return AgentRunResponse(applications=fastapi_app.state.store.list_all())

    @fastapi_app.get("/admin", response_class=HTMLResponse)
    def admin_dashboard(request: Request) -> HTMLResponse:
        apps = fastapi_app.state.store.list_all()
        stats = {
            "total": len(apps),
            "applied": len([a for a in apps if a.submitted_at]),
            "notified": len([a for a in apps if a.notified_at]),
        }
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"applications": apps, "stats": stats},
        )

    @fastapi_app.on_event("shutdown")
    def dispose_db_engine() -> None:
        fastapi_app.state.engine.dispose()

    return fastapi_app


app = create_app()
