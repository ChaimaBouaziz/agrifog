"""Sharing a daily water quota between the fogs — pure functions, easy to test.

Every fog runs EXACTLY the same `allocate()` on EXACTLY the same requests, so both
end up with the same split without any leader: agreement by determinism.

need of a zone for the day (mm) = water to END THE DAY above the stress threshold (+1 % margin):
    threshold+margin − (moisture now − ETc of the day + rain expected), never negative,
    capped by what the valve can deliver in the cool hours (6 h/day)
priority of a fog = sensitivity of its crop × (1 + 2 × stress)
split: each fog first gets its need up to an equal share (nobody loses vs 50/50), then the
       shares not needed by one fog go to the others (need × priority).
    stress = how deep the driest zone is below the threshold, towards the wilting point (0..1)
"""

from dataclasses import dataclass

MIN_SHARE_L = 1.0


@dataclass
class ZoneNeedInput:
    moisture: float      # % vol (médiane des capteurs)
    area_m2: float


@dataclass
class CropParams:
    kc: float
    threshold_pct: float
    fc_pct: float
    wp_pct: float
    root_mm: float
    sensitivity: float   # tomate 1.0 : très sensible au stress ; olivier 0.5 : résistant
    refill_margin_pct: float = 1.0

    @property
    def target_pct(self) -> float:
        return self.fc_pct - self.refill_margin_pct


NEED_MARGIN_PCT = 1.0
COOL_HOURS_PER_DAY = 6.0


def zone_need_mm(moisture: float, crop: CropParams, et0_day: float, rain_mm: float) -> float:
    """Water needed today so that the zone does not fall under the stress threshold."""
    end_of_day = moisture - (crop.kc * et0_day - rain_mm) / crop.root_mm * 100
    return max(0.0, (crop.threshold_pct + NEED_MARGIN_PCT - end_of_day) / 100 * crop.root_mm)


def fog_need_l(zones: list[ZoneNeedInput], crop: CropParams, et0_day: float, rain_mm: float,
               flow_lpm: float = 20.0) -> float:
    max_l = flow_lpm * 60 * COOL_HOURS_PER_DAY      # ce qu'une vanne peut délivrer dans la journée
    return sum(min(zone_need_mm(z.moisture, crop, et0_day, rain_mm) * z.area_m2, max_l) for z in zones)


def stress(moisture: float, crop: CropParams) -> float:
    """0 at the threshold (or above), 1 at the wilting point."""
    span = crop.threshold_pct - crop.wp_pct
    return min(1.0, max(0.0, (crop.threshold_pct - moisture) / span))


def priority(zones: list[ZoneNeedInput], crop: CropParams) -> float:
    worst = max((stress(z.moisture, crop) for z in zones), default=0.0)
    return round(crop.sensitivity * (1 + 2 * worst), 3)


def _water_fill(quota_l: float, need: dict[str, float], prio: dict[str, float]) -> dict[str, float]:
    """Share proportional to need × priority, never more than the need; leftovers go to the others."""
    alloc = {f: 0.0 for f in need}
    remaining, active = quota_l, sorted(f for f in need if need[f] > 0)
    while remaining > MIN_SHARE_L and active:
        weight = {f: need[f] * prio[f] for f in active}
        total_w = sum(weight.values())
        capped = [f for f in active if alloc[f] + remaining * weight[f] / total_w >= need[f]]
        if not capped:  # personne n'est rassasié : on distribue tout
            for f in active:
                alloc[f] += remaining * weight[f] / total_w
            break
        for f in capped:  # les rassasiés prennent leur besoin, le reste est redistribué
            remaining -= need[f] - alloc[f]
            alloc[f] = need[f]
            active.remove(f)
    return alloc


def allocate(quota_l: float, requests: dict[str, dict]) -> dict[str, float]:
    """Split `quota_l` between fogs. requests = {fog: {"need_l": .., "priority": ..}}.

    1. floor: each fog first gets what it needs, up to its EQUAL share
       -> nobody ever gets less than with a plain 50/50 split (when it needs it);
    2. what is left (the shares some fogs do not need) goes to the fogs that still need water,
       in proportion to need × priority;
    3. if everybody is served, the remaining margin is split equally.
    Deterministic: same inputs -> same output on every fog (sorted keys, rounding).
    """
    fogs = sorted(requests)
    need = {f: max(0.0, float(requests[f]["need_l"])) for f in fogs}
    prio = {f: max(0.01, float(requests[f]["priority"])) for f in fogs}
    equal = quota_l / len(fogs)

    if sum(need.values()) <= quota_l:
        spare = (quota_l - sum(need.values())) / len(fogs)
        return {f: round(need[f] + spare, 1) for f in fogs}

    base = {f: min(need[f], equal) for f in fogs}
    extra = _water_fill(quota_l - sum(base.values()), {f: need[f] - base[f] for f in fogs}, prio)
    return {f: round(base[f] + extra[f], 1) for f in fogs}


def isolated_share(quota_l: float, n_fogs: int = 2) -> float:
    """Offline-first: without news from the neighbour, take an equal share."""
    return round(quota_l / n_fogs, 1)


def evening_need_l(zones: list[ZoneNeedInput], crop: CropParams, et0_day: float,
                   et_fraction_until_evening: float) -> float:
    """Water still needed at the evening window (from mid-morning) to stay above the threshold.

    Same logic as zone_need_mm: the night has almost no ET, so the moisture expected at 18h
    must simply be above threshold + margin.
    """
    total = 0.0
    for z in zones:
        at_evening = z.moisture - crop.kc * et0_day * et_fraction_until_evening / crop.root_mm * 100
        total += max(0.0, (crop.threshold_pct + NEED_MARGIN_PCT - at_evening) / 100 * crop.root_mm) * z.area_m2
    return total


def spare_to_release(allocated_l: float, used_l: float, evening_l: float, margin: float = 0.2,
                     min_release_l: float = 200.0) -> float:
    """What a fog can give back today, keeping a 20 % margin on its evening need."""
    spare = allocated_l - used_l - evening_l * (1 + margin)
    return round(spare, 1) if spare >= min_release_l else 0.0
