from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .config import SETTINGS
from .errors import EngineError
from .schema import (ComfortMetrics, ComfortModel, FixedComfortTarget, FuelModel,
                     OccupancySpec, RunPeriod)

NIGHT_HOURS = tuple(range(18, 24)) + tuple(range(0, 7))
JANUARY = 1


@dataclass(frozen=True)
class ComfortCatalogue:
    adaptive: tuple[ComfortModel, ...]
    fixed: tuple[FixedComfortTarget, ...]
    fuels: tuple[FuelModel, ...]

    def adaptive_model(self, identifier: str) -> ComfortModel:
        for model in self.adaptive:
            if model.id == identifier:
                return model
        raise EngineError(
            f"unknown adaptive comfort model {identifier!r}; available: "
            f"{[model.id for model in self.adaptive]}")

    def fixed_target(self, identifier: str) -> FixedComfortTarget:
        for target in self.fixed:
            if target.id == identifier:
                return target
        raise EngineError(
            f"unknown fixed comfort target {identifier!r}; available: "
            f"{[target.id for target in self.fixed]}")

    def fuel(self, identifier: str) -> FuelModel:
        for model in self.fuels:
            if model.fuel == identifier:
                return model
        raise EngineError(
            f"unknown fuel model {identifier!r}; available: "
            f"{[model.fuel for model in self.fuels]}")


@lru_cache(maxsize=4)
def load_catalogue(path: str | None = None) -> ComfortCatalogue:
    source = Path(path) if path else SETTINGS.data_dir / "comfort_models.json"
    if not source.is_file():
        raise EngineError(f"comfort model catalogue not found at {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    adaptive = tuple(ComfortModel.model_validate(item)
                     for item in payload.get("adaptive_models", []))
    fixed = tuple(FixedComfortTarget.model_validate(item)
                  for item in payload.get("fixed_targets", []))
    fuels = tuple(FuelModel.model_validate(item)
                  for item in payload.get("fuel_models", []))
    if not adaptive:
        raise EngineError(f"{source} defines no adaptive comfort models")
    return ComfortCatalogue(adaptive=adaptive, fixed=fixed, fuels=fuels)


def running_mean_series(daily_mean_c: pd.Series, alpha: float) -> pd.Series:
    if daily_mean_c.empty:
        raise EngineError("cannot build a running mean from an empty daily series")
    values = daily_mean_c.to_numpy(dtype=float)
    seed = float(np.mean(values[-min(7, len(values)):]))
    output = np.empty_like(values)
    running = seed
    for _ in range(2):
        running = seed
        for index, day_mean in enumerate(values):
            running = (1.0 - alpha) * day_mean + alpha * running
            output[index] = running
        seed = running
    return pd.Series(output, index=daily_mean_c.index, name="running_mean_c")


def comfort_bounds_frame(outdoor_c: pd.Series, model: ComfortModel) -> pd.DataFrame:
    if outdoor_c.empty:
        raise EngineError("cannot derive comfort bounds from an empty outdoor series")
    daily = outdoor_c.resample("1D").mean().dropna()
    running = running_mean_series(daily, model.running_mean_alpha)
    hourly = running.reindex(outdoor_c.index, method="ffill").bfill()
    bounds = [model.bounds(float(value)) for value in hourly.to_numpy(dtype=float)]
    lower = np.array([item[0] for item in bounds], dtype=float)
    upper = np.array([item[1] for item in bounds], dtype=float)
    return pd.DataFrame({"running_mean_c": hourly.to_numpy(dtype=float),
                         "lower_c": lower, "upper_c": upper},
                        index=outdoor_c.index)


