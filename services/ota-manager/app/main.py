"""ota-manager — ota-manager (stub)."""

from fastapi import FastAPI

from agrifog_common import fog_id, load_config

SERVICE = "ota-manager"

app = FastAPI(title=SERVICE)
config = load_config()


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": fog_id(), "status": "ok"}


# TODO (Semaines 4–5) : voir README.md
