from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .errors import WeatherError
from .site_data import great_circle_km

SOLAR_TOLERANCE = 0.10
TEMPERATURE_TOLERANCE_K = 3.0
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
               "Nov", "Dec")
NASA_POWER_CITATION = ("NASA POWER, all sky surface shortwave downward irradiance "
                       "(ALLSKY_SFC_SW_DWN), from CERES SYN1deg, monthly means")


@dataclass(frozen=True)
class MonthCheck:
    month: int
    file_value: float
    reference_value: float

    @property
    def difference(self) -> float:
        return self.file_value - self.reference_value

    @property
    def ratio(self) -> float | None:
        if self.reference_value == 0.0:
            return None
        return self.file_value / self.reference_value


@dataclass(frozen=True)
class RefereeReport:
    station: str | None
    station_distance_km: float | None
    night_minimum: tuple[MonthCheck, ...] = ()
    day_maximum: tuple[MonthCheck, ...] = ()
    hours_below_record_low: int = 0
    coldest_hour_c: float | None = None
    record_low_c: float | None = None
    record_low_date: str | None = None
    solar: tuple[MonthCheck, ...] = ()
    annual_solar_file_kwh_m2: float | None = None
    annual_solar_reference_kwh_m2: float | None = None
    solar_source: str | None = None
    flags: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def solar_factors(self) -> dict[int, float]:
        return {check.month: round(1.0 / check.ratio, 4) for check in self.solar
                if check.ratio}


