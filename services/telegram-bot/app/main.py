"""Telegram bot of one fog: alerts to the farmer's phone, commands from the phone.

    $env:TELEGRAM_TOKEN="123456:ABC..."   # donné par @BotFather — ne jamais le committer
    $env:TELEGRAM_PIN="2580"              # code à taper une fois : /start 2580
    $env:FOG_ID="fog-a"; python -m app.main

Only chats that sent `/start <PIN>` may use the bot (saved in data/telegram_<fog>.json).
"""

import json
import os
import sys
import threading
import time

import paho.mqtt.client as mqtt

from agrifog_common import fog_id, load_config, topics
from agrifog_common.config import REPO_ROOT

from .logic import FarmView, handle
from .telegram import Outbox, TelegramAPI, poll_updates


class Bot:
    def __init__(self, fog: str, crop: str, api, pin: str, publish, chats_path):
        self.fog, self.crop, self.pin, self.publish = fog, crop, pin, publish
        self.view = FarmView()
        self.outbox = Outbox(api)
        self.chats_path = chats_path
        self.chats: set[int] = set(json.loads(chats_path.read_text())) if chats_path.exists() else set()
        self.lock = threading.Lock()

    # ---- MQTT -> téléphone
    def broadcast(self, text: str | None) -> None:
        if text:
            for chat in sorted(self.chats):
                self.outbox.send(chat, text)

    def on_mqtt(self, topic: str, msg: dict) -> None:
        with self.lock:
            if topic == topics.alert(self.fog):
                self.broadcast(self.view.on_alert(msg))
            elif topic == topics.quota_budget(self.fog):
                self.broadcast(self.view.on_budget(msg))
            elif topic.endswith("/decision"):
                self.view.on_decision(topic.split("/")[2], msg)

    # ---- téléphone -> fog
    def on_message(self, message: dict) -> None:
        chat, text = message["chat"]["id"], message.get("text", "")
        with self.lock:
            if chat not in self.chats:
                parts = text.split()
                if parts[:1] == ["/start"] and len(parts) > 1 and parts[1] == self.pin:
                    self.chats.add(chat)
                    self.chats_path.parent.mkdir(parents=True, exist_ok=True)
                    self.chats_path.write_text(json.dumps(sorted(self.chats)))
                    self.outbox.send(chat, f"✅ Connecté à {self.fog}. Vous recevrez les alertes. Tapez /aide")
                else:
                    self.outbox.send(chat, "🔒 Accès réservé. Tapez /start suivi du code PIN.")
                return
            reply = handle(text, self.fog, self.crop, self.view)
            for topic, payload in reply.publish:
                self.publish(topic, payload)
            self.outbox.send(chat, reply.text)


def main() -> None:
    token, pin = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_PIN")
    if not token or not pin:
        sys.exit("Définir TELEGRAM_TOKEN (donné par @BotFather) et TELEGRAM_PIN (code d'accès).")
    cfg, fog = load_config(), fog_id()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"telegram-bot-{fog}")
    bot = Bot(fog, cfg["crop"]["name"], TelegramAPI(token), pin,
              lambda t, p: client.publish(t, json.dumps(p), qos=1),
              REPO_ROOT / "data" / f"telegram_{fog}.json")
    subs = [topics.alert(fog), topics.quota_budget(fog), topics.decision(fog, "+")]

    def on_connect(c, u, f, rc, p):
        print(f"[mqtt] connecté ({rc})")
        for s in subs:
            c.subscribe(s, qos=1)

    def on_mqtt_message(c, u, m):
        try:
            bot.on_mqtt(m.topic, json.loads(m.payload))
        except (ValueError, KeyError) as e:
            print(f"[mqtt] message ignoré sur {m.topic} : {e}")

    client.on_connect, client.on_message = on_connect, on_mqtt_message
    client.connect(cfg["mqtt"]["host"], cfg["mqtt"]["port"])
    client.loop_start()
    print(f"bot Telegram {fog} prêt ({len(bot.chats)} téléphone(s) déjà autorisé(s)). Ctrl+C pour arrêter.")

    offset = None
    try:
        while True:
            messages, offset = poll_updates(bot.outbox.api, offset)
            for m in messages:
                bot.on_message(m)
            with bot.lock:
                bot.outbox.flush()          # renvoie ce qui attendait (internet revenu)
    except KeyboardInterrupt:
        pass
    client.loop_stop()


if __name__ == "__main__":
    main()
