import os

os.environ.setdefault("OTA_MQTT", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import SERVICE, app  # noqa: E402


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == SERVICE


def test_unknown_firmware_is_404():
    assert TestClient(app).get("/firmware/9.9.9.bin").status_code == 404
