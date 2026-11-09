# Build from the repo root:
#   docker build -f services/fed-learning/Dockerfile -t agrifog/fed-learning .
FROM python:3.12-slim
WORKDIR /srv
COPY libs/agrifog_common /tmp/agrifog_common
COPY services/fed-learning/requirements.txt .
RUN pip install --no-cache-dir /tmp/agrifog_common -r requirements.txt
COPY config /srv/config
COPY services/fed-learning/app /srv/app
ENV AGRIFOG_CONFIG_DIR=/srv/config FOG_ID=fog-a
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
