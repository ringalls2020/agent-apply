from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .models import AgentRunRequest, AgentRunResponse, UpdateApplicationStatusRequest
from .services import OpportunityAgent
from .store import InMemoryStore, JsonFileStore

templates = Jinja2Templates(directory="app/templates")


def build_store() -> InMemoryStore:
    persistence_file = os.getenv("AGENT_APPLY_STORE_FILE")
    if persistence_file:
        return JsonFileStore(Path(persistence_file))
    return InMemoryStore()


def create_app() -> FastAPI:
    fastapi_app = FastAPI(
        title="Agent Apply",
        version="0.2.0",
        description=(
            "Agentic application workflow: discover opportunities, apply, enrich contacts, "
            "and notify candidate with admin dashboard controls."
        ),
    )
    fastapi_app.state.store = build_store()
    fastapi_app.state.agent = OpportunityAgent(store=fastapi_app.state.store)

    @fastapi_app.get("/health")
    def health() -> dict:
        return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

    @fastapi_app.post("/agent/run", response_model=AgentRunResponse)
    def run_agent(payload: AgentRunRequest) -> AgentRunResponse:
        applications = fastapi_app.state.agent.run(payload)
        return AgentRunResponse(applications=applications)

    @fastapi_app.get("/applications", response_model=AgentRunResponse)
    def list_applications() -> AgentRunResponse:
        return AgentRunResponse(applications=fastapi_app.state.store.list_all())

    @fastapi_app.patch("/applications/{application_id}", response_model=AgentRunResponse)
    def update_application_status(
        application_id: str, payload: UpdateApplicationStatusRequest
    ) -> AgentRunResponse:
        record = fastapi_app.state.store.get(application_id)
        if not record:
            raise HTTPException(status_code=404, detail="Application not found")

        record.status = payload.status
        if payload.notes:
            record.notes = payload.notes
        record.updated_at = datetime.utcnow()

        if payload.status.value == "notified" and not record.notified_at:
            record.notified_at = datetime.utcnow()

        fastapi_app.state.store.upsert(record)
        return AgentRunResponse(applications=[record])

    @fastapi_app.delete("/applications/{application_id}")
    def delete_application(application_id: str) -> dict:
        deleted = fastapi_app.state.store.delete(application_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Application not found")
        return {"deleted": True, "application_id": application_id}

    @fastapi_app.get("/admin", response_class=HTMLResponse)
    def admin_dashboard(request: Request) -> HTMLResponse:
        apps = fastapi_app.state.store.list_all()
        stats = {
            "total": len(apps),
            "applied": len([a for a in apps if a.submitted_at]),
            "contacted": len([a for a in apps if a.contact]),
            "notified": len([a for a in apps if a.notified_at]),
        }
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"applications": apps, "stats": stats},
        )

    return fastapi_app


app = create_app()
