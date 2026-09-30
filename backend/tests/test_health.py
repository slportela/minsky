from fastapi.testclient import TestClient

from minsky_api.main import create_app


def test_health():
    response = TestClient(create_app()).get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
