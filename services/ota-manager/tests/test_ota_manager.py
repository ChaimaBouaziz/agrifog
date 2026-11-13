import hashlib
import json

import pytest

from agrifog_common import load_config

from app.catalog import Catalog, ChecksumError
from app.manager import OtaManager

BLOB = b"\xe9AgriFog firmware 0.3.0" * 50
SHA = hashlib.sha256(BLOB).hexdigest()


def fake_web(extra=None):
    files = {"https://github.com/x/firmware.bin": BLOB,
             "https://github.com/x/firmware.bin.sha256": f"{SHA}  firmware.bin\n".encode(),
             "https://api.github.com/repos/ChaimaBouaziz/agrifog/releases/tags/fw-v0.3.0": json.dumps(
                 {"assets": [{"name": "firmware.bin", "browser_download_url": "https://github.com/x/firmware.bin"},
                             {"name": "firmware.bin.sha256",
                              "browser_download_url": "https://github.com/x/firmware.bin.sha256"}]}).encode()}
    files.update(extra or {})
    return lambda url: files[url]


def test_release_from_github_is_checked_and_mirrored(tmp_path):
    cat = Catalog(tmp_path, download=fake_web())
    rel = cat.add_github("fw-v0.3.0")
    assert rel.version == "0.3.0" and rel.sha256 == SHA and rel.size == len(BLOB)
    assert cat.path("0.3.0").read_bytes() == BLOB
    assert Catalog(tmp_path, download=fake_web()).get("0.3.0").sha256 == SHA      # catalogue persistant


def test_corrupted_download_is_refused(tmp_path):
    cat = Catalog(tmp_path, download=fake_web({"https://github.com/x/firmware.bin": BLOB[:-1] + b"X"}))
    with pytest.raises(ChecksumError):
        cat.add_github("fw-v0.3.0")
    assert cat.releases() == []


def make_manager(tmp_path, fog="fog-b"):
    cat = Catalog(tmp_path, download=fake_web())
    cat.add_github("fw-v0.3.0")
    sent = []
    m = OtaManager(load_config(fog), fog, cat, "http://127.0.0.1:8003", publish=lambda t, p: sent.append((t, p)),
                   clock=lambda: 1000.0)
    return m, sent


def test_simulated_esp32_downloads_from_the_fog_wokwi_from_github(tmp_path):
    m, sent = make_manager(tmp_path)
    m.deploy("0.3.0", ["esp-b-01", "esp-b-wokwi"])
    urls = {t.split("/")[2]: p["url"] for t, p in sent}
    assert urls["esp-b-01"] == "http://127.0.0.1:8003/firmware/0.3.0.bin"       # miroir local du fog
    assert urls["esp-b-wokwi"] == "https://github.com/x/firmware.bin"           # Wokwi ne voit pas le fog
    assert all(p["sha256"] == SHA and p["size"] == len(BLOB) for _, p in sent)


def test_deploy_all_and_unknown_devices(tmp_path):
    m, sent = make_manager(tmp_path)
    m.deploy("0.3.0", "all")
    assert len(sent) == len(m.known) and "fog-b/device/esp-b-01/ota" in [t for t, _ in sent]
    with pytest.raises(ValueError):
        m.deploy("0.3.0", ["esp-a-01"])          # un ESP32 de l'autre fog
    with pytest.raises(KeyError):
        m.deploy("9.9.9", ["esp-b-01"])          # version absente du catalogue


def test_status_and_heartbeat_tracking(tmp_path):
    m, _ = make_manager(tmp_path)
    m.on_heartbeat("esp-b-01", {"fw_version": "0.1.0"})
    m.deploy("0.3.0", ["esp-b-01"])
    assert m.snapshot()["devices"]["esp-b-01"]["ota"]["state"] == "sent"
    m.on_status("esp-b-01", {"state": "installed", "version": "0.3.0", "from": "0.1.0"})
    snap = m.snapshot()
    assert snap["devices"]["esp-b-01"]["version"] == "0.3.0"
    assert snap["versions"] == {"0.3.0": 1}
