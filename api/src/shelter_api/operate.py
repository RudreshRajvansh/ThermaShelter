from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from shelter_engine import Engine
from shelter_engine.commander import commander_card, splice_forecast
from shelter_engine.errors import EngineError, WeatherError
from shelter_engine.materials import MaterialLibrary
from shelter_engine.metrics import HEATING_CANDIDATES
from shelter_engine.schema import CommanderCard, RunOptions, RunPeriod, SiteSpec
from shelter_engine.site_data import components_from_wind, wind_from_components
from shelter_engine.weather import load_epw

from . import egress
from . import sites as site_builder

FORECAST_URL = egress.endpoint("SHELTER_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
LOGGER = logging.getLogger("shelter_api.operate")
PAST_DAYS = 7
FORECAST_DAYS = 3
CACHE_LIMIT_H = 72.0
SPIN_UP_DAYS = 30
MASS_DENSITY_FLOOR = 800.0


@dataclass(frozen=True)
class Forecast:
    utc_times: np.ndarray
    columns: dict[str, np.ndarray]
    fetched_at: datetime
    source: str


def fetch_forecast(latitude_deg: float, longitude_deg: float, elevation_m: float,
                   fetch: Callable | None = None) -> dict[str, Any]:
    payload = site_builder._fetcher(fetch)(FORECAST_URL, {
        "latitude": egress.grid(latitude_deg), "longitude": egress.grid(longitude_deg),
        "elevation": f"{elevation_m:.0f}", "past_days": str(PAST_DAYS + 1),
        "forecast_days": str(FORECAST_DAYS), "hourly": ",".join(site_builder.HOURLY),
        "wind_speed_unit": "ms", "timezone": "GMT"})
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise site_builder.SiteUnavailable("the forecast returned no hourly series")
    return {"time": hourly["time"],
            **{name: [value if isinstance(value, (int, float)) else None
                      for value in hourly.get(name, [])]
               for name in site_builder.HOURLY}}


def _columns(hourly: dict[str, Any]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    times = np.array(hourly["time"], dtype="datetime64[s]")
    raw = {name: site_builder._column(hourly, name) for name in site_builder.HOURLY}
    east, north = components_from_wind(raw["wind_speed_10m"], raw["wind_direction_10m"])
    speed, direction = wind_from_components(east, north)
    return times, {
        "dry_bulb_c": raw["temperature_2m"], "dew_point_c": raw["dew_point_2m"],
        "relative_humidity_pct": raw["relative_humidity_2m"],
        "pressure_pa": raw["surface_pressure"] * 100.0,
        "global_horizontal_wh_m2": raw["shortwave_radiation"],
        "direct_normal_wh_m2": raw["direct_normal_irradiance"],
        "diffuse_horizontal_wh_m2": raw["diffuse_radiation"],
        "wind_speed_m_s": speed, "wind_direction_deg": direction,
        "sky_cover_tenths": raw["cloud_cover"] / 10.0}


def forecast_for(post_id: str, latitude_deg: float, longitude_deg: float, elevation_m: float,
                 data_dir: Path, now: datetime,
                 fetch: Callable | None = None) -> Forecast | None:
    cache = Path(data_dir) / "forecast" / f"{post_id}.json"
    try:
        hourly = fetch_forecast(latitude_deg, longitude_deg, elevation_m, fetch)
        times, columns = _columns(hourly)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"fetched_at": now.isoformat(), "hourly": hourly}),
                         encoding="utf-8")
        return Forecast(times, columns, now, "forecast")
    except (site_builder.SiteUnavailable, WeatherError, OSError, ValueError) as error:
        LOGGER.info("forecast for %s not fetched, trying the cache: %s", post_id, error)
    if not cache.is_file():
        return None
    try:
        stored = json.loads(cache.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(stored["fetched_at"])
        if (now - fetched).total_seconds() / 3600.0 > CACHE_LIMIT_H:
            return None
        times, columns = _columns(stored["hourly"])
    except (KeyError, ValueError, TypeError, site_builder.SiteUnavailable):
        return None
    return Forecast(times, columns, fetched, "cached forecast")


def night_of_for(now: datetime) -> date:
    return now.date() if now.hour >= 6 else now.date() - timedelta(days=1)


def mass_scaled(library: MaterialLibrary, design, multiplier: float) -> MaterialLibrary:
    if abs(multiplier - 1.0) < 1e-9:
        return library
    used = {layer.material_id for construction in (design.wall, design.roof, design.floor)
            for layer in construction.layers}
    materials = [material.model_copy(update={
        "density_kg_m3": material.density_kg_m3 * multiplier, "density_range": None})
        if material.id in used and material.density_kg_m3 >= MASS_DENSITY_FLOOR
        else material for material in library.materials]
    return MaterialLibrary(materials, library.glazing_systems, library.phase_change_materials,
                           origin=f"{library.origin} (mass x{multiplier:.2f})")


def post_design(post: dict):
    from shelter_engine.schema import DesignSpec

    design = DesignSpec.model_validate(post["design"])
    occupancy = design.occupancy.model_copy(update={"occupants": int(post["soldiers"])})
    return design.variant(occupancy=occupancy)


def calibrated_design(design, calibration: dict | None):
    if not calibration:
        return design
    ventilation = design.ventilation.model_copy(update={
        "infiltration_ach": float(calibration["infiltration_ach"]),
        "infiltration_range": None})
    return design.variant(ventilation=ventilation)


def _nominal(day: date, nominal_year: int) -> date:
    return day.replace(year=nominal_year) if not (day.month == 2 and day.day == 29) \
        else date(nominal_year, 2, 28)


def _shift(series: pd.Series, years: int) -> pd.Series:
    if years == 0:
        return series
    shifted = series.copy()
    shifted.index = series.index + pd.DateOffset(years=years)
    return shifted


def tonight(post: dict, site: SiteSpec, library: MaterialLibrary, data_dir: Path, lang: str,
            now: datetime, engine_kwargs: dict, fetch: Callable | None = None
            ) -> CommanderCard:
    from shelter_engine.schema import DesignSpec, FuelModel

    design = calibrated_design(post_design(post), post.get("calibration"))
    calibration = post.get("calibration") or {}
    run_library = mass_scaled(library, design, float(calibration.get("mass_multiplier", 1.0)))
    night_of = night_of_for(now)
    notes: list[str] = []
    forecast = forecast_for(post["id"], post["latitude_deg"], post["longitude_deg"],
                            site.elevation_m, data_dir, now, fetch)
    run_site = site
    source = "climatology"
    age = None
    if forecast is not None:
        base = load_epw(site.epw_path, site)
        spliced = splice_forecast(base.frame, forecast.utc_times, forecast.columns,
                                  site.time_zone_h, night_of - timedelta(days=PAST_DAYS),
                                  _last_covered(forecast, site.time_zone_h, night_of))
        path = Path(data_dir) / "forecast" / f"{post['id']}.epw"
        site_builder.write_epw(path, site.name, None, site.latitude_deg, site.longitude_deg,
                               site.time_zone_h, site.elevation_m, 2019, spliced, (
                                   f"Site weather with {PAST_DAYS} past days and the Open-Meteo "
                                   f"forecast spliced in for {night_of}.",
                                   f"Built for post {post['id']} by the shelter tool."))
        run_site = site.model_copy(update={"epw_path": str(path)})
        source = forecast.source
        age = max(0.0, (now - forecast.fetched_at).total_seconds() / 3600.0)
    else:
        notes.append("No forecast could be fetched or found in the cache, so tonight is "
                     "simulated on the site's typical weather for this date.")
    engine = Engine(run_site, run_library, **engine_kwargs)
    nominal_night = _nominal(night_of, engine._nominal_year)
    end = nominal_night + timedelta(days=1)
    if end.year != nominal_night.year:
        end = nominal_night
        notes.append("The night crosses the new year; the run stops at midnight.")
    period = RunPeriod(start=nominal_night, end=end, label="tonight")
    free = engine._run(design, RunOptions(period=period, conditioned_run=False,
                                          surface_output=False, spin_up_days=SPIN_UP_DAYS),
                       conditioned=False)
    heated = engine._run(design, RunOptions(period=period, conditioned_run=True,
                                            surface_output=False, spin_up_days=SPIN_UP_DAYS),
                         conditioned=True)
    years = night_of.year - nominal_night.year
    joules = pd.Series(dtype=float)
    for name in HEATING_CANDIDATES:
        wide = heated.output.wide(name)
        if not wide.empty:
            joules = wide.sum(axis=1).resample("1h").sum()
            break
    sunspace = free.output.series("Zone Mean Air Temperature", "SUNSPACE", required=False)
    fuel = engine.fuel if isinstance(engine.fuel, FuelModel) else None
    if fuel is None:
        raise EngineError("the post's heater has no fuel model")
    hourly = lambda series: series.resample("1h").mean()
    return commander_card(
        post_id=post["id"], post_name=post.get("name", post["id"]), design=design,
        site=site, night_of=night_of, issued_at=now, lang=lang, people=int(post["soldiers"]),
        operative_c=_shift(hourly(free.series.operative_c), years),
        room_air_c=_shift(hourly(free.series.air_c), years),
        outdoor_c=_shift(hourly(free.series.outdoor_c), years),
        sunspace_air_c=None if sunspace is None else _shift(hourly(sunspace), years),
        heating_j=_shift(joules, years), fuel=fuel,
        stock_units=post.get("stock_units"), forecast_source=source, forecast_age_h=age,
        model_error_k=calibration.get("rmse_after_k"),
        calibrated_on=date.fromisoformat(calibration["calibrated_on"])
        if calibration.get("calibrated_on") else None, notes=notes)


def _last_covered(forecast: Forecast, utc_offset_h: float, night_of: date) -> date:
    last_local = forecast.utc_times[-1] + np.timedelta64(int(round(utc_offset_h * 3600)), "s")
    covered = (pd.Timestamp(last_local).to_pydatetime() - timedelta(days=1)).date()
    covered = min(covered, night_of + timedelta(days=1))
    if covered < night_of:
        raise WeatherError("the forecast does not reach tonight")
    return covered


class Scheduler:
    def __init__(self, at: str, job: Callable[[], None]):
        hour, minute = (int(part) for part in at.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"the schedule time {at!r} is not a clock time")
        self.hour, self.minute = hour, minute
        self.job = job
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="operate-scheduler",
                                        daemon=True)

    def seconds_until_next(self, now: datetime) -> float:
        target = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.seconds_until_next(datetime.now())):
            try:
                self.job()
            except Exception:
                LOGGER.exception("the scheduled Commander Card run failed")


