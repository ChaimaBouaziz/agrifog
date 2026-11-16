"""Deux pump-scheduler reliés par un faux pont MQTT (avec latence) et une horloge simulée."""

from datetime import datetime, timedelta, timezone

import pytest

from app.scheduler import CLAIM_TTL_S, PumpScheduler

SPEED = 360


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Bridge:
    """Les messages domaine/pump/claim/* arrivent chez l'autre fog après `latency` secondes."""

    def __init__(self, clock, latency=0.2, valve_delay=3.0, valves_alive=True):
        self.clock, self.latency, self.inflight, self.cmds, self.nodes = clock, latency, [], [], {}
        self.valve_delay, self.valves_alive, self.acks = valve_delay, valves_alive, []
        self.up = True

    def node(self, fog):
        s = PumpScheduler(fog, SPEED, clock=self.clock)
        s.publish = lambda topic, payload, retain, src=fog: self._publish(src, topic, payload)
        self.nodes[fog] = s
        return s

    def _publish(self, src, topic, payload):
        if topic.endswith("/cmd"):
            self.cmds.append((self.clock(), src, topic, payload))
            if self.valves_alive:      # la vanne se réveille, ouvre, et confirme sur le broker où elle est
                self.acks.append((self.clock() + self.valve_delay, src, payload))
        elif topic.startswith("domaine/pump/claim/") and self.up:
            for fog, n in self.nodes.items():
                if fog != src:
                    self.inflight.append((self.clock() + self.latency, n, topic.split("/")[-1], payload))

    def run(self, seconds, step=0.05):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t = round(self.clock.t + step, 4)
            due = [m for m in self.inflight if m[0] <= self.clock.t]
            self.inflight = [m for m in self.inflight if m[0] > self.clock.t]
            for _, n, fog, payload in due:
                n.on_claim(fog, payload)
            for ack in [a for a in self.acks if a[0] <= self.clock.t]:
                self.acks.remove(ack)
                if ack[1] in self.nodes:
                    self.nodes[ack[1]].on_valve(valve_ack(ack[2]))
            for n in self.nodes.values():
                n.tick()


def valve_ack(cmd):
    opened = datetime(2025, 7, 1, 6, 0, tzinfo=timezone.utc)
    until = opened + timedelta(seconds=cmd["seconds"])
    iso = lambda t: t.isoformat().replace("+00:00", "Z")
    return {"id": cmd["id"], "device": "esp", "state": "open", "opened": iso(opened), "until": iso(until)}


def req(fog, zone, minutes=30, n=0):
    return {"id": f"{fog}-{zone}-{n}", "fog": fog, "zone": zone, "seconds": minutes * 60, "ts": "2025-07-01T06:00:00Z"}


def intervals(bridge):
    out = []
    for n in bridge.nodes.values():
        out += [(h["start_unix"], h["end_unix"], h["zone"]) for h in n.history]
    return sorted(out)


def assert_no_overlap(bridge):
    iv = intervals(bridge)
    for (s1, e1, z1), (s2, e2, z2) in zip(iv, iv[1:]):
        assert s2 >= e1, f"{z1} et {z2} pompent en même temps"


def test_single_fog_waters_its_zones_one_after_the_other():
    clock = Clock()
    br = Bridge(clock)
    a = br.node("fog-a")
    for z in ("z1", "z2", "z3"):
        a.on_request(req("fog-a", z))
    br.run(40)
    assert [c[2] for c in br.cmds] == ["fog-a/zone/z1/cmd", "fog-a/zone/z2/cmd", "fog-a/zone/z3/cmd"]
    assert_no_overlap(br)
    assert a.snapshot()["pump"]["holder"] is None and not a.queue


def test_two_fogs_never_pump_at_the_same_time_and_take_turns():
    clock = Clock()
    br = Bridge(clock)
    a, b = br.node("fog-a"), br.node("fog-b")
    for z in ("z1", "z2", "z3"):                 # heure fraîche : les deux fogs veulent arroser en même temps
        a.on_request(req("fog-a", z))
        b.on_request(req("fog-b", z))
    br.run(80)
    assert len(br.cmds) == 6
    assert_no_overlap(br)
    order = [c[1] for c in br.cmds]
    assert order == ["fog-a", "fog-b", "fog-a", "fog-b", "fog-a", "fog-b"]   # alternance


