# Build from the repo root:
#   docker build -f services/telegram-bot/Dockerfile -t agrifog/telegram-bot .
# Le jeton n'est JAMAIS dans l'image : docker run -e TELEGRAM_TOKEN=... -e TELEGRAM_PIN=...
FROM python:3.12-slim
WORKDIR /srv
COPY libs/agrifog_common /tmp/agrifog_common
COPY services/telegram-bot/requirements.txt .
RUN pip install --no-cache-dir /tmp/agrifog_common -r requirements.txt
COPY config /srv/config
COPY services/telegram-bot/app /srv/app
ENV AGRIFOG_CONFIG_DIR=/srv/config FOG_ID=fog-a
CMD ["python", "-m", "app.main"]