RECENT_DAYS = 85


def fetch_window(latitude_deg: float, longitude_deg: float, elevation_m: float, first: date,
                 last: date, today: date, fetch: Callable | None = None) -> dict[str, Any]:
    recent = (today - first).days <= RECENT_DAYS
    params = {"latitude": egress.grid(latitude_deg), "longitude": egress.grid(longitude_deg),
              "elevation": f"{elevation_m:.0f}", "start_date": first.isoformat(),
              "end_date": last.isoformat(), "hourly": ",".join(site_builder.HOURLY),
              "wind_speed_unit": "ms", "timezone": "GMT"}
    if not recent:
        params["models"] = site_builder.WEATHER_MODEL
    payload = site_builder._fetcher(fetch)(FORECAST_URL if recent
                                           else site_builder.ARCHIVE_URL, params)
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise site_builder.SiteUnavailable("the weather service returned no hourly series")
    return hourly


def calibration_model(post: dict, site: SiteSpec, library: MaterialLibrary, data_dir: Path,
                      first: date, last: date, hourly: dict[str, Any],
                      engine_kwargs: dict) -> Callable[[float, float], pd.Series]:
    times, columns = _columns(hourly)
    base = load_epw(site.epw_path, site)
    spliced = splice_forecast(base.frame, times, columns, site.time_zone_h, first, last)
    path = Path(data_dir) / "forecast" / f"{post['id']}_calibration.epw"
    site_builder.write_epw(path, site.name, None, site.latitude_deg, site.longitude_deg,
                           site.time_zone_h, site.elevation_m, 2019, spliced, (
                               f"Site weather with the observed days {first} to {last} "
                               "spliced in for calibration.",
                               f"Built for post {post['id']} by the shelter tool."))
    run_site = site.model_copy(update={"epw_path": str(path)})
    design = post_design(post)

    def evaluate(ach: float, mass: float) -> pd.Series:
        variant = calibrated_design(design, {"infiltration_ach": ach})
        engine = Engine(run_site, mass_scaled(library, variant, mass), **engine_kwargs)
        start = _nominal(first, engine._nominal_year)
        period = RunPeriod(start=start, end=_nominal(last, engine._nominal_year),
                           label="calibration")
        products = engine._run(variant, RunOptions(period=period, conditioned_run=False,
                                                   surface_output=False,
                                                   spin_up_days=SPIN_UP_DAYS),
                               conditioned=False)
        return _shift(products.series.air_c.resample("1h").mean(), first.year - start.year)

    return evaluate


