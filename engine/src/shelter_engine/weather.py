from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .errors import WeatherError
from .schema import (Assumption, EngineWarning, Provenance, RunPeriod, Severity,
                     SiteSpec, SourceRef, SourceStatus)

SEA_LEVEL_PRESSURE_PA = 101325.0
BAROMETRIC_EXPONENT = 5.25588
BAROMETRIC_LAPSE = 2.25577e-5
PRESSURE_TOLERANCE = 0.05
ELEVATION_TOLERANCE_M = 50.0
COORDINATE_TOLERANCE_DEG = 0.25
RUNNING_MEAN_ALPHA = 0.80
RUNNING_MEAN_SEED_DAYS = 7
MAX_INTERPOLATED_GAP_H = 6
MISSING_FRACTION_SEVERE = 0.01

EPW_HEADER_LINES = 8
_COLUMNS = [
    "year", "month", "day", "hour", "minute", "flags", "dry_bulb_c", "dew_point_c",
    "relative_humidity_pct", "pressure_pa", "ext_horizontal_radiation_wh_m2",
    "ext_direct_normal_wh_m2", "horizontal_infrared_wh_m2",
    "global_horizontal_wh_m2", "direct_normal_wh_m2", "diffuse_horizontal_wh_m2",
    "global_horizontal_illuminance", "direct_normal_illuminance",
    "diffuse_horizontal_illuminance", "zenith_luminance", "wind_direction_deg",
    "wind_speed_m_s", "total_sky_cover", "opaque_sky_cover", "visibility_km",
    "ceiling_height_m", "present_weather_observation", "present_weather_codes",
    "precipitable_water_mm", "aerosol_optical_depth", "snow_depth_cm",
    "days_since_last_snowfall", "albedo", "liquid_precipitation_depth_mm",
    "liquid_precipitation_quantity_h",
]
_MISSING = {
    "dry_bulb_c": 99.9, "dew_point_c": 99.9, "relative_humidity_pct": 999.0,
    "pressure_pa": 999999.0, "horizontal_infrared_wh_m2": 9999.0,
    "global_horizontal_wh_m2": 9999.0, "direct_normal_wh_m2": 9999.0,
    "diffuse_horizontal_wh_m2": 9999.0, "wind_direction_deg": 999.0,
    "wind_speed_m_s": 999.0, "total_sky_cover": 99.0, "opaque_sky_cover": 99.0,
}
_CRITICAL = ("dry_bulb_c", "relative_humidity_pct", "pressure_pa",
             "direct_normal_wh_m2", "diffuse_horizontal_wh_m2")

PRESSURE_SOURCE = SourceRef(
    citation="ISO 2533 standard atmosphere barometric relation used to check the "
             "EPW station pressure column against the site elevation",
    status=SourceStatus.VERIFIED)


def standard_pressure_pa(elevation_m: float) -> float:
    if elevation_m <= -500.0 or elevation_m >= 11000.0:
        raise WeatherError(f"elevation {elevation_m} m is outside the troposphere model")
    return SEA_LEVEL_PRESSURE_PA * (1.0 - BAROMETRIC_LAPSE * elevation_m) ** BAROMETRIC_EXPONENT


def air_density_kg_m3(pressure_pa: float, temperature_c: float) -> float:
    kelvin = temperature_c + 273.15
    if kelvin <= 0.0:
        raise WeatherError("temperature below absolute zero")
    return pressure_pa / (287.058 * kelvin)


@dataclass
class EpwHeader:
    city: str
    state: str
    country: str
    source: str
    wmo: str
    latitude_deg: float
    longitude_deg: float
    time_zone_h: float
    elevation_m: float


