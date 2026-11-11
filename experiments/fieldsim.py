"""Offline season simulation of one fog, reusing the REAL code of the project:
    - simulator/simulator/soil.py           soil water balance (the "truth")
    - services/irrigation-engine/app/decision.py + hargreaves.py   the engine's rules
    - services/quota-negotiator/app/allocation.py                  the quota split
Modules are loaded from their files, so these experiments always test the code that runs on the fogs.
"""

import importlib.util
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulator"))
sys.path.insert(0, str(ROOT / "libs" / "agrifog_common"))

from agrifog_common import load_config  # noqa: E402
from simulator.soil import bucket_from_config, et0_rate_mm_h  # noqa: E402
from simulator.weather import default_weather_path, load_weather  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


decision = _load("engine_decision", ROOT / "services/irrigation-engine/app/decision.py")
hargreaves = _load("engine_hargreaves", ROOT / "services/irrigation-engine/app/hargreaves.py")
allocation = _load("quota_allocation", ROOT / "services/quota-negotiator/app/allocation.py")

STEP = timedelta(minutes=15)
STEP_H = STEP.total_seconds() / 3600


@dataclass
class ZoneSim:
    """One zone during the season: true soil + valve + counters."""

    id: str
    bucket: object
    area_m2: float
    flow_lpm: float
    open_until: datetime | None = None
    liters: float = 0.0
    etc_potential_mm: float = 0.0
    stress_days: set = field(default_factory=set)
    daily: list = field(default_factory=list)

    def is_open(self, t: datetime) -> bool:
        return self.open_until is not None and t < self.open_until


class Fog:
    def __init__(self, fog: str, weather: list[dict]):
        self.fog = fog
        self.cfg = load_config(fog)
        c, s = self.cfg["crop"], self.cfg["soil"]
        self.crop = decision.Crop(c["name"], c["kc"], c["moisture_threshold_pct"],
                                  s["field_capacity_pct"], c["root_depth_mm"])
        self.alloc_crop = allocation.CropParams(c["kc"], c["moisture_threshold_pct"], s["field_capacity_pct"],
                                                s["wilting_point_pct"], c["root_depth_mm"],
                                                c.get("stress_sensitivity", 1.0))
        self.lat = self.cfg["location"]["latitude"]
        self.weather = weather
        self.zones = [ZoneSim(z["id"], bucket_from_config(self.cfg), z.get("area_m2", 200.0),
                              z.get("flow_lpm", 20.0)) for z in self.cfg["zones"]]
        self.budget_l: float | None = None   # None = pas de quota
        self.used_today = 0.0
        self.sensor_noise = 0.2               # médiane de 3 capteurs bruités (% vol)
        self.rng = random.Random(sum(map(ord, fog)))

    # ---------------------------------------------------------------- helpers
    def et0_estimate(self, d: int, doy: int) -> float:
        """What the engine knows: Hargreaves on yesterday's measured temperatures."""
        y = self.weather[max(0, d - 1)]
        return hargreaves.et0_hargreaves(y["t_min"], y["t_max"], self.lat, doy)

    def rain_next_24h(self, d: int, hour: float) -> float:
        today = self.weather[d]["rain_mm"] * (24 - hour) / 24
        tomorrow = self.weather[d + 1]["rain_mm"] * hour / 24 if d + 1 < len(self.weather) else 0.0
        return today + tomorrow

    def zone_inputs(self):
        return [allocation.ZoneNeedInput(z.bucket.theta, z.area_m2) for z in self.zones]

    # ------------------------------------------------------------ policies
    def agrifog_decide(self, z: ZoneSim, t: datetime, d: int) -> None:
        if z.is_open(t):
            return
        et0 = self.et0_estimate(d, t.timetuple().tm_yday)
        measured = z.bucket.theta + self.rng.gauss(0, self.sensor_noise)   # ce que voit le fog
        dec = decision.decide(measured, t, et0, self.crop,
                              decision.ZoneSpec(z.id, z.area_m2, z.flow_lpm),
                              self.rain_next_24h(d, t.hour), z.open_until)
        if dec.action != "water":
            return
        liters = dec.liters
        if self.budget_l is not None:                     # même règle que le moteur
            left = self.budget_l - self.used_today
            if left < 50:
                return
            liters = min(liters, left)
        self.used_today += liters
        z.open_until = t + timedelta(minutes=liters / z.flow_lpm)

    def fixed_decide(self, z: ZoneSim, t: datetime, mm: float, hour: int) -> None:
        if t.hour == hour and t.minute == 0 and mm > 0:
            liters = mm * z.area_m2
            if self.budget_l is not None:
                liters = max(0.0, min(liters, self.budget_l - self.used_today))
            self.used_today += liters
            z.open_until = t + timedelta(minutes=liters / z.flow_lpm)

    # ----------------------------------------------------------- one step
    def step_soil(self, t: datetime, d: int) -> None:
        day = self.weather[d]
        h = t.hour + t.minute / 60 + STEP_H / 2
        for z in self.zones:
            open_min = 0.0
            if z.open_until is not None:
                open_min = max(0.0, min((z.open_until - t).total_seconds() / 60, STEP_H * 60))
            liters = z.flow_lpm * open_min
            z.liters += liters
            rate = et0_rate_mm_h(day["et0_fao"], h)
            z.etc_potential_mm += z.bucket.kc * rate * STEP_H
            stress_before = z.bucket.stress_h
            z.bucket.step(STEP_H, rate, rain_mm=day["rain_mm"] / 24 * STEP_H, irrig_mm=liters / z.area_m2)
            if z.bucket.stress_h > stress_before:
                z.stress_days.add(d)


