from fastapi.testclient import TestClient

from app.main import SERVICE, app


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == SERVICE
