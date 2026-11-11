import urllib.error

from app.logic import FarmView, handle
from app.main import Bot
from app.telegram import Outbox, poll_updates


class FakeAPI:
    def __init__(self, online=True):
        self.online, self.sent, self.updates = online, [], []

    def call(self, method, **params):
        if not self.online:
            raise urllib.error.URLError("pas d'internet")
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            return {}
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return ups
        raise AssertionError(method)


def make_bot(tmp_path, online=True):
    published = []
    api = FakeAPI(online)
    bot = Bot("fog-a", "tomate", api, "2580", lambda t, p: published.append((t, p)), tmp_path / "chats.json")
    return bot, api, published


def msg(text, chat=42):
    return {"chat": {"id": chat}, "text": text}


def test_unknown_phone_must_give_the_pin(tmp_path):
    bot, api, published = make_bot(tmp_path)
    bot.on_message(msg("/fuite z2"))
    assert "Accès réservé" in api.sent[-1][1] and not published
    bot.on_message(msg("/start 0000"))
    assert 42 not in bot.chats
    bot.on_message(msg("/start 2580"))
    assert 42 in bot.chats and (tmp_path / "chats.json").exists()


def test_alerts_are_pushed_and_resolved(tmp_path):
    bot, api, _ = make_bot(tmp_path)
    bot.on_message(msg("/start 2580"))
    bot.on_mqtt("fog-a/alert", {"type": "leak", "zone": "z2", "msg": "FUITE sur z2 : 6.0 L/min", "resolved": False})
    assert "FUITE" in api.sent[-1][1]
    bot.on_message(msg("/alertes"))
    assert "z2" in api.sent[-1][1]
    bot.on_mqtt("fog-a/alert", {"type": "leak", "zone": "z2", "msg": "…", "resolved": True})
    assert api.sent[-1][1].startswith("✅ Résolu")
    assert not bot.view.alerts


def test_fault_and_watering_commands_publish_mqtt(tmp_path):
    bot, api, published = make_bot(tmp_path)
    bot.on_message(msg("/start 2580"))
    bot.on_message(msg("/fuite z2 4"))
    bot.on_message(msg("/arroser z1 300"))
    bot.on_message(msg("/reparer"))
    assert published[0] == ("fog-a/sim/fault", {"type": "leak", "zone": "z2", "lpm": 4.0})
    assert published[1][0] == "fog-a/zone/z1/cmd" and published[1][1]["seconds"] == 300
    assert published[2] == ("fog-a/sim/fault", {"type": "clear"})


def test_incomplete_command_gives_an_example():
    r = handle("/capteur", "fog-a", "tomate", FarmView())
    assert "Exemple" in r.text and not r.publish


def test_status_lists_zones_and_quota():
    v = FarmView()
    v.on_decision("z1", {"action": "wait", "moisture": 24.1, "reason": "pas besoin"})
    v.on_budget({"day": "2025-07-01", "allocated_l": 2413, "mode": "négocié"})
    text = handle("/etat", "fog-a", "tomate", v).text
    assert "z1 : 24.1 %" in text and "2413 L" in text


def test_daily_quota_is_announced_once():
    v = FarmView()
    assert "Quota" in v.on_budget({"day": "2025-07-01", "allocated_l": 3000, "mode": "isolé"})
    assert v.on_budget({"day": "2025-07-01", "allocated_l": 3000, "mode": "isolé"}) is None
    assert "cède" in v.on_budget({"day": "2025-07-01", "allocated_l": 3500, "mode": "négocié", "received_l": 500})


def test_messages_wait_when_internet_is_down():
    api = FakeAPI(online=False)
    box = Outbox(api)
    box.send(42, "alerte 1")
    box.send(42, "alerte 2")
    assert len(box.queue) == 2 and not api.sent
    api.online = True
    assert box.flush() == 2 and [t for _, t in api.sent] == ["alerte 1", "alerte 2"]


def test_polling_survives_no_internet(monkeypatch):
    monkeypatch.setattr("app.telegram.time.sleep", lambda s: None)
    msgs, offset = poll_updates(FakeAPI(online=False), 7)
    assert msgs == [] and offset == 7
    api = FakeAPI()
    api.updates = [{"update_id": 10, "message": {"chat": {"id": 1}, "text": "/etat"}}]
    msgs, offset = poll_updates(api, None)
    assert msgs[0]["text"] == "/etat" and offset == 11
