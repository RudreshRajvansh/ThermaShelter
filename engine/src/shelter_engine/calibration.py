from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .errors import EngineError

MIN_HOURS = 24
MAX_DAYS = 90
ACH_BOUNDS = (0.1, 5.0)
MASS_BOUNDS = (0.5, 2.0)
HEATER_SETTLE_H = 2
MAX_EVALUATIONS = 40
TIME_FIELDS = ("timestamp", "time", "datetime")
TEMPERATURE_FIELDS = ("temperature_c", "temp_c", "temperature")


@dataclass(frozen=True)
class LoggerRecord:
    temperature_c: pd.Series
    heater_on: pd.Series | None


@dataclass(frozen=True)
class CalibrationResult:
    infiltration_ach: float
    mass_multiplier: float
    rmse_before_k: float
    rmse_after_k: float
    hours: int
    evaluations: int


def _field(names: list[str], options: tuple[str, ...]) -> str | None:
    lowered = {name.strip().lower(): name for name in names}
    return next((lowered[option] for option in options if option in lowered), None)


def read_logger_csv(text: str) -> LoggerRecord:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    names = reader.fieldnames or []
    time_field = _field(names, TIME_FIELDS)
    temperature_field = _field(names, TEMPERATURE_FIELDS)
    heater_field = _field(names, ("heater_on", "heater", "bukhari"))
    if time_field is None or temperature_field is None:
        raise EngineError("the logger file needs a timestamp column and a temperature_c "
                          "column")
    stamps, temperatures, heater = [], [], []
    for number, row in enumerate(reader, start=2):
        try:
            stamps.append(pd.Timestamp(row[time_field]))
            temperatures.append(float(row[temperature_field]))
        except (TypeError, ValueError) as error:
            raise EngineError(f"line {number} of the logger file is unreadable: {error}") \
                from error
        if heater_field is not None:
            value = str(row.get(heater_field, "")).strip().lower()
            heater.append(value in ("1", "true", "yes", "on"))
    if not stamps:
        raise EngineError("the logger file holds no readings")
    index = pd.DatetimeIndex(stamps)
    if index.tz is not None:
        raise EngineError("give the logger times in the post's local time without a zone")
    series = pd.Series(temperatures, index=index).sort_index()
    series = series[~series.index.duplicated(keep="first")]
    if not np.isfinite(series.to_numpy()).all() or series.min() < -60 or series.max() > 60:
        raise EngineError("the logger temperatures fall outside -60 to 60 C")
    hourly = series.resample("1h").mean().dropna()
    if len(hourly) < MIN_HOURS:
        raise EngineError(f"the logger covers {len(hourly)} hours; at least {MIN_HOURS} are "
                          "needed to fit anything")
    if (hourly.index[-1] - hourly.index[0]).days > MAX_DAYS:
        raise EngineError(f"calibrate on at most {MAX_DAYS} days at a time")
    heater_hourly = None
    if heater_field is not None:
        flags = pd.Series(heater, index=index).sort_index()
        flags = flags[~flags.index.duplicated(keep="first")]
        heater_hourly = flags.resample("1h").max().reindex(hourly.index).fillna(False)
    return LoggerRecord(temperature_c=hourly, heater_on=heater_hourly)


def fit_mask(record: LoggerRecord) -> pd.Series:
    keep = pd.Series(True, index=record.temperature_c.index)
    if record.heater_on is not None:
        lit = record.heater_on.astype(bool)
        spread = lit.copy()
        for lag in range(1, HEATER_SETTLE_H + 1):
            spread |= lit.shift(lag, fill_value=False)
        keep &= ~spread
    return keep


def rmse(model: pd.Series, measured: pd.Series, mask: pd.Series) -> float:
    joined = pd.concat([model.rename("model"), measured.rename("measured")], axis=1,
                       join="inner")
    joined = joined[mask.reindex(joined.index).fillna(False).astype(bool)]
    if len(joined) < MIN_HOURS:
        raise EngineError(f"only {len(joined)} hours of the model and the logger line up "
                          "without the heater; at least 24 are needed")
    return float(np.sqrt(np.mean((joined["model"] - joined["measured"]) ** 2)))


def fit(evaluate: Callable[[float, float], pd.Series], record: LoggerRecord,
        start_ach: float) -> CalibrationResult:
    from scipy.optimize import minimize

    mask = fit_mask(record)
    measured = record.temperature_c
    calls = {"count": 0}
    seen: dict[tuple[float, float], float] = {}

    def score(values: np.ndarray) -> float:
        key = (round(float(np.clip(values[0], *ACH_BOUNDS)), 3),
               round(float(np.clip(values[1], *MASS_BOUNDS)), 3))
        if key not in seen:
            calls["count"] += 1
            seen[key] = rmse(evaluate(*key), measured, mask)
        return seen[key]

    start = np.array([float(np.clip(start_ach, *ACH_BOUNDS)), 1.0])
    before = score(start)
    result = minimize(score, start, method="Powell", bounds=[ACH_BOUNDS, MASS_BOUNDS],
                      options={"maxfev": MAX_EVALUATIONS, "xtol": 0.02, "ftol": 0.005})
    best = np.round(np.clip(result.x, [ACH_BOUNDS[0], MASS_BOUNDS[0]],
                            [ACH_BOUNDS[1], MASS_BOUNDS[1]]), 3)
    after = score(best)
    if not math.isfinite(after) or after >= before - 1e-9:
        best, after = start, before
    return CalibrationResult(infiltration_ach=float(best[0]), mass_multiplier=float(best[1]),
                             rmse_before_k=before, rmse_after_k=after,
                             hours=int(mask.sum()), evaluations=calls["count"])
