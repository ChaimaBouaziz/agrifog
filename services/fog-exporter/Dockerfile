# Build from the repo root:
#   docker build -f services/fog-exporter/Dockerfile -t agrifog/fog-exporter .
FROM python:3.12-slim
WORKDIR /srv
COPY libs/agrifog_common /tmp/agrifog_common
COPY services/fog-exporter/requirements.txt .
# L'exporter n'utilise ni numpy ni l'IA : on installe la lib commune sans ses dépendances
# (numpy = 17 Mo) et seulement ce qu'il faut. Délai long et reprises : connexions lentes.
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 --no-deps /tmp/agrifog_common \
 && pip install --no-cache-dir --default-timeout=120 --retries 10 "pyyaml>=6" "paho-mqtt>=2" -r requirements.txt
COPY config /srv/config
COPY services/fog-exporter/app /srv/app
ENV AGRIFOG_CONFIG_DIR=/srv/config FOG_ID=fog-a PYTHONUNBUFFERED=1
EXPOSE 9108
CMD ["python", "-m", "app.main"]
