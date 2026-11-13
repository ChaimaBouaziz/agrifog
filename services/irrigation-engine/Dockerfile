# Build from the repo root:
#   docker build -f services/irrigation-engine/Dockerfile -t agrifog/irrigation-engine .
FROM python:3.12-slim
WORKDIR /srv
COPY libs/agrifog_common /tmp/agrifog_common
COPY services/irrigation-engine/requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 /tmp/agrifog_common -r requirements.txt
COPY config /srv/config
COPY services/irrigation-engine/app /srv/app
ENV AGRIFOG_CONFIG_DIR=/srv/config FOG_ID=fog-a
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
