"""Two negotiators wired to each other through a fake bus (= the MQTT bridge)."""

from agrifog_common import load_config

from app.negotiator import QuotaNegotiator


class Bus:
    def __init__(self):
        self.nodes, self.log, self.connected = {}, [], True

    def publisher(self, me):
        def publish(topic, payload, retain=False):
            self.log.append((topic, payload))
            if not self.connected or not topic.startswith("domaine/"):
                return
            other = self.nodes["fog-b" if me == "fog-a" else "fog-a"]
            if topic == f"domaine/quota/request/{me}":
                other.on_neighbour_request(payload)
            elif topic == f"domaine/quota/release/{me}":
                other.on_neighbour_release(payload)
        return publish


def make_pair(connected=True):
    bus = Bus()
    bus.connected = connected
    for fog in ("fog-a", "fog-b"):
        bus.nodes[fog] = QuotaNegotiator(load_config(fog), fog, publish=bus.publisher(fog))
    return bus, bus.nodes["fog-a"], bus.nodes["fog-b"]


def feed(neg, hhmm, moisture, day=1):
    for z, dev in (("z1", "01"), ("z2", "04"), ("z3", "07")):
        prefix = "esp-a-" if neg.fog == "fog-a" else "esp-b-"
        neg.on_telemetry(z, {"ts": f"2025-07-{day:02d}T{hhmm}:00Z", "device": prefix + dev,
                             "soil_moisture": moisture})


def budgets(bus, fog):
    return [p for t, p in bus.log if t == f"{fog}/quota/budget"]


def test_both_fogs_agree_on_the_same_split():
    bus, a, b = make_pair()
    a.on_decision({"et0": 6.5}); b.on_decision({"et0": 6.5})
    feed(a, "03:00", 21.0)          # tomate sèche
    feed(b, "03:00", 17.5)          # olivier bien arrosé : besoin < sa moitié
    alloc_a = [p for t, p in bus.log if t == "domaine/quota/allocation/fog-a"][0]["allocation"]
    alloc_b = [p for t, p in bus.log if t == "domaine/quota/allocation/fog-b"][0]["allocation"]
    assert alloc_a == alloc_b
    assert sum(alloc_a.values()) == 6000
    assert alloc_a["fog-a"] > alloc_a["fog-b"]           # la tomate récupère ce dont l'olivier n'a pas besoin
    assert budgets(bus, "fog-a")[-1]["mode"] == "négocié"


def test_silent_neighbour_means_isolated_half():
    bus, a, b = make_pair(connected=False)
    feed(a, "03:00", 21.0)
    assert not budgets(bus, "fog-a")                     # on attend le voisin…
    feed(a, "04:05", 21.0)                               # …1 h, puis mode isolé
    assert budgets(bus, "fog-a")[-1]["allocated_l"] == 3000
    assert "isolé" in budgets(bus, "fog-a")[-1]["mode"]


def test_spare_is_given_to_the_neighbour_at_10h():
    bus, a, b = make_pair()
    a.on_decision({"et0": 6.5}); b.on_decision({"et0": 6.5})
    feed(a, "03:00", 21.0); feed(b, "03:00", 10.0)       # journée de pénurie : chacun reçoit moins que son besoin
    before_b = budgets(bus, "fog-b")[-1]["allocated_l"]
    feed(a, "10:00", 29.0)                               # arrosée ce matin : plus besoin de rien ce soir
    released = [p for t, p in bus.log if t == "domaine/quota/release/fog-a"]
    need_b = [p for t, p in bus.log if t == "domaine/quota/request/fog-b"][0]["need_l"]
    assert released and 0 < released[0]["liters"] <= need_b - before_b   # seulement ce qui manque au voisin
    assert budgets(bus, "fog-b")[-1]["allocated_l"] == before_b + released[0]["liters"]


def test_usage_is_tracked_per_day():
    bus, a, _ = make_pair()
    a.on_usage({"day": "2025-07-01", "used_l": 1200})
    assert a.snapshot()["2025-07-01"]["used_l"] == 1200


def test_request_is_retained_for_a_late_neighbour():
    bus, a, _ = make_pair()
    feed(a, "03:00", 21.0)
    assert ("domaine/quota/request/fog-a" in [t for t, _ in bus.log])
    # le Bus de test ne simule pas le "retained" : on vérifie le drapeau envoyé au broker
    sent = []
    a.publish = lambda topic, payload, retain=False: sent.append((topic, retain))
    feed(a, "03:00", 21.0, day=2)
    assert ("domaine/quota/request/fog-a", True) in sent


def test_no_cession_back_to_a_neighbour_who_just_gave():
    bus, a, b = make_pair()
    a.on_decision({"et0": 6.5}); b.on_decision({"et0": 6.5})
    feed(a, "03:00", 21.0); feed(b, "03:00", 10.0)
    feed(b, "10:00", 18.0)                               # l'olivier a été arrosé : il cède à la tomate qui manque
    assert [p for t, p in bus.log if t == "domaine/quota/release/fog-b"]
    feed(a, "10:00", 29.0)                               # la tomate a maintenant trop… mais ne rend rien
    assert not [p for t, p in bus.log if t == "domaine/quota/release/fog-a"]


def test_nobody_gives_when_both_have_enough():
    bus, a, b = make_pair()
    a.on_decision({"et0": 6.5}); b.on_decision({"et0": 6.5})
    feed(a, "03:00", 27.0); feed(b, "03:00", 17.5)       # besoins faibles : chacun a assez avec sa part
    feed(a, "10:00", 29.0); feed(b, "10:00", 18.0)
    assert not [p for t, p in bus.log if t.startswith("domaine/quota/release/")]   # pas de cessions croisées
