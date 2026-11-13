"""OTA of the virtual ESP32: download, SHA-256 check, install — like the real firmware."""

import hashlib

from test_fleet import make_fleet

BLOB = b"\xe9" + b"firmware AgriFog 0.3.0" * 100
GOOD = {"id": "ota-1", "version": "0.3.0", "url": "http://fog/firmware/0.3.0.bin",
        "sha256": hashlib.sha256(BLOB).hexdigest(), "size": len(BLOB)}


def device():
    *_, devices = make_fleet()
    return devices[0]


def test_good_firmware_is_installed_and_announced_in_heartbeat():
    d = device()
    states = [s["state"] for s in d.on_ota(GOOD, download=lambda url: BLOB)]
    assert states == ["downloading", "installed"]
    assert d.heartbeat()["fw_version"] == "0.3.0"


def test_wrong_checksum_is_refused_and_version_kept():
    d = device()
    before = d.fw_version
    out = d.on_ota({**GOOD, "sha256": "0" * 64}, download=lambda url: BLOB)
    assert out[-1]["state"] == "failed" and "SHA-256" in out[-1]["detail"]
    assert d.fw_version == before


def test_truncated_download_is_refused():
    d = device()
    out = d.on_ota(GOOD, download=lambda url: BLOB[:100])
    assert out[-1]["state"] == "failed"


def test_same_version_is_skipped_and_duplicates_ignored():
    d = device()
    d.on_ota(GOOD, download=lambda url: BLOB)
    assert d.on_ota(GOOD, download=lambda url: BLOB) == []                       # même id : doublon QoS 1
    assert d.on_ota({**GOOD, "id": "ota-2"}, download=lambda url: BLOB)[0]["state"] == "skipped"


def test_unreachable_url_fails_cleanly():
    def boom(url):
        raise OSError("connexion refusée")
    out = device().on_ota(GOOD, download=boom)
    assert out[-1]["state"] == "failed" and "téléchargement" in out[-1]["detail"]
