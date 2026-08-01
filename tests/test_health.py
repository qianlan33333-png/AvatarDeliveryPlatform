from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_reports_service_and_database() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["service"] == "avatar-delivery-platform"
    assert body["database"] == "ok"
    assert body["redis"].startswith(("ok", "optional:"))


def test_root_redirects_to_admin() -> None:
    response = TestClient(app).get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/admin"

