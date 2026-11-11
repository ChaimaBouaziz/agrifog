"""Minimal Telegram Bot API client (long polling) — standard library only.

Offline-first: messages that cannot be sent (no internet) wait in a queue and go
out when the connection comes back.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

# TELEGRAM_API permet de pointer vers un faux serveur pour les tests de bout en bout
API = os.environ.get("TELEGRAM_API", "https://api.telegram.org") + "/bot{token}/{method}"
MAX_QUEUE = 200


class TelegramAPI:
    def __init__(self, token: str, timeout: float = 35):
        self.token, self.timeout = token, timeout

    def call(self, method: str, **params) -> dict:
        data = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                       for k, v in params.items()}).encode()
        req = urllib.request.Request(API.format(token=self.token, method=method), data=data)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.load(resp)
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "erreur Telegram"))
        return body["result"]


class Outbox:
    """Send now if possible, otherwise keep for later (oldest dropped beyond MAX_QUEUE)."""

    def __init__(self, api):
        self.api = api
        self.queue: deque[tuple[int, str]] = deque(maxlen=MAX_QUEUE)

    def send(self, chat_id: int, text: str) -> None:
        self.queue.append((chat_id, text))
        self.flush()

    def flush(self) -> int:
        sent = 0
        while self.queue:
            chat_id, text = self.queue[0]
            try:
                self.api.call("sendMessage", chat_id=chat_id, text=text)
            except (urllib.error.URLError, OSError, TimeoutError):
                return sent          # pas d'internet : on réessaiera plus tard
            except RuntimeError as e:
                print(f"[telegram] message refusé ({e}), abandonné")
            self.queue.popleft()
            sent += 1
        return sent


def poll_updates(api, offset: int | None) -> tuple[list[dict], int | None]:
    """One long-polling call. Returns (messages, next offset)."""
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    try:
        updates = api.call("getUpdates", **params)
    except (urllib.error.URLError, OSError, TimeoutError):
        time.sleep(5)
        return [], offset
    msgs = [u["message"] for u in updates if "message" in u and "text" in u["message"]]
    if updates:
        offset = updates[-1]["update_id"] + 1
    return msgs, offset
