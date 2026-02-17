from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture
def test_client() -> Iterator[TestClient]:
    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app) as client:
        yield client


def _auth_headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _signup_user(
    client: TestClient,
    *,
    full_name: str,
    email: str,
    password: str = "strong-password",
) -> dict:
    response = client.post(
        "/v1/auth/signup",
        json={
            "full_name": full_name,
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 201
    return response.json()


def _seed_profile(client: TestClient, *, user_id: str, applications_per_day: int = 3) -> None:
    preferences = {
        "interests": ["ai", "climate"],
        "locations": ["United States"],
        "applications_per_day": applications_per_day,
    }
    resume = {
        "filename": "resume.txt",
        "resume_text": "ML engineer with automation and platform experience.",
    }

    pref_response = client.put(f"/v1/users/{user_id}/preferences", json=preferences)
    resume_response = client.put(f"/v1/users/{user_id}/resume", json=resume)

    assert pref_response.status_code == 200
    assert resume_response.status_code == 200


def test_health_endpoint_returns_ok(test_client: TestClient) -> None:
    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_returns_generated_request_id_header(
    test_client: TestClient,
) -> None:
    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")


def test_health_endpoint_reuses_incoming_request_id_header(
    test_client: TestClient,
) -> None:
    response = test_client.get("/health", headers={"x-request-id": "req-123"})

    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "req-123"


def test_signup_login_and_me_flow(test_client: TestClient) -> None:
    signup_body = _signup_user(
        test_client,
        full_name="Jane Doe",
        email="jane@example.com",
    )

    signup_token = signup_body["token"]
    me_response = test_client.get("/v1/auth/me", headers=_auth_headers(signup_token))
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "jane@example.com"

    login_response = test_client.post(
        "/v1/auth/login",
        json={"email": "jane@example.com", "password": "strong-password"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["user"]["id"] == signup_body["user"]["id"]


def test_login_rejects_invalid_credentials(test_client: TestClient) -> None:
    _signup_user(test_client, full_name="Jane Doe", email="jane@example.com")

    login_response = test_client.post(
        "/v1/auth/login",
        json={"email": "jane@example.com", "password": "wrong-password"},
    )
    assert login_response.status_code == 401
    assert login_response.json()["detail"] == "Invalid credentials."


def test_agent_run_requires_resume(test_client: TestClient) -> None:
    signup_body = _signup_user(
        test_client,
        full_name="Jane Doe",
        email="jane@example.com",
    )

    response = test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(signup_body["token"]),
    )
    assert response.status_code == 400
    assert "User resume not found" in response.json()["detail"]


def test_agent_run_and_list_applications_are_user_scoped(test_client: TestClient) -> None:
    user_a = _signup_user(test_client, full_name="Jane A", email="a@example.com")
    user_b = _signup_user(test_client, full_name="Jane B", email="b@example.com")

    _seed_profile(test_client, user_id=user_a["user"]["id"], applications_per_day=2)

    run_response = test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(user_a["token"]),
    )
    assert run_response.status_code == 200
    assert len(run_response.json()["applications"]) == 2

    list_a = test_client.get("/v1/applications", headers=_auth_headers(user_a["token"]))
    list_b = test_client.get("/v1/applications", headers=_auth_headers(user_b["token"]))

    assert list_a.status_code == 200
    assert list_b.status_code == 200
    assert len(list_a.json()["applications"]) == 2
    assert len(list_b.json()["applications"]) == 0


def test_legacy_application_endpoints_return_gone(test_client: TestClient) -> None:
    response_run = test_client.post(
        "/agent/run",
        json={
            "profile": {
                "full_name": "Jane Doe",
                "email": "jane@example.com",
                "resume_text": "any",
                "interests": ["ai"],
            },
            "max_opportunities": 1,
        },
    )
    response_list = test_client.get("/applications")

    assert response_run.status_code == 410
    assert response_list.status_code == 410


def test_admin_dashboard_renders_html_and_stats(test_client: TestClient) -> None:
    user = _signup_user(test_client, full_name="Jane Doe", email="jane@example.com")
    _seed_profile(test_client, user_id=user["user"]["id"], applications_per_day=2)

    test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(user["token"]),
    )

    response = test_client.get("/admin")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Agent Apply Admin Dashboard" in response.text
    assert "Total Opportunities" in response.text
    assert "Applied" in response.text
    assert "Notified" in response.text