def calibrate(post: dict, site: SiteSpec, library: MaterialLibrary, data_dir: Path,
              logger_csv: str, today: date, engine_kwargs: dict,
              fetch: Callable | None = None) -> dict:
    from shelter_engine.calibration import fit, read_logger_csv

    record = read_logger_csv(logger_csv)
    first = record.temperature_c.index[0].date()
    last = record.temperature_c.index[-1].date()
    if first.year != last.year:
        raise EngineError("the logger file crosses the new year; calibrate on one side of it")
    if last > today:
        raise EngineError("the logger file runs into the future")
    hourly = fetch_window(post["latitude_deg"], post["longitude_deg"], site.elevation_m,
                          first - timedelta(days=1), last + timedelta(days=1), today, fetch)
    evaluate = calibration_model(post, site, library, data_dir, first, last, hourly,
                                 engine_kwargs)
    start_ach = float((post.get("calibration") or {}).get(
        "infiltration_ach", post_design(post).ventilation.infiltration_ach))
    result = fit(evaluate, record, start_ach)
    return {"infiltration_ach": result.infiltration_ach,
            "mass_multiplier": result.mass_multiplier,
            "rmse_before_k": round(result.rmse_before_k, 3),
            "rmse_after_k": round(result.rmse_after_k, 3),
            "calibrated_on": today.isoformat(), "hours": result.hours}
