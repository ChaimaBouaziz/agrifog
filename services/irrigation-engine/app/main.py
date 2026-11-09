"""irrigation-engine — décide quand et combien arroser (stub)."""

from fastapi import FastAPI

from agrifog_common import fog_id, load_config

SERVICE = "irrigation-engine"

app = FastAPI(title=SERVICE)
config = load_config()


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": fog_id(), "status": "ok"}


# TODO (semaine 3) : ET0 Hargreaves, seuil par culture, ordre d'arrosage, vérif débit
