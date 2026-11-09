# Build from the repo root:
#   docker build -f services/pump-scheduler/Dockerfile -t agrifog/pump-scheduler .
FROM python:3.12-slim
WORKDIR /srv
COPY libs/agrifog_common /tmp/agrifog_common
COPY services/pump-scheduler/requirements.txt .
RUN pip install --no-cache-dir /tmp/agrifog_common -r requirements.txt
COPY config /srv/config
COPY services/pump-scheduler/app /srv/app
ENV AGRIFOG_CONFIG_DIR=/srv/config FOG_ID=fog-a
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