def test_simultaneous_claims_resolved_identically_on_both_sides():
    clock = Clock()
    br = Bridge(clock, latency=1.0)              # pont lent : chacun ne voit l'autre qu'après 1 s
    a, b = br.node("fog-a"), br.node("fog-b")
    b.on_request(req("fog-b", "z1"))
    a.on_request(req("fog-a", "z1"))
    br.run(15)
    assert len(br.cmds) == 2
    assert_no_overlap(br)


def test_the_first_in_line_goes_first():
    clock = Clock()
    br = Bridge(clock)
    a, b = br.node("fog-a"), br.node("fog-b")
    b.on_request(req("fog-b", "z2"))
    br.run(0.5)                                  # fog-b est entré dans la file avant fog-a
    a.on_request(req("fog-a", "z1"))
    br.run(30)
    assert [c[1] for c in br.cmds] == ["fog-b", "fog-a"]


def test_a_dead_neighbour_does_not_block_the_pump_forever():
    clock = Clock()
    br = Bridge(clock)
    a, b = br.node("fog-a"), br.node("fog-b")
    b.on_request(req("fog-b", "z1", minutes=600))   # fog-b prend la pompe pour longtemps…
    br.run(3)
    assert b.using
    del br.nodes["fog-b"]                       # … et son PC s'éteint (plus de rafraîchissement)
    a.on_request(req("fog-a", "z1"))
    br.run(CLAIM_TTL_S - 5)
    assert not [c for c in br.cmds if c[1] == "fog-a"]       # bail encore valable : on attend
    br.run(CLAIM_TTL_S)
    assert [c for c in br.cmds if c[1] == "fog-a"]            # bail expiré : fog-a reprend la pompe


def test_duplicate_request_is_watered_once():
    clock = Clock()
    br = Bridge(clock)
    a = br.node("fog-a")
    a.on_request(req("fog-a", "z1"))
    a.on_request(req("fog-a", "z1"))            # QoS 1 : le même ordre livré deux fois
    br.run(10)
    assert len(br.cmds) == 1


def test_takeover_requests_of_the_neighbour_parcel_use_the_same_pump():
    clock = Clock()
    br = Bridge(clock)
    b = br.node("fog-b")
    b.on_request(req("fog-a", "z1"))            # fog-b a repris la parcelle de fog-a (fog-twin)
    br.run(5)
    assert br.cmds[0][2] == "fog-a/zone/z1/cmd" and br.cmds[0][1] == "fog-b"


@pytest.mark.parametrize("retained", [True, False])
def test_old_retained_claim_only_counts_once_refreshed(retained):
    clock = Clock()
    br = Bridge(clock)
    a = br.node("fog-a")
    a.on_claim("fog-b", {"fog": "fog-b", "want": True, "since": 0, "using": True, "remaining_s": 600},
               retained=retained)
    a.on_request(req("fog-a", "z1"))
    br.run(5)
    assert bool(br.cmds) == retained            # vieux message retained : ignoré ; message vivant : respecté


def test_pump_stays_reserved_until_the_valve_has_really_watered():
    clock = Clock()
    br = Bridge(clock, valve_delay=8.0)         # vanne en deep sleep : elle n'ouvre que 8 s après l'ordre
    a, b = br.node("fog-a"), br.node("fog-b")
    a.on_request(req("fog-a", "z1"))
    br.run(3)
    b.on_request(req("fog-b", "z1"))
    br.run(9)                                    # fog-a : vanne ouverte depuis ~1 s, 5 s d'arrosage
    assert [c[1] for c in br.cmds] == ["fog-a"]
    br.run(30)
    assert [c[1] for c in br.cmds] == ["fog-a", "fog-b"]
    (first,) = a.history
    assert first["confirmed"] and first["end_unix"] - first["start_unix"] >= 8 + 5


def test_valve_that_never_answers_frees_the_pump():
    clock = Clock()
    br = Bridge(clock, valves_alive=False)
    a = br.node("fog-a")
    a.on_request(req("fog-a", "z1"))
    a.on_request(req("fog-a", "z2"))
    br.run(60)
    assert len(br.cmds) == 2 and not a.history[0]["confirmed"]
