"""fog-twin — fog-twin (stub)."""

from fastapi import FastAPI

from agrifog_common import fog_id, load_config

SERVICE = "fog-twin"

app = FastAPI(title=SERVICE)
config = load_config()


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": fog_id(), "status": "ok"}


# TODO (Semaine 6) : voir README.md
