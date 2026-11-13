"""Firmware catalogue of the fog: every release is downloaded ONCE, its SHA-256 checked, then
served locally to the ESP32s (the fog is the local mirror: 10 ESP32s do not each hit Internet).

A release comes from the GitHub Release published by the CI (tag fw-vX.Y.Z, assets
firmware.bin + firmware.bin.sha256) or from any URL + expected SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

GITHUB_REPO = "ChaimaBouaziz/agrifog"

Download = Callable[[str], bytes]


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "agrifog-ota-manager"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


@dataclass
class Release:
    version: str
    sha256: str
    size: int
    url: str            # source publique (GitHub Release) : utilisée par l'ESP32 de Wokwi
    source: str = ""


class ChecksumError(ValueError):
    pass


class Catalog:
    def __init__(self, root: Path, download: Download = http_get):
        self.root, self.download = Path(root), download
        (self.root / "firmware").mkdir(parents=True, exist_ok=True)
        self.index = self.root / "catalog.json"
        self._releases: dict[str, Release] = {}
        if self.index.exists():
            for r in json.loads(self.index.read_text(encoding="utf-8")):
                self._releases[r["version"]] = Release(**r)

    # ------------------------------------------------------------------ queries
    def releases(self) -> list[Release]:
        return sorted(self._releases.values(), key=lambda r: [int(x) if x.isdigit() else x
                                                              for x in r.version.split(".")])

    def get(self, version: str) -> Release:
        if version not in self._releases:
            raise KeyError(f"version {version} absente du catalogue")
        return self._releases[version]

    def path(self, version: str) -> Path:
        return self.root / "firmware" / f"{version}.bin"

    # ------------------------------------------------------------------ adding
    def add(self, version: str, url: str, sha256: str, source: str = "") -> Release:
        """Download, check the SHA-256 (refuse on mismatch), keep a local copy."""
        blob = self.download(url)
        digest = hashlib.sha256(blob).hexdigest()
        if digest != sha256.strip().lower():
            raise ChecksumError(f"SHA-256 de {url} = {digest[:12]}…, attendu {sha256[:12]}… : refusé")
        self.path(version).write_bytes(blob)
        rel = Release(version, digest, len(blob), url, source or url)
        self._releases[version] = rel
        self._save()
        return rel

    def add_github(self, tag: str, repo: str = GITHUB_REPO) -> Release:
        """Release fw-vX.Y.Z published by the CI: firmware.bin + firmware.bin.sha256."""
        meta = json.loads(self.download(f"https://api.github.com/repos/{repo}/releases/tags/{tag}"))
        assets = {a["name"]: a["browser_download_url"] for a in meta.get("assets", [])}
        if "firmware.bin" not in assets or "firmware.bin.sha256" not in assets:
            raise ValueError(f"la release {tag} n'a pas firmware.bin + firmware.bin.sha256")
        sha = self.download(assets["firmware.bin.sha256"]).decode().split()[0]
        version = tag.removeprefix("fw-v").removeprefix("v")
        return self.add(version, assets["firmware.bin"], sha, source=f"github:{repo}@{tag}")

    def _save(self) -> None:
        self.index.write_text(json.dumps([asdict(r) for r in self.releases()], indent=2), encoding="utf-8")
