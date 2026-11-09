"""fed-learning — fed-learning (stub)."""

from fastapi import FastAPI

from agrifog_common import fog_id, load_config

SERVICE = "fed-learning"

app = FastAPI(title=SERVICE)
config = load_config()


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": fog_id(), "status": "ok"}


# TODO (Semaine 6) : voir README.md
