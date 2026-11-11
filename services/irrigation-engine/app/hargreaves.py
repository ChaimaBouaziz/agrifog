"""Reference evapotranspiration with Hargreaves (FAO-56, eq. 52).

Hargreaves only needs temperatures, which is exactly what our DHT22 sensors give:

    ET0 = 0.0023 * Ra * (Tmean + 17.8) * sqrt(Tmax - Tmin)       [mm/day]

Ra is the extraterrestrial radiation (FAO-56 eq. 21), computed from the latitude
and the day of the year, expressed in mm/day of evaporated water (x 0.408).
"""

import math

GSC = 0.0820  # constante solaire (MJ m-2 min-1)


def extraterrestrial_radiation_mm(lat_deg: float, day_of_year: int) -> float:
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi / 365 * day_of_year)
    delta = 0.409 * math.sin(2 * math.pi / 365 * day_of_year - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    ra_mj = (24 * 60 / math.pi) * GSC * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws)
    )
    return 0.408 * ra_mj


def et0_hargreaves(t_min: float, t_max: float, lat_deg: float, day_of_year: int) -> float:
    t_mean = (t_min + t_max) / 2
    ra = extraterrestrial_radiation_mm(lat_deg, day_of_year)
    return max(0.0, 0.0023 * ra * (t_mean + 17.8) * math.sqrt(max(0.0, t_max - t_min)))
