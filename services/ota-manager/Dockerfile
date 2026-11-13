# Build from the repo root:
#   docker build -f services/ota-manager/Dockerfile -t agrifog/ota-manager .
FROM python:3.12-slim
WORKDIR /srv
COPY libs/agrifog_common /tmp/agrifog_common
COPY services/ota-manager/requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 /tmp/agrifog_common -r requirements.txt
COPY config /srv/config
COPY services/ota-manager/app /srv/app
ENV AGRIFOG_CONFIG_DIR=/srv/config FOG_ID=fog-a OTA_DATA_DIR=/srv/data/ota
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