def _to_hourly(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    inferred = pd.infer_freq(series.index[:min(len(series), 20)])
    if inferred in ("H", "h", "60T", "60min"):
        return series.dropna()
    return series.resample("1h").mean().dropna()


def _night_mask(index: pd.DatetimeIndex, hours: tuple[int, ...]) -> np.ndarray:
    if not hours:
        hours = NIGHT_HOURS
    return np.isin(index.hour.to_numpy(), np.asarray(hours, dtype=int))


def _daily_swing(series: pd.Series) -> pd.Series:
    grouped = series.groupby(series.index.date)
    return grouped.max() - grouped.min()


def _time_lag_hours(indoor: pd.Series, outdoor: pd.Series) -> float | None:
    indoor_peaks = indoor.groupby(indoor.index.date).idxmax()
    outdoor_peaks = outdoor.groupby(outdoor.index.date).idxmax()
    lags: list[float] = []
    for day, indoor_peak in indoor_peaks.items():
        outdoor_peak = outdoor_peaks.get(day)
        if outdoor_peak is None or pd.isna(outdoor_peak) or pd.isna(indoor_peak):
            continue
        lag = (indoor_peak - outdoor_peak).total_seconds() / 3600.0
        lags.append((lag + 12.0) % 24.0 - 12.0)
    if not lags:
        return None
    return float(np.median(lags))


def evaluate_comfort(operative_c: pd.Series, outdoor_c: pd.Series,
                     model: ComfortModel, period: RunPeriod,
                     occupancy: OccupancySpec | None = None,
                     fixed_target: FixedComfortTarget | None = None,
                     design_day: date | None = None) -> ComfortMetrics:
    operative = _to_hourly(operative_c.dropna())
    outdoor = _to_hourly(outdoor_c.dropna())
    if operative.empty:
        raise EngineError("no operative temperature values to evaluate comfort against")
    common = operative.index.intersection(outdoor.index)
    if len(common) == 0:
        raise EngineError("operative and outdoor series do not overlap in time")
    operative = operative.loc[common]
    outdoor = outdoor.loc[common]

    bounds = comfort_bounds_frame(outdoor, model)
    values = operative.to_numpy(dtype=float)
    lower = bounds["lower_c"].to_numpy(dtype=float)
    upper = bounds["upper_c"].to_numpy(dtype=float)
    deficit = np.clip(lower - values, 0.0, None)
    excess = np.clip(values - upper, 0.0, None)

    index = operative.index
    night_hours = tuple(fixed_target.hours) if fixed_target is not None else NIGHT_HOURS
    night = _night_mask(index, night_hours)
    january = index.month.to_numpy() == JANUARY

    overnight_pool = np.logical_and(night, january)
    if not overnight_pool.any():
        overnight_pool = night
    minimum_overnight = float(values[overnight_pool].min()) if overnight_pool.any() else None
    overnight_month = JANUARY if bool(np.logical_and(night, january).any()) else None

    occupied_mask = np.ones(len(values), dtype=bool)
    if occupancy is not None:
        hours = occupancy.occupied_hours
        occupied_mask = np.isin(index.hour.to_numpy(), np.asarray(hours, dtype=int)) \
            if hours else np.zeros(len(values), dtype=bool)

    outside = np.logical_or(deficit > 0.0, excess > 0.0)
    daytime = np.isin(index.hour.to_numpy(), np.arange(9, 18))

    design_minimum = None
    design_day_label = None
    worst_margin = None
    if design_day is not None:
        day_mask = index.date == design_day
        if day_mask.any():
            design_minimum = float(values[day_mask].min())
            design_day_label = design_day.isoformat()
            worst_margin = float(np.min(values[day_mask] - lower[day_mask]))
    if worst_margin is None:
        nightly = np.logical_and(night, np.ones(len(values), dtype=bool))
        if nightly.any():
            worst_margin = float(np.min(values[nightly] - lower[nightly]))

    fixed_below = None
    if fixed_target is not None:
        target_mask = np.isin(index.hour.to_numpy(),
                              np.asarray(fixed_target.hours, dtype=int))
        fixed_below = float(np.sum(np.logical_and(
            target_mask, values < fixed_target.lower_bound_c)))

    indoor_swing = _daily_swing(operative)
    outdoor_swing = _daily_swing(outdoor)
    aligned = outdoor_swing.reindex(indoor_swing.index)
    usable = aligned > 0.5
    decrement = float((indoor_swing[usable] / aligned[usable]).median()) \
        if bool(usable.any()) else None

    return ComfortMetrics(
        comfort_model_id=model.id,
        minimum_overnight_operative_c=minimum_overnight,
        minimum_overnight_month=overnight_month,
        minimum_operative_c=float(values.min()),
        maximum_operative_c=float(values.max()),
        maximum_daytime_operative_c=float(values[daytime].max())
        if daytime.any() else None,
        minimum_on_design_day_c=design_minimum,
        design_day_date=design_day_label,
        hours_below_band=float(np.sum(deficit > 0.0)),
        hours_above_band=float(np.sum(excess > 0.0)),
        hours_outside_band=float(np.sum(outside)),
        degree_hours_below_kh=float(np.sum(deficit)),
        degree_hours_above_kh=float(np.sum(excess)),
        degree_hours_total_kh=float(np.sum(deficit) + np.sum(excess)),
        occupied_hours_outside_band=float(np.sum(np.logical_and(outside, occupied_mask))),
        fixed_target_hours_below=fixed_below,
        fixed_target_id=fixed_target.id if fixed_target is not None else None,
        worst_night_margin_k=worst_margin,
        mean_operative_c=float(np.mean(values)),
        mean_daily_swing_k=float(indoor_swing.mean()) if not indoor_swing.empty else None,
        decrement_factor=decrement,
        time_lag_h=_time_lag_hours(operative, outdoor),
    )


def comfort_band_series(outdoor_c: pd.Series,
                        model: ComfortModel) -> tuple[pd.Series, pd.Series]:
    frame = comfort_bounds_frame(_to_hourly(outdoor_c.dropna()), model)
    return frame["lower_c"], frame["upper_c"]