def load_station(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise WeatherError(f"station reference not found at {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    months = payload.get("months")
    if not isinstance(months, dict) or not months:
        raise WeatherError(f"{source} holds no monthly table")
    return payload


def station_for(latitude_deg: float, longitude_deg: float,
                stations: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None,
                                                             float | None]:
    best = None
    best_km = None
    for station in stations:
        distance = great_circle_km(latitude_deg, longitude_deg,
                                   float(station["latitude_deg"]),
                                   float(station["longitude_deg"]))
        if distance <= float(station.get("match_radius_km", 15.0)) \
                and (best_km is None or distance < best_km):
            best, best_km = station, distance
    return best, best_km


def _daily_extremes(dry_bulb_c: pd.Series) -> pd.DataFrame:
    grouped = dry_bulb_c.groupby(dry_bulb_c.index.normalize())
    return pd.DataFrame({"low": grouped.min(), "high": grouped.max()})


def _monthly_solar_kwh_m2_day(ghi_wh_m2: pd.Series) -> dict[int, float]:
    daily = ghi_wh_m2.groupby(ghi_wh_m2.index.normalize()).sum() / 1000.0
    return {int(month): float(value)
            for month, value in daily.groupby(daily.index.month).mean().items()}


def referee(frame: pd.DataFrame, latitude_deg: float, longitude_deg: float,
            stations: list[Mapping[str, Any]] | None = None,
            solar_reference: Mapping[int, float] | None = None,
            solar_source: str | None = None) -> RefereeReport:
    if "dry_bulb_c" not in frame or frame["dry_bulb_c"].dropna().empty:
        raise WeatherError("the weather frame has no dry bulb temperature")
    dry = frame["dry_bulb_c"].dropna()
    flags: list[str] = []
    notes: list[str] = []
    station, distance = station_for(latitude_deg, longitude_deg, stations or [])

    minimum_checks: list[MonthCheck] = []
    maximum_checks: list[MonthCheck] = []
    below_record = 0
    record_low = record_date = None
    if station is not None:
        daily = _daily_extremes(dry)
        for key, entry in sorted(station["months"].items(), key=lambda item: int(item[0])):
            month = int(key)
            block = daily[daily.index.month == month]
            if block.empty:
                continue
            if "mean_daily_minimum_c" in entry:
                minimum_checks.append(MonthCheck(month, float(block["low"].mean()),
                                                 float(entry["mean_daily_minimum_c"])))
            if "mean_daily_maximum_c" in entry:
                maximum_checks.append(MonthCheck(month, float(block["high"].mean()),
                                                 float(entry["mean_daily_maximum_c"])))
            low = entry.get("record_low_c")
            if low is not None:
                hours = dry[dry.index.month == month]
                count = int((hours < float(low)).sum())
                below_record += count
                if record_low is None or float(low) < record_low:
                    record_low, record_date = float(low), entry.get("record_low_date")
        cold = [check for check in minimum_checks
                if check.difference < -TEMPERATURE_TOLERANCE_K]
        warm = [check for check in minimum_checks
                if check.difference > TEMPERATURE_TOLERANCE_K]
        if cold:
            worst = min(cold, key=lambda check: check.difference)
            flags.append(
                f"Nights in this weather file run colder than {station['station']}'s "
                f"observed normals in {len(cold)} months, by up to "
                f"{-worst.difference:.1f} K in {MONTH_NAMES[worst.month - 1]} "
                f"({worst.file_value:.1f} C against {worst.reference_value:.1f} C). "
                "Designs simulated on it look colder than they will be.")
        if warm:
            worst = max(warm, key=lambda check: check.difference)
            flags.append(
                f"Nights in this weather file run warmer than {station['station']}'s "
                f"observed normals in {len(warm)} months, by up to "
                f"{worst.difference:.1f} K in {MONTH_NAMES[worst.month - 1]}. Designs "
                "simulated on it look warmer than they will be.")
        if below_record:
            flags.append(
                f"{below_record} hours are colder than the lowest temperature "
                f"{station['station']} has ever recorded for that month, including "
                f"{float(dry.min()):.1f} C against the all time record of "
                f"{record_low:.1f} C ({record_date}).")
    else:
        notes.append("No station table is on file within reach of this site, so the "
                     "temperatures are not checked against observations.")

    solar_checks: list[MonthCheck] = []
    annual_file = annual_reference = None
    if solar_reference and "global_horizontal_wh_m2" in frame:
        monthly = _monthly_solar_kwh_m2_day(frame["global_horizontal_wh_m2"].fillna(0.0))
        for month in range(1, 13):
            if month in monthly and month in solar_reference:
                solar_checks.append(MonthCheck(month, monthly[month],
                                               float(solar_reference[month])))
        days = pd.Series(pd.date_range("2019-01-01", "2019-12-31")).dt.month.value_counts()
        annual_file = float(sum(check.file_value * days[check.month]
                                for check in solar_checks))
        annual_reference = float(sum(check.reference_value * days[check.month]
                                     for check in solar_checks))
        high = [check for check in solar_checks
                if check.ratio and check.ratio > 1.0 + SOLAR_TOLERANCE]
        low = [check for check in solar_checks
               if check.ratio and check.ratio < 1.0 - SOLAR_TOLERANCE]
        if high:
            worst = max(high, key=lambda check: check.ratio or 0.0)
            flags.append(
                f"The weather file's sunshine is more than {SOLAR_TOLERANCE:.0%} above "
                f"{solar_source or 'the satellite reference'} in {len(high)} months, by "
                f"up to {(worst.ratio - 1.0):.0%} in {MONTH_NAMES[worst.month - 1]}. Too "
                "much sun makes a passive design look better than it is.")
        if low:
            worst = min(low, key=lambda check: check.ratio or 0.0)
            flags.append(
                f"The weather file's sunshine is more than {SOLAR_TOLERANCE:.0%} below "
                f"{solar_source or 'the satellite reference'} in {len(low)} months, by up "
                f"to {(1.0 - worst.ratio):.0%} in {MONTH_NAMES[worst.month - 1]}.")
    elif solar_reference is None:
        notes.append("No satellite solar reference is on file for this site.")

    return RefereeReport(
        station=station["station"] if station else None,
        station_distance_km=round(distance, 2) if distance is not None else None,
        night_minimum=tuple(minimum_checks), day_maximum=tuple(maximum_checks),
        hours_below_record_low=below_record,
        coldest_hour_c=float(dry.min()), record_low_c=record_low,
        record_low_date=record_date, solar=tuple(solar_checks),
        annual_solar_file_kwh_m2=annual_file,
        annual_solar_reference_kwh_m2=annual_reference,
        solar_source=solar_source, flags=tuple(flags), notes=tuple(notes))


def monthly_mean(values: Mapping[str, Any]) -> dict[int, float]:
    grouped: dict[int, list[float]] = {}
    for key, value in values.items():
        if len(str(key)) != 6 or str(key)[4:] == "13":
            continue
        try:
            month = int(str(key)[4:])
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number < 0.0 or not np.isfinite(number):
            continue
        grouped.setdefault(month, []).append(number)
    return {month: float(np.mean(items)) for month, items in grouped.items() if items}
