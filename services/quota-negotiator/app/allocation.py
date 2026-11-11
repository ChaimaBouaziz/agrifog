"""Sharing a daily water quota between the fogs — pure functions, easy to test.

Every fog runs EXACTLY the same `allocate()` on EXACTLY the same requests, so both
end up with the same split without any leader: agreement by determinism.

need of a zone for the day (mm) = refill to target now + ETc of the day − rain expected
priority of a fog = sensitivity of its crop × (1 + 2 × stress)
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


def zone_need_mm(moisture: float, crop: CropParams, et0_day: float, rain_mm: float) -> float:
    refill = max(0.0, (crop.target_pct - moisture) / 100 * crop.root_mm)
    return max(0.0, refill + crop.kc * et0_day - rain_mm)


def fog_need_l(zones: list[ZoneNeedInput], crop: CropParams, et0_day: float, rain_mm: float) -> float:
    return sum(zone_need_mm(z.moisture, crop, et0_day, rain_mm) * z.area_m2 for z in zones)


def stress(moisture: float, crop: CropParams) -> float:
    """0 at the threshold (or above), 1 at the wilting point."""
    span = crop.threshold_pct - crop.wp_pct
    return min(1.0, max(0.0, (crop.threshold_pct - moisture) / span))


def priority(zones: list[ZoneNeedInput], crop: CropParams) -> float:
    worst = max((stress(z.moisture, crop) for z in zones), default=0.0)
    return round(crop.sensitivity * (1 + 2 * worst), 3)


def allocate(quota_l: float, requests: dict[str, dict]) -> dict[str, float]:
    """Split `quota_l` between fogs. requests = {fog: {"need_l": .., "priority": ..}}.

    - if everybody's need fits: everybody gets its need, the rest is split equally (margin);
    - otherwise "water-filling": share proportional to need × priority, a fog never gets
      more than it needs, and what it does not need goes back to the others.
    Deterministic: same inputs -> same output on every fog (sorted keys, rounding).
    """
    fogs = sorted(requests)
    need = {f: max(0.0, float(requests[f]["need_l"])) for f in fogs}
    prio = {f: max(0.01, float(requests[f]["priority"])) for f in fogs}

    if sum(need.values()) <= quota_l:
        spare = (quota_l - sum(need.values())) / len(fogs)
        return {f: round(need[f] + spare, 1) for f in fogs}

    alloc = {f: 0.0 for f in fogs}
    remaining, active = quota_l, [f for f in fogs if need[f] > 0]
    while remaining > MIN_SHARE_L and active:
        weight = {f: need[f] * prio[f] for f in active}
        total_w = sum(weight.values())
        capped = []
        for f in active:
            share = remaining * weight[f] / total_w
            if alloc[f] + share >= need[f]:
                capped.append(f)
        if not capped:  # personne n'est rassasié : on distribue tout et c'est fini
            for f in active:
                alloc[f] += remaining * weight[f] / total_w
            remaining = 0.0
            break
        for f in capped:  # ceux qui sont rassasiés prennent leur besoin, le reste est redistribué
            remaining -= need[f] - alloc[f]
            alloc[f] = need[f]
            active.remove(f)
    return {f: round(alloc[f], 1) for f in fogs}


def isolated_share(quota_l: float, n_fogs: int = 2) -> float:
    """Offline-first: without news from the neighbour, take an equal share."""
    return round(quota_l / n_fogs, 1)


def evening_need_l(zones: list[ZoneNeedInput], crop: CropParams, et0_day: float,
                   et_fraction_until_evening: float) -> float:
    """Water still needed at the evening window, from the moisture now (mid-morning)."""
    total = 0.0
    for z in zones:
        at_evening = z.moisture - crop.kc * et0_day * et_fraction_until_evening / crop.root_mm * 100
        total += max(0.0, (crop.target_pct - at_evening) / 100 * crop.root_mm) * z.area_m2
    return total


def spare_to_release(allocated_l: float, used_l: float, evening_l: float, margin: float = 0.2,
                     min_release_l: float = 200.0) -> float:
    """What a fog can give back today, keeping a 20 % margin on its evening need."""
    spare = allocated_l - used_l - evening_l * (1 + margin)
    return round(spare, 1) if spare >= min_release_l else 0.0
