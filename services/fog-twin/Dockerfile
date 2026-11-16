# Build from the repo root:
#   docker build -f services/fog-twin/Dockerfile -t agrifog/fog-twin .
FROM python:3.12-slim
WORKDIR /srv
COPY libs/agrifog_common /tmp/agrifog_common
COPY services/fog-twin/requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 /tmp/agrifog_common -r requirements.txt
COPY config /srv/config
# le moteur d'irrigation, pour reprendre la parcelle du voisin avec exactement le même code
COPY services/irrigation-engine/app /srv/irrigation_engine
COPY services/fog-twin/app /srv/app
ENV AGRIFOG_CONFIG_DIR=/srv/config FOG_ID=fog-a TWIN_DATA_DIR=/srv/data/twin
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
