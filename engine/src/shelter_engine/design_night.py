from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .errors import WeatherError
from .schema import SourceRef, SourceStatus

NIGHT_START_HOUR = 18
NIGHT_END_HOUR = 6
WINTER_START_MONTH = 7
MIN_NIGHTS_PER_WINTER = 300
MIN_WINTERS = 5
DEFAULT_RETURN_PERIOD_YEARS = 10.0

GUMBEL_SOURCE = SourceRef(
    citation=("Annual coldest night fitted with a Gumbel distribution for minima by "
              "maximum likelihood (scipy.stats.gumbel_l); the design night is the real "
              "night whose minimum lies closest to the fitted return level"),
    status=SourceStatus.DERIVED,
    note=("A return level from a short record is an extrapolation. The number of "
          "winters behind the fit is always reported with it."))


@dataclass(frozen=True)
class WinterMinimum:
    winter: int
    night_of: date
    minimum_c: float
    observed_c: float | None = None


@dataclass(frozen=True)
class DesignNight:
    night_of: date
    minimum_c: float
    return_level_c: float
    return_period_years: float
    winters: tuple[WinterMinimum, ...]
    gumbel_location_c: float
    gumbel_scale_k: float
    source: str
    notes: tuple[str, ...] = ()

    @property
    def winter_count(self) -> int:
        return len(self.winters)

    @property
    def gap_to_return_level_k(self) -> float:
        return self.minimum_c - self.return_level_c


def nightly_minima(hourly_c: pd.Series) -> pd.Series:
    if not isinstance(hourly_c.index, pd.DatetimeIndex):
        raise WeatherError("the hourly series needs a datetime index")
    clean = hourly_c.dropna()
    if clean.empty:
        raise WeatherError("the hourly series has no values")
    hours = clean.index.hour
    night = clean[(hours >= NIGHT_START_HOUR) | (hours < NIGHT_END_HOUR)]
    if night.empty:
        raise WeatherError("the hourly series has no night hours")
    night_of = (night.index - pd.Timedelta(hours=NIGHT_END_HOUR)).normalize()
    return night.groupby(night_of).min()


def winter_of(day: pd.Timestamp | date) -> int:
    return day.year + 1 if day.month >= WINTER_START_MONTH else day.year


def winter_minima(hourly_c: pd.Series,
                  min_nights: int = MIN_NIGHTS_PER_WINTER) -> list[WinterMinimum]:
    nightly = nightly_minima(hourly_c)
    winters = np.array([winter_of(day) for day in nightly.index])
    out: list[WinterMinimum] = []
    for winter in sorted(set(winters.tolist())):
        block = nightly[winters == winter]
        if len(block) < min_nights:
            continue
        coldest = block.idxmin()
        out.append(WinterMinimum(winter=int(winter), night_of=coldest.date(),
                                 minimum_c=float(block.min())))
    return out


def gumbel_minima(values: np.ndarray) -> tuple[float, float]:
    from scipy.stats import gumbel_l

    data = np.asarray(values, dtype=float)
    if data.size < 2 or not np.isfinite(data).all():
        raise WeatherError("a Gumbel fit needs at least two finite annual minima")
    if float(np.ptp(data)) < 1e-9:
        raise WeatherError("every annual minimum is the same, so no spread can be fitted")
    location, scale = gumbel_l.fit(data)
    if not (math.isfinite(location) and math.isfinite(scale) and scale > 0.0):
        raise WeatherError("the Gumbel fit did not converge on a positive scale")
    return float(location), float(scale)


def return_level_c(location: float, scale: float, return_period_years: float) -> float:
    from scipy.stats import gumbel_l

    if return_period_years <= 1.0:
        raise WeatherError("a return period must be longer than one year")
    return float(gumbel_l.ppf(1.0 / return_period_years, loc=location, scale=scale))


def design_night(hourly_c: pd.Series, source: str,
                 return_period_years: float = DEFAULT_RETURN_PERIOD_YEARS,
                 observed: dict[int, float] | None = None,
                 min_winters: int = MIN_WINTERS) -> DesignNight:
    winters = winter_minima(hourly_c)
    if len(winters) < min_winters:
        raise WeatherError(
            f"only {len(winters)} complete winters are available; a return level needs "
            f"at least {min_winters}")
    minima = np.array([item.minimum_c for item in winters])
    location, scale = gumbel_minima(minima)
    level = return_level_c(location, scale, return_period_years)
    nightly = nightly_minima(hourly_c)
    complete = {item.winter for item in winters}
    candidates = nightly[[winter_of(day) in complete for day in nightly.index]]
    gaps = (candidates - level).abs()
    best = gaps[gaps == gaps.min()].index.max()
    notes: list[str] = []
    if len(winters) < 2 * return_period_years:
        notes.append(
            f"The fit rests on {len(winters)} winters, so the {return_period_years:g} "
            "year return level is an extrapolation beyond most of the record.")
    if observed:
        winters = [WinterMinimum(item.winter, item.night_of, item.minimum_c,
                                 observed.get(item.winter)) for item in winters]
        pairs = [(item.minimum_c, item.observed_c) for item in winters
                 if item.observed_c is not None]
        if pairs:
            bias = float(np.mean([model - seen for model, seen in pairs]))
            notes.append(
                f"Against {len(pairs)} observed winters the source's coldest night "
                f"differs by {bias:+.1f} K on average.")
    return DesignNight(night_of=best.date(), minimum_c=float(candidates[best]),
                       return_level_c=level, return_period_years=return_period_years,
                       winters=tuple(winters), gumbel_location_c=location,
                       gumbel_scale_k=scale, source=source, notes=tuple(notes))