def run_season(fog: str, policy: str, fixed_mm: float = 0, fixed_hour: int = 7,
               weather: list[dict] | None = None, budget_fn=None, start_moisture: float | None = None) -> Fog:
    """policy = "agrifog" or "fixed". budget_fn(fog_obj, day_index, hour) may set fog_obj.budget_l."""
    weather = weather or load_weather(default_weather_path())
    f = Fog(fog, weather)
    if start_moisture is not None:
        for z in f.zones:
            z.bucket.theta = start_moisture
    t0 = datetime.fromisoformat(weather[0]["date"]).replace(tzinfo=timezone.utc)
    for d in range(len(weather)):
        f.used_today = 0.0
        for i in range(int(24 / STEP_H)):
            t = t0 + timedelta(days=d) + i * STEP
            if budget_fn and t.minute == 0:
                budget_fn(f, d, t.hour)
            for z in f.zones:
                if policy == "agrifog" and t.minute == 0:
                    f.agrifog_decide(z, t, d)
                elif policy == "fixed":
                    f.fixed_decide(z, t, fixed_mm, fixed_hour)
            f.step_soil(t, d)
        for z in f.zones:
            z.daily.append({"date": weather[d]["date"], "theta": z.bucket.theta, "liters": z.liters})
    return f


def run_pair(quota_l: float, mode: str, weather: list[dict] | None = None) -> dict[str, Fog]:
    """Both fogs, same season, one shared daily quota. mode = "negotiated" or "half".

    negotiated : 03h requests (need, priority) -> 04h allocate() -> 10h spare given back (like the services)
    half       : 50 % each, known from midnight (no cooperation)
    """
    weather = weather or load_weather(default_weather_path())
    fogs = {f: Fog(f, weather) for f in ("fog-a", "fog-b")}
    t0 = datetime.fromisoformat(weather[0]["date"]).replace(tzinfo=timezone.utc)
    for d in range(len(weather)):
        doy = (t0 + timedelta(days=d)).timetuple().tm_yday
        requests = {}
        for f in fogs.values():
            f.used_today = 0.0
            f.budget_l = quota_l / 2      # part égale ; remplacée à 4h par la négociation
        for i in range(int(24 / STEP_H)):
            t = t0 + timedelta(days=d) + i * STEP
            if mode == "negotiated" and t.minute == 0:
                if t.hour == 3:
                    for name, f in fogs.items():
                        zones = f.zone_inputs()
                        need = allocation.fog_need_l(zones, f.alloc_crop, f.et0_estimate(d, doy),
                                                     f.rain_next_24h(d, 3))
                        requests[name] = {"need_l": need, "priority": allocation.priority(zones, f.alloc_crop)}
                elif t.hour == 4:
                    alloc = allocation.allocate(quota_l, requests)
                    for name, f in fogs.items():
                        f.budget_l = alloc[name]
                elif t.hour == 10:
                    for name, f in fogs.items():
                        evening = allocation.evening_need_l(f.zone_inputs(), f.alloc_crop, f.et0_estimate(d, doy),
                                                            decision.et_fraction(10, 8))
                        spare = allocation.spare_to_release(f.budget_l, f.used_today, evening)
                        if spare > 0:
                            other = fogs["fog-b" if name == "fog-a" else "fog-a"]
                            f.budget_l -= spare
                            other.budget_l += spare
            for f in fogs.values():
                for z in f.zones:
                    if t.minute == 0:
                        f.agrifog_decide(z, t, d)
                f.step_soil(t, d)
        for f in fogs.values():
            for z in f.zones:
                z.daily.append({"date": weather[d]["date"], "theta": z.bucket.theta, "liters": z.liters})
    return fogs


def summary(f: Fog) -> dict:
    zs = f.zones
    area = sum(z.area_m2 for z in zs)
    etc_actual = sum(z.bucket.etc_mm for z in zs) / len(zs)
    etc_pot = sum(z.etc_potential_mm for z in zs) / len(zs)
    return {
        "water_m3": sum(z.liters for z in zs) / 1000,
        "water_mm": sum(z.liters for z in zs) / area,
        "drain_mm": sum(z.bucket.drain_mm for z in zs) / len(zs),
        "stress_days": max(len(z.stress_days) for z in zs),
        "stress_h": sum(z.bucket.stress_h for z in zs) / len(zs),
        "rel_et_pct": 100 * etc_actual / etc_pot if etc_pot else 100.0,  # proxy du rendement
    }
