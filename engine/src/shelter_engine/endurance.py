from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from .errors import EngineError
from .schema import SourceRef, SourceStatus

FLOOR_C = 5.0
CUTOFF_HOUR = 18
NIGHT_END_HOUR = 6

FLOOR_SOURCE = SourceRef(
    citation=("Fuel-out floor of 5 C in the room: the point the tool treats as the "
              "limit of safe shelter for resting men without heat"),
    status=SourceStatus.ASSUMED,
    note=("A planning threshold, not a physiological limit. It is shown next to every "
          "fuel-out figure and can be changed per request."))


@dataclass(frozen=True)
class EnduranceResult:
    night_of: date
    cutoff: datetime
    floor_c: float
    reached_floor: bool
    hours_above_floor: float
    operative_at_cutoff_c: float
    minimum_after_cutoff_c: float
    minimum_at: datetime
    hours_simulated_after_cutoff: float
    night_heating_kwh: float
    night_fuel_units: float | None
    fuel_unit_name: str | None
    fuel: str | None


def cutoff_for(night_of: date, hour: int = CUTOFF_HOUR) -> datetime:
    if not 0 <= hour <= 23:
        raise EngineError("the fuel-out hour must lie between 0 and 23")
    return datetime.combine(night_of, time(hour, 0))


def night_window(night_of: date) -> tuple[datetime, datetime]:
    start = datetime.combine(night_of, time(CUTOFF_HOUR, 0))
    return start, datetime.combine(night_of + timedelta(days=1), time(NIGHT_END_HOUR, 0))


def coast_down(operative_c: pd.Series, cutoff: datetime,
               floor_c: float = FLOOR_C) -> tuple[bool, float, float, float, datetime,
                                                  float]:
    after = operative_c[operative_c.index >= pd.Timestamp(cutoff)].dropna()
    if after.empty:
        raise EngineError("the simulation ends before the heater is cut")
    start = float(after.iloc[0])
    lowest = float(after.min())
    lowest_at = after.idxmin().to_pydatetime()
    span = (after.index[-1] - pd.Timestamp(cutoff)).total_seconds() / 3600.0 + 1.0
    below = after[after < floor_c]
    if below.empty:
        return False, span, start, lowest, lowest_at, span
    hours = (below.index[0] - pd.Timestamp(cutoff)).total_seconds() / 3600.0
    return True, max(hours, 0.0), start, lowest, lowest_at, span


def window_sum(series: pd.Series, start: datetime, end: datetime) -> float:
    mask = (series.index >= pd.Timestamp(start)) & (series.index < pd.Timestamp(end))
    return float(series[mask].sum())
