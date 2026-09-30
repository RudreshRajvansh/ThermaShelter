from __future__ import annotations

import math
from dataclasses import dataclass

from .schema import SourceRef, SourceStatus

SEA_LEVEL_PRESSURE_KPA = 101.325
PERSILY_REFERENCE_PRESSURE_KPA = 101.0
PERSILY_REFERENCE_TEMPERATURE_K = 273.0
CO2_L_S_PER_MJ_DAY = 0.00048
SECONDS_PER_DAY = 86_400.0
CO2_M3_S_PER_W_REFERENCE = CO2_L_S_PER_MJ_DAY / 1000.0 * SECONDS_PER_DAY / 1.0e6
ROOM_REFERENCE_C = 17.0
KELVIN = 273.15
OUTDOOR_CO2_PPM = 427.35
DEFAULT_CO2_LIMIT_PPM = 1000.0

PERSILY_SOURCE = SourceRef(
    citation=("Persily and de Jonge, Carbon dioxide generation rates for building "
              "occupants, Indoor Air 27 (2017) 868-879: 1 MJ/day of energy use gives "
              "0.00048 L/s of CO2 at a respiratory quotient of 0.85, expressed at 101 kPa "
              "and 273 K"),
    status=SourceStatus.VERIFIED,
    url="https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=922955",
    note=("Scaled to the site by the ideal gas law. The same moles of CO2 fill a larger "
          "volume in thin air, so for the same volume of fresh air the concentration "
          "rises further at altitude than at sea level."))
OUTDOOR_CO2_SOURCE = SourceRef(
    citation="NOAA Global Monitoring Laboratory, Mauna Loa annual mean CO2 for 2025, "
             "427.35 ppm",
    status=SourceStatus.VERIFIED,
    url="https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_annmean_mlo.txt")
PRESSURE_SOURCE = SourceRef(
    citation="Standard atmosphere pressure at the site elevation, ISO 2533",
    status=SourceStatus.DERIVED,
    note=("EnergyPlus takes the hourly pressure from the weather file; the standard "
          "atmosphere is used only to size the fresh air, and at Leh the two differ by "
          "under one per cent."))
LIMIT_SOURCE = SourceRef(
    citation="Night time indoor CO2 limit set by the user, default 1000 ppm",
    status=SourceStatus.ASSUMED,
    note=("1000 ppm is an indoor air quality marker for stale air, not a toxicity limit. "
          "It is the design limit the fresh air is sized to hold, and the search treats "
          "a night above it as a broken design."))


@dataclass(frozen=True)
class FreshAirSizing:
    site_pressure_kpa: float
    co2_m3_s_per_w: float
    co2_l_s_per_person: float
    limit_ppm: float
    outdoor_ppm: float
    fresh_air_l_s_per_person: float
    sea_level_fresh_air_l_s_per_person: float

    @property
    def altitude_factor(self) -> float:
        return self.fresh_air_l_s_per_person / self.sea_level_fresh_air_l_s_per_person


def standard_pressure_kpa(elevation_m: float) -> float:
    if not math.isfinite(elevation_m):
        raise ValueError("elevation must be a finite number of metres")
    base = 1.0 - 2.25577e-5 * max(elevation_m, -500.0)
    if base <= 0.0:
        raise ValueError(f"elevation {elevation_m} m is above the standard atmosphere")
    return SEA_LEVEL_PRESSURE_KPA * base ** 5.25588


def co2_rate_m3_s_per_w(pressure_kpa: float, room_c: float = ROOM_REFERENCE_C) -> float:
    if pressure_kpa <= 0.0 or not math.isfinite(pressure_kpa):
        raise ValueError("pressure must be a positive number of kPa")
    kelvin = room_c + KELVIN
    if kelvin <= 0.0:
        raise ValueError("room temperature must be above absolute zero")
    return (CO2_M3_S_PER_W_REFERENCE * (kelvin / PERSILY_REFERENCE_TEMPERATURE_K)
            * (PERSILY_REFERENCE_PRESSURE_KPA / pressure_kpa))


def size_fresh_air(elevation_m: float, metabolic_w_per_person: float,
                   limit_ppm: float = DEFAULT_CO2_LIMIT_PPM,
                   outdoor_ppm: float = OUTDOOR_CO2_PPM) -> FreshAirSizing:
    if metabolic_w_per_person <= 0.0:
        raise ValueError("metabolic rate must be positive to size fresh air")
    if limit_ppm <= outdoor_ppm:
        raise ValueError(
            f"a CO2 limit of {limit_ppm:.0f} ppm is at or below the outdoor "
            f"{outdoor_ppm:.0f} ppm, so no amount of fresh air can hold it")
    pressure = standard_pressure_kpa(elevation_m)
    rate = co2_rate_m3_s_per_w(pressure)
    per_person_l_s = rate * metabolic_w_per_person * 1000.0
    headroom = (limit_ppm - outdoor_ppm) * 1.0e-6
    sea_level = co2_rate_m3_s_per_w(SEA_LEVEL_PRESSURE_KPA) * metabolic_w_per_person * 1000.0
    return FreshAirSizing(
        site_pressure_kpa=pressure, co2_m3_s_per_w=rate,
        co2_l_s_per_person=per_person_l_s, limit_ppm=limit_ppm, outdoor_ppm=outdoor_ppm,
        fresh_air_l_s_per_person=per_person_l_s / headroom,
        sea_level_fresh_air_l_s_per_person=sea_level / headroom)


def steady_state_ppm(people: float, co2_l_s_per_person: float, outdoor_air_l_s: float,
                     outdoor_ppm: float = OUTDOOR_CO2_PPM) -> float:
    if people <= 0.0:
        return outdoor_ppm
    if outdoor_air_l_s <= 0.0:
        return math.inf
    return outdoor_ppm + people * co2_l_s_per_person / outdoor_air_l_s * 1.0e6

