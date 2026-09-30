from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Sequence

import numpy as np

from .errors import WeatherError
from .schema import SitingCell, SitingMap, SiteSpec, SourceRef, SourceStatus
from .site_data import (EARTH_RADIUS_M, DemMosaic, horizon_azimuths_deg,
                        horizon_distances_m, horizon_from_mosaics)
from .solar import sun_position

STEP_MINUTES = 10
BEST_SPOTS = 3
MAX_CELLS = 625
WINTER_FIRST = (11, 1)
WINTER_LAST = (2, 28)
SITING_SOURCE = SourceRef(
    citation=("Winter sun hours from the site's solar geometry against a 36 direction "
              "horizon computed at every grid point from the same elevation tiles as the "
              "site horizon; slope and aspect from central differences of the tiles"),
    status=SourceStatus.DERIVED,
    note=("Direct sun above the terrain horizon only; cloud, snow cover and the shade "
          "of buildings or trees are not in the elevation model."))


def winter_days(year: int = 2019, every_days: int = 7) -> list[date]:
    first = date(year - 1, *WINTER_FIRST)
    last = date(year, *WINTER_LAST)
    days = []
    day = first
    while day <= last:
        days.append(day)
        day += timedelta(days=every_days)
    return days


def sun_table(site: SiteSpec, days: Sequence[date],
              step_minutes: int = STEP_MINUTES) -> tuple[np.ndarray, np.ndarray]:
    steps = 1440 // step_minutes
    altitude = np.empty((len(days), steps))
    azimuth = np.empty((len(days), steps))
    for row, day in enumerate(days):
        for column in range(steps):
            hour = (column + 0.5) * step_minutes / 60.0
            altitude[row, column], azimuth[row, column] = sun_position(
                site.latitude_deg, site.longitude_deg, site.time_zone_h, day, hour)
    return altitude, azimuth


def blocked_at(horizon_azimuths: np.ndarray, blocked_deg: np.ndarray,
               azimuth_deg: np.ndarray) -> np.ndarray:
    return np.interp(np.asarray(azimuth_deg) % 360.0, horizon_azimuths, blocked_deg,
                     period=360.0)


def sun_hours(horizon_azimuths: np.ndarray, blocked_deg: np.ndarray, altitude: np.ndarray,
              azimuth: np.ndarray, step_minutes: int = STEP_MINUTES) -> np.ndarray:
    above = (altitude > 0.0) & (altitude > blocked_at(horizon_azimuths, blocked_deg,
                                                      azimuth))
    return above.sum(axis=-1) * step_minutes / 60.0


def slope_aspect(near: DemMosaic, latitude_deg: np.ndarray, longitude_deg: np.ndarray,
                 spacing_m: float) -> tuple[np.ndarray, np.ndarray]:
    half = spacing_m / 2.0
    d_lat = math.degrees(half / EARTH_RADIUS_M)
    d_lon = np.degrees(half / (EARTH_RADIUS_M * np.cos(np.radians(latitude_deg))))
    east = near.sample(latitude_deg, longitude_deg + d_lon) \
        - near.sample(latitude_deg, longitude_deg - d_lon)
    north = near.sample(latitude_deg + d_lat, longitude_deg) \
        - near.sample(latitude_deg - d_lat, longitude_deg)
    gradient_east = east / spacing_m
    gradient_north = north / spacing_m
    slope = np.degrees(np.arctan(np.hypot(gradient_east, gradient_north)))
    aspect = (np.degrees(np.arctan2(-gradient_east, -gradient_north)) + 360.0) % 360.0
    return slope, aspect


def siting_map(site: SiteSpec, near: DemMosaic, far: DemMosaic, near_radius_m: float,
               half_width_m: float = 1500.0, step_m: float = 250.0,
               year: int = 2019) -> SitingMap:
    if step_m <= 0.0 or half_width_m <= 0.0:
        raise WeatherError("the siting grid needs a positive width and spacing")
    count = 2 * int(round(half_width_m / step_m)) + 1
    if count * count > MAX_CELLS:
        raise WeatherError(f"a {count} by {count} grid is more than the {MAX_CELLS} cells "
                           "the siting map computes; widen the spacing")
    if half_width_m + 1000.0 > near_radius_m:
        raise WeatherError("the siting grid reaches past the detailed elevation tiles")
    offsets = np.linspace(-half_width_m, half_width_m, count)
    east, north = np.meshgrid(offsets, offsets)
    latitude = site.latitude_deg + np.degrees(north / EARTH_RADIUS_M)
    longitude = site.longitude_deg + np.degrees(
        east / (EARTH_RADIUS_M * math.cos(math.radians(site.latitude_deg))))
    elevation = near.sample(latitude, longitude)
    if not np.isfinite(elevation).all():
        raise WeatherError("the elevation tiles do not cover the siting grid")
    slope, aspect = slope_aspect(near, latitude, longitude, step_m)
    azimuths = horizon_azimuths_deg()
    distances = horizon_distances_m()
    days = winter_days(year)
    altitude, sun_azimuth = sun_table(site, days)
    solstice = [index for index, day in enumerate(days)
                if abs((day - date(year - 1, 12, 21)).days) <= 3]
    solstice_row = solstice[0] if solstice else int(np.argmin(altitude.max(axis=1)))
    flat = sun_hours(azimuths, np.zeros(len(azimuths)), altitude, sun_azimuth)
    cells: list[SitingCell] = []
    for index, (lat, lon, height) in enumerate(zip(latitude.ravel(), longitude.ravel(),
                                                  elevation.ravel())):
        blocked = horizon_from_mosaics(float(lat), float(lon), float(height), near, far,
                                       near_radius_m, azimuths, distances)
        hours = sun_hours(azimuths, blocked, altitude, sun_azimuth)
        cells.append(SitingCell(
            east_m=float(east.ravel()[index]), north_m=float(north.ravel()[index]),
            latitude_deg=float(lat), longitude_deg=float(lon), elevation_m=float(height),
            slope_deg=float(slope.ravel()[index]), aspect_deg=float(aspect.ravel()[index]),
            solstice_sun_h=float(hours[solstice_row]), winter_sun_h=float(hours.mean()),
            lost_to_terrain_h=float(flat.mean() - hours.mean())))
    ranked = sorted(range(len(cells)),
                    key=lambda item: (-round(cells[item].winter_sun_h, 2),
                                      cells[item].slope_deg))
    best: list[int] = []
    for item in ranked:
        if all(math.hypot(cells[item].east_m - cells[other].east_m,
                          cells[item].north_m - cells[other].north_m) > 1.5 * step_m
               for other in best):
            best.append(item)
        if len(best) == BEST_SPOTS:
            break
    centre = len(cells) // 2
    return SitingMap(
        site_id=site.id, half_width_m=half_width_m, step_m=step_m, columns=count,
        cells=tuple(cells), best=tuple(best), site_cell=centre,
        winter_from=days[0], winter_to=days[-1], days_sampled=len(days),
        flat_winter_sun_h=float(flat.mean()), source=SITING_SOURCE)