@dataclass
class WeatherFile:
    path: Path
    header: EpwHeader
    frame: pd.DataFrame
    warnings: list[EngineWarning] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)

    @property
    def hours(self) -> int:
        return len(self.frame)

    @property
    def dry_bulb(self) -> pd.Series:
        return self.frame["dry_bulb_c"]

    def slice(self, period: RunPeriod) -> pd.DataFrame:
        start = datetime.combine(self._map_date(period.start), datetime.min.time())
        end = datetime.combine(self._map_date(period.end), datetime.min.time()) \
            + timedelta(days=1)
        window = self.frame.loc[(self.frame.index >= start) & (self.frame.index < end)]
        if window.empty:
            raise WeatherError(
                f"the weather file holds no hours between {period.start} and {period.end}")
        return window

    def _map_date(self, value: date) -> date:
        year = int(self.frame.index[0].year)
        try:
            return date(year, value.month, value.day)
        except ValueError:
            return date(year, value.month, 28)

    def daily_mean(self) -> pd.Series:
        return self.dry_bulb.resample("1D").mean()

    def running_mean_outdoor_c(self, alpha: float = RUNNING_MEAN_ALPHA) -> pd.Series:
        daily = self.daily_mean()
        if daily.empty:
            raise WeatherError("weather file has no daily means")
        values = daily.to_numpy(dtype=float)
        seed_window = values[-min(RUNNING_MEAN_SEED_DAYS, len(values)):]
        running = float(np.mean(seed_window))
        output = np.empty_like(values)
        for _ in range(2):
            for index, day_mean in enumerate(values):
                running = (1.0 - alpha) * day_mean + alpha * running
                output[index] = running
        return pd.Series(output, index=daily.index, name="running_mean_outdoor_c")

    def hourly_running_mean(self, alpha: float = RUNNING_MEAN_ALPHA) -> pd.Series:
        daily = self.running_mean_outdoor_c(alpha)
        expanded = daily.reindex(self.frame.index, method="ffill")
        return expanded.bfill()

    def coldest_day(self, period: RunPeriod | None = None) -> date:
        frame = self.frame if period is None else self.slice(period)
        daily = frame["dry_bulb_c"].resample("1D").mean().dropna()
        if daily.empty:
            raise WeatherError("no complete day available to pick a design day")
        return daily.idxmin().date()

    def hottest_day(self, period: RunPeriod | None = None) -> date:
        frame = self.frame if period is None else self.slice(period)
        daily = frame["dry_bulb_c"].resample("1D").mean().dropna()
        if daily.empty:
            raise WeatherError("no complete day available to pick a design day")
        return daily.idxmax().date()

    def annual_mean_c(self) -> float:
        return float(self.dry_bulb.mean())

    def heating_degree_days(self, base_c: float = 18.0) -> float:
        daily = self.daily_mean()
        return float(np.clip(base_c - daily.to_numpy(dtype=float), 0.0, None).sum())

    def cooling_degree_days(self, base_c: float = 18.0) -> float:
        daily = self.daily_mean()
        return float(np.clip(daily.to_numpy(dtype=float) - base_c, 0.0, None).sum())


def _parse_header(lines: list[str], path: Path) -> EpwHeader:
    if not lines:
        raise WeatherError(f"{path} is empty")
    parts = [item.strip() for item in lines[0].split(",")]
    if not parts or parts[0].upper() != "LOCATION":
        raise WeatherError(f"{path} does not start with an EPW LOCATION header")
    if len(parts) < 10:
        raise WeatherError(f"{path} has a truncated LOCATION header")
    try:
        latitude = float(parts[6])
        longitude = float(parts[7])
        time_zone = float(parts[8])
        elevation = float(parts[9])
    except ValueError as error:
        raise WeatherError(f"{path} has a malformed LOCATION header: {error}") from error
    return EpwHeader(city=parts[1], state=parts[2], country=parts[3], source=parts[4],
                     wmo=parts[5], latitude_deg=latitude, longitude_deg=longitude,
                     time_zone_h=time_zone, elevation_m=elevation)


def _build_index(frame: pd.DataFrame, path: Path) -> pd.DatetimeIndex:
    months = frame["month"].to_numpy(dtype=int)
    days = frame["day"].to_numpy(dtype=int)
    hours = frame["hour"].to_numpy(dtype=int)
    if hours.min() < 1 or hours.max() > 24:
        raise WeatherError(f"{path} has hour values outside 1..24")
    has_leap_day = bool(np.any((months == 2) & (days == 29)))
    year = 2020 if has_leap_day else 2019
    try:
        stamps = pd.to_datetime({
            "year": np.full(len(frame), year),
            "month": months, "day": days, "hour": hours - 1,
        })
    except (ValueError, OverflowError) as error:
        raise WeatherError(f"{path} has an invalid date column: {error}") from error
    index = pd.DatetimeIndex(stamps)
    if index.has_duplicates:
        duplicates = int(index.duplicated().sum())
        raise WeatherError(f"{path} repeats {duplicates} hourly timestamps")
    if not index.is_monotonic_increasing:
        raise WeatherError(f"{path} rows are not in chronological order")
    return index


