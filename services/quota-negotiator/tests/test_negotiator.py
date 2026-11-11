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
    feed(b, "03:00", 15.0)          # olivier confortable
    alloc_a = [p for t, p in bus.log if t == "domaine/quota/allocation/fog-a"][0]["allocation"]
    alloc_b = [p for t, p in bus.log if t == "domaine/quota/allocation/fog-b"][0]["allocation"]
    assert alloc_a == alloc_b
    assert sum(alloc_a.values()) == 6000
    assert alloc_a["fog-a"] > alloc_a["fog-b"]           # la tomate stressée passe devant
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
    a.on_decision({"et0": 3.0}); b.on_decision({"et0": 6.5})
    feed(a, "03:00", 29.5); feed(b, "03:00", 9.0)
    before_b = budgets(bus, "fog-b")[-1]["allocated_l"]
    a.on_forecast({"rain_next_24h_mm": 0})
    feed(a, "10:00", 29.5)                               # tomate encore bien humide : surplus
    released = [p for t, p in bus.log if t == "domaine/quota/release/fog-a"]
    assert released and released[0]["liters"] > 0
    assert budgets(bus, "fog-b")[-1]["allocated_l"] == before_b + released[0]["liters"]


def test_usage_is_tracked_per_day():
    bus, a, _ = make_pair()
    a.on_usage({"day": "2025-07-01", "used_l": 1200})
    assert a.snapshot()["2025-07-01"]["used_l"] == 1200
