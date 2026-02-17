from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_health_endpoint_returns_ok() -> None:
    response = client().get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "timestamp" in response.json()


def test_agent_run_and_list_applications_endpoints() -> None:
    test_client = client()
    payload = {
        "profile": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "resume_text": "ML engineer with interest in climate and robotics. " * 2,
            "interests": ["ai", "climate"],
            "locations": ["Remote"],
        },
        "max_opportunities": 3,
        "auto_apply": True,
    }

    run_response = test_client.post("/agent/run", json=payload)
    list_response = test_client.get("/applications")

    assert run_response.status_code == 200
    assert list_response.status_code == 200

    run_body = run_response.json()
    list_body = list_response.json()

    assert len(run_body["applications"]) == 3
    assert len(list_body["applications"]) == 3
    assert all(item["status"] == "notified" for item in run_body["applications"])


def test_update_and_delete_application() -> None:
    test_client = client()
    payload = {
        "profile": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "resume_text": "ML engineer with interest in climate and robotics. " * 2,
            "interests": ["ai"],
            "locations": ["Remote"],
        },
        "max_opportunities": 1,
        "auto_apply": True,
    }

    run_response = test_client.post("/agent/run", json=payload)
    app_id = run_response.json()["applications"][0]["id"]

    update_response = test_client.patch(
        f"/applications/{app_id}", json={"status": "archived", "notes": "duplicate posting"}
    )
    delete_response = test_client.delete(f"/applications/{app_id}")

    assert update_response.status_code == 200
    assert update_response.json()["applications"][0]["status"] == "archived"
    assert update_response.json()["applications"][0]["notes"] == "duplicate posting"

    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_agent_run_rejects_empty_interests() -> None:
    test_client = client()
    payload = {
        "profile": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "resume_text": "Any resume text that is sufficiently long for validation to pass.",
            "interests": [],
            "locations": ["Remote"],
        },
        "max_opportunities": 1,
        "auto_apply": True,
    }

    response = test_client.post("/agent/run", json=payload)

    assert response.status_code == 422


def test_admin_dashboard_renders_html_and_stats() -> None:
    test_client = client()

    payload = {
        "profile": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "resume_text": "Any resume text that is sufficiently long for validation to pass.",
            "interests": ["ai"],
            "locations": ["Remote"],
        },
        "max_opportunities": 2,
        "auto_apply": True,
    }
    test_client.post("/agent/run", json=payload)

    response = test_client.get("/admin")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Agent Apply Admin Dashboard" in response.text
    assert "Contacted" in response.text


def test_json_file_store_persists_records_when_env_is_set(tmp_path: Path, monkeypatch) -> None:
    db_file = tmp_path / "apps.json"
    monkeypatch.setenv("AGENT_APPLY_STORE_FILE", str(db_file))

    app = create_app()
    test_client = TestClient(app)

    payload = {
        "profile": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "resume_text": "ML engineer with interest in climate and robotics. " * 2,
            "interests": ["ai"],
            "locations": ["Remote"],
        },
        "max_opportunities": 1,
        "auto_apply": True,
    }

    response = test_client.post("/agent/run", json=payload)
    assert response.status_code == 200
    assert db_file.exists() is True