def _clean_missing(frame: pd.DataFrame, path: Path,
                   warnings: list[EngineWarning]) -> pd.DataFrame:
    for column, sentinel in _MISSING.items():
        if column not in frame:
            continue
        series = frame[column].astype(float)
        flagged = series >= sentinel - 1e-6
        count = int(flagged.sum())
        if count == 0:
            continue
        fraction = count / max(1, len(series))
        series = series.mask(flagged)
        interpolated = series.interpolate(method="time",
                                          limit=MAX_INTERPOLATED_GAP_H,
                                          limit_direction="both")
        remaining = int(interpolated.isna().sum())
        frame[column] = interpolated
        severity = Severity.SEVERE if (column in _CRITICAL
                                       and fraction > MISSING_FRACTION_SEVERE) \
            else Severity.WARNING
        warnings.append(EngineWarning(
            code="weather_missing_values", severity=severity, origin="weather",
            count=count,
            message=(f"{path.name} column {column} carries {count} missing sentinel "
                     f"values ({fraction * 100:.2f} percent); gaps up to "
                     f"{MAX_INTERPOLATED_GAP_H} h were interpolated and {remaining} "
                     "hours remain unfilled.")))
    return frame


def _check_pressure(frame: pd.DataFrame, header: EpwHeader, site: SiteSpec | None,
                    path: Path, warnings: list[EngineWarning],
                    assumptions: list[Assumption]) -> None:
    elevation = site.elevation_m if site is not None else header.elevation_m
    expected = standard_pressure_pa(elevation)
    observed = float(frame["pressure_pa"].mean())
    assumptions.append(Assumption(
        name="weather_mean_station_pressure", value=round(observed, 1), unit="Pa",
        source=PRESSURE_SOURCE, provenance=Provenance.MEASURED))
    assumptions.append(Assumption(
        name="expected_station_pressure_for_elevation", value=round(expected, 1),
        unit="Pa", source=PRESSURE_SOURCE, provenance=Provenance.DERIVED))
    if expected <= 0.0:
        return
    deviation = abs(observed - expected) / expected
    if deviation > PRESSURE_TOLERANCE:
        warnings.append(EngineWarning(
            code="weather_pressure_mismatch", severity=Severity.SEVERE, origin="weather",
            message=(
                f"{path.name} reports a mean station pressure of {observed:.0f} Pa but "
                f"{elevation:.0f} m elevation implies about {expected:.0f} Pa, a "
                f"{deviation * 100:.1f} percent difference. Air density, infiltration heat "
                "loss and convection coefficients all scale with this column, and "
                "EnergyPlus will not warn about it. Correct the pressure column in "
                "Layer 0 before trusting any result from this file.")))


def _check_location(header: EpwHeader, site: SiteSpec | None, path: Path,
                    warnings: list[EngineWarning]) -> None:
    if site is None:
        return
    if abs(header.elevation_m - site.elevation_m) > ELEVATION_TOLERANCE_M:
        warnings.append(EngineWarning(
            code="weather_elevation_mismatch", severity=Severity.WARNING,
            origin="weather",
            message=(f"{path.name} states {header.elevation_m:.0f} m elevation while the "
                     f"site record says {site.elevation_m:.0f} m.")))
    if (abs(header.latitude_deg - site.latitude_deg) > COORDINATE_TOLERANCE_DEG
            or abs(header.longitude_deg - site.longitude_deg) > COORDINATE_TOLERANCE_DEG):
        warnings.append(EngineWarning(
            code="weather_coordinate_mismatch", severity=Severity.WARNING,
            origin="weather",
            message=(f"{path.name} is for {header.latitude_deg:.3f}, "
                     f"{header.longitude_deg:.3f} while the site record says "
                     f"{site.latitude_deg:.3f}, {site.longitude_deg:.3f}; solar geometry "
                     "follows the weather file header.")))
    if abs(header.time_zone_h - site.time_zone_h) > 0.01:
        warnings.append(EngineWarning(
            code="weather_timezone_mismatch", severity=Severity.SEVERE, origin="weather",
            message=(f"{path.name} declares time zone {header.time_zone_h} while the site "
                     f"record says {site.time_zone_h}; solar position would be shifted.")))


