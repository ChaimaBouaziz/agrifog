# Build depuis la racine du dépôt (fait automatiquement par docker compose)
FROM python:3.12-slim
WORKDIR /srv
COPY libs/agrifog_common /tmp/agrifog_common
COPY simulator/requirements.txt .
RUN pip install --no-cache-dir /tmp/agrifog_common -r requirements.txt
COPY config /srv/config
COPY simulator/simulator /srv/simulator
ENV AGRIFOG_CONFIG_DIR=/srv/config PYTHONUNBUFFERED=1
CMD ["python", "-m", "simulator.main"]
