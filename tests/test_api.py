from fastapi.testclient import TestClient

from backend.main import create_app


def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_health_endpoint_returns_ok() -> None:
    response = client().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_agent_run_and_list_applications_endpoints() -> None:
    test_client = client()
    payload = {
        "profile": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "resume_text": "ML engineer with interest in climate and robotics.",
            "interests": ["ai", "climate"],
        },
        "max_opportunities": 3,
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


def test_agent_run_rejects_empty_interests() -> None:
    test_client = client()
    payload = {
        "profile": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "resume_text": "Any resume text",
            "interests": [],
        },
        "max_opportunities": 1,
    }

    response = test_client.post("/agent/run", json=payload)

    assert response.status_code == 422


def test_admin_dashboard_renders_html_and_stats() -> None:
    test_client = client()

    payload = {
        "profile": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "resume_text": "Any resume text",
            "interests": ["ai"],
        },
        "max_opportunities": 2,
    }
    test_client.post("/agent/run", json=payload)

    response = test_client.get("/admin")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Agent Apply Admin Dashboard" in response.text
    assert "Total Opportunities" in response.text
    assert "Applied" in response.text
    assert "Notified" in response.text