def load_epw(path: str | Path, site: SiteSpec | None = None) -> WeatherFile:
    source = Path(path)
    if not source.is_file():
        raise WeatherError(f"weather file not found at {source}")
    try:
        text = source.read_text(encoding="latin-1")
    except OSError as error:
        raise WeatherError(f"cannot read {source}: {error}") from error
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) <= EPW_HEADER_LINES:
        raise WeatherError(f"{source} holds no data rows")
    header = _parse_header(lines, source)
    warnings: list[EngineWarning] = []
    assumptions: list[Assumption] = []

    records = []
    for offset, line in enumerate(lines[EPW_HEADER_LINES:]):
        parts = line.split(",")
        if len(parts) < len(_COLUMNS):
            parts = parts + [""] * (len(_COLUMNS) - len(parts))
        elif len(parts) > len(_COLUMNS):
            parts = parts[:len(_COLUMNS)]
        records.append(parts)
    frame = pd.DataFrame(records, columns=_COLUMNS)
    numeric = [column for column in _COLUMNS
               if column not in ("flags", "present_weather_codes")]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["month", "day", "hour"]].isna().any().any():
        raise WeatherError(f"{source} has unreadable date or hour columns")

    if len(frame) not in (8760, 8784):
        warnings.append(EngineWarning(
            code="weather_row_count", severity=Severity.SEVERE, origin="weather",
            message=(f"{source.name} holds {len(frame)} data rows; a full year is 8760 "
                     "rows, or 8784 in a leap year. EnergyPlus will refuse run periods "
                     "that fall outside the rows present.")))

    frame.index = _build_index(frame, source)
    frame = frame.drop(columns=["year", "month", "day", "hour", "minute"])
    frame = _clean_missing(frame, source, warnings)
    unfilled = int(frame["dry_bulb_c"].isna().sum())
    if unfilled:
        first_gap = frame.index[frame["dry_bulb_c"].isna()][0]
        raise WeatherError(
            f"{source} leaves {unfilled} dry bulb hours unusable after interpolation, "
            f"the first at {first_gap}. Gaps longer than {MAX_INTERPOLATED_GAP_H} h are "
            "not filled, because inventing a day of weather would corrupt every result "
            "built on it. Repair the file in Layer 0.")
    _check_pressure(frame, header, site, source, warnings, assumptions)
    _check_location(header, site, source, warnings)

    return WeatherFile(path=source, header=header, frame=frame, warnings=warnings,
                       assumptions=assumptions)


@lru_cache(maxsize=8)
def _cached_epw(resolved: str, mtime: float, site_key: str) -> WeatherFile:
    return load_epw(resolved)


def load_epw_cached(path: str | Path, site: SiteSpec | None = None) -> WeatherFile:
    source = Path(path)
    if not source.is_file():
        raise WeatherError(f"weather file not found at {source}")
    weather = _cached_epw(str(source.resolve()), source.stat().st_mtime, "")
    if site is None:
        return weather
    warnings = list(weather.warnings)
    assumptions = list(weather.assumptions)
    codes = {item.code for item in warnings}
    extra: list[EngineWarning] = []
    _check_location(weather.header, site, source, extra)
    _check_pressure(weather.frame, weather.header, site, source, extra, [])
    for item in extra:
        if item.code not in codes:
            warnings.append(item)
    return WeatherFile(path=weather.path, header=weather.header, frame=weather.frame,
                       warnings=warnings, assumptions=assumptions)


def dew_point_c(temperature_c: float | np.ndarray,
                relative_humidity_pct: float | np.ndarray):
    humidity = np.clip(np.asarray(relative_humidity_pct, dtype=float), 0.1, 100.0)
    temperature = np.asarray(temperature_c, dtype=float)
    a, b = 17.625, 243.04
    gamma = np.log(humidity / 100.0) + (a * temperature) / (b + temperature)
    return (b * gamma) / (a - gamma)


def saturation_pressure_pa(temperature_c: float | np.ndarray):
    temperature = np.asarray(temperature_c, dtype=float)
    return 610.94 * np.exp(17.625 * temperature / (temperature + 243.04))


def hours_in(period: RunPeriod) -> int:
    return period.days * 24
