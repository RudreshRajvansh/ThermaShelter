from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

from .errors import WeatherError
from .schema import (ClimateObjective, GroundTemperatures, HorizonProfile, SourceRef)

EARTH_RADIUS_M = 6_371_000.0
REFRACTION_COEFFICIENT = 0.13
HORIZON_AZIMUTHS = 36
HORIZON_NEAREST_M = 60.0
HORIZON_FARTHEST_M = 50_000.0
HORIZON_SAMPLES = 160
TILE_SIZE = 256
MERCATOR_LIMIT_DEG = 85.05112878
TERRAIN_HALF_WIDTH_M = 5000.0
TERRAIN_POINTS = 51
MAX_BLOCKED_DEG = 89.9
SOIL_DIFFUSIVITY_M2_DAY = 0.05
GROUND_DEPTH_M = 0.5
DAYS_PER_YEAR = 365.0
MONTH_MID_DAYS = np.array([15.5, 45.0, 74.5, 105.0, 135.5, 166.0, 196.5, 227.5, 258.0,
                           288.5, 319.0, 349.5])
HOURS_PER_YEAR = 8760


def horizon_distances_m(samples: int = HORIZON_SAMPLES, nearest_m: float = HORIZON_NEAREST_M,
                        farthest_m: float = HORIZON_FARTHEST_M) -> np.ndarray:
    if samples < 2 or not 0.0 < nearest_m < farthest_m:
        raise WeatherError("horizon sampling needs at least two distances in increasing order")
    return np.geomspace(nearest_m, farthest_m, samples)


def horizon_azimuths_deg(count: int = HORIZON_AZIMUTHS) -> np.ndarray:
    if not 2 <= count <= 72:
        raise WeatherError("a horizon profile holds between 2 and 72 directions")
    return np.arange(count) * (360.0 / count)


def destination_points(latitude_deg: float, longitude_deg: float,
                       azimuths_deg: Sequence[float],
                       distances_m: Sequence[float]) -> np.ndarray:
    if not (-90.0 <= latitude_deg <= 90.0 and -180.0 <= longitude_deg <= 180.0):
        raise WeatherError("coordinates must be a latitude in [-90, 90] and a longitude in "
                           "[-180, 180]")
    start_lat = math.radians(latitude_deg)
    start_lon = math.radians(longitude_deg)
    azimuth = np.radians(np.asarray(azimuths_deg, dtype=float))[:, None]
    angle = np.asarray(distances_m, dtype=float)[None, :] / EARTH_RADIUS_M
    lat = np.arcsin(np.sin(start_lat) * np.cos(angle)
                    + np.cos(start_lat) * np.sin(angle) * np.cos(azimuth))
    lon = start_lon + np.arctan2(np.sin(azimuth) * np.sin(angle) * np.cos(start_lat),
                                 np.cos(angle) - np.sin(start_lat) * np.sin(lat))
    lon = (np.degrees(lon) + 540.0) % 360.0 - 180.0
    return np.stack([np.degrees(lat), lon], axis=-1)


def great_circle_km(latitude_a: float, longitude_a: float, latitude_b: float,
                    longitude_b: float) -> float:
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    d_lat = lat_b - lat_a
    d_lon = math.radians(longitude_b - longitude_a)
    value = math.sin(d_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(d_lon / 2) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(value))) / 1000.0


def horizon_from_elevations(site_elevation_m: float, elevations_m: np.ndarray,
                            distances_m: Sequence[float]) -> np.ndarray:
    grid = np.asarray(elevations_m, dtype=float)
    distance = np.asarray(distances_m, dtype=float)
    if grid.ndim != 2 or grid.shape[1] != len(distance):
        raise WeatherError("elevations must be a directions by distances grid")
    if not (np.isfinite(grid).all() and math.isfinite(site_elevation_m)):
        raise WeatherError("the elevation samples contain a missing value")
    effective_radius = EARTH_RADIUS_M / (1.0 - REFRACTION_COEFFICIENT)
    drop = distance ** 2 / (2.0 * effective_radius)
    rise = grid - site_elevation_m - drop[None, :]
    angles = np.degrees(np.arctan2(rise, distance[None, :]))
    return np.clip(angles.max(axis=1), 0.0, MAX_BLOCKED_DEG)


def world_pixels(latitude_deg, longitude_deg, zoom: int,
                 tile_size: int = TILE_SIZE) -> tuple[np.ndarray, np.ndarray]:
    size = float(2 ** zoom * tile_size)
    latitude = np.clip(np.asarray(latitude_deg, dtype=float), -MERCATOR_LIMIT_DEG,
                       MERCATOR_LIMIT_DEG)
    longitude = np.asarray(longitude_deg, dtype=float)
    x = (longitude + 180.0) / 360.0 * size
    y = (1.0 - np.arcsinh(np.tan(np.radians(latitude))) / math.pi) / 2.0 * size
    return x, y


def tile_range(latitude_deg: float, longitude_deg: float, radius_m: float,
               zoom: int) -> tuple[int, int, int, int]:
    if abs(latitude_deg) > MERCATOR_LIMIT_DEG - 1.0:
        raise WeatherError("elevation tiles do not reach within a degree of the poles")
    if radius_m <= 0.0:
        raise WeatherError("the terrain radius must be positive")
    d_lat = math.degrees(radius_m / EARTH_RADIUS_M)
    d_lon = math.degrees(radius_m / (EARTH_RADIUS_M * math.cos(math.radians(latitude_deg))))
    xs, ys = world_pixels([latitude_deg + d_lat, latitude_deg - d_lat],
                          [longitude_deg - d_lon, longitude_deg + d_lon], zoom)
    last = 2 ** zoom - 1
    return (int(math.floor(xs[0] / TILE_SIZE)), int(math.floor(xs[1] / TILE_SIZE)),
            max(0, int(math.floor(ys[0] / TILE_SIZE))),
            min(last, int(math.floor(ys[1] / TILE_SIZE))))


def zoom_for(latitude_deg: float, longitude_deg: float, radius_m: float, preferred: int,
             max_tiles: int = 16) -> int:
    for zoom in range(preferred, -1, -1):
        x0, x1, y0, y1 = tile_range(latitude_deg, longitude_deg, radius_m, zoom)
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= max_tiles:
            return zoom
    return 0


@dataclass(frozen=True)
class DemMosaic:
    zoom: int
    tile_x0: int
    tile_y0: int
    heights_m: np.ndarray

    def sample(self, latitude_deg, longitude_deg) -> np.ndarray:
        x, y = world_pixels(latitude_deg, longitude_deg, self.zoom)
        world = float(2 ** self.zoom * TILE_SIZE)
        column = (x - self.tile_x0 * TILE_SIZE) % world - 0.5
        row = y - self.tile_y0 * TILE_SIZE - 0.5
        rows, columns = self.heights_m.shape
        column = np.asarray(column, dtype=float)
        row = np.asarray(row, dtype=float)
        inside = (column >= 0.0) & (column <= columns - 1) & (row >= 0.0) & (row <= rows - 1)
        c0 = np.clip(np.floor(column).astype(int), 0, max(columns - 2, 0))
        r0 = np.clip(np.floor(row).astype(int), 0, max(rows - 2, 0))
        fc = np.clip(column - c0, 0.0, 1.0)
        fr = np.clip(row - r0, 0.0, 1.0)
        grid = self.heights_m
        c1 = np.minimum(c0 + 1, columns - 1)
        r1 = np.minimum(r0 + 1, rows - 1)
        value = (grid[r0, c0] * (1 - fc) * (1 - fr) + grid[r0, c1] * fc * (1 - fr)
                 + grid[r1, c0] * (1 - fc) * fr + grid[r1, c1] * fc * fr)
        return np.where(inside, value, np.nan)


def horizon_from_mosaics(latitude_deg: float, longitude_deg: float, site_elevation_m: float,
                         near: DemMosaic, far: DemMosaic, near_radius_m: float,
                         azimuths_deg: Sequence[float],
                         distances_m: Sequence[float]) -> np.ndarray:
    distances = np.asarray(distances_m, dtype=float)
    points = destination_points(latitude_deg, longitude_deg, azimuths_deg, distances)
    close = np.broadcast_to(distances[None, :] <= near_radius_m, points.shape[:2])
    elevations = np.where(close, near.sample(points[..., 0], points[..., 1]),
                          far.sample(points[..., 0], points[..., 1]))
    if not np.isfinite(elevations).all():
        raise WeatherError("the elevation tiles do not cover every horizon sample")
    return horizon_from_elevations(site_elevation_m, elevations, distances)


def terrain_obj(latitude_deg: float, longitude_deg: float, site_elevation_m: float,
                mosaic: DemMosaic, half_width_m: float = TERRAIN_HALF_WIDTH_M,
                points: int = TERRAIN_POINTS) -> str:
    if points < 2 or half_width_m <= 0.0:
        raise WeatherError("a terrain mesh needs at least two points and a positive width")
    offsets = np.linspace(-half_width_m, half_width_m, points)
    east, north = np.meshgrid(offsets, offsets)
    latitude = latitude_deg + np.degrees(north / EARTH_RADIUS_M)
    longitude = longitude_deg + np.degrees(
        east / (EARTH_RADIUS_M * math.cos(math.radians(latitude_deg))))
    heights = mosaic.sample(latitude, longitude) - site_elevation_m
    if not np.isfinite(heights).all():
        raise WeatherError("the elevation tiles do not cover the terrain square")
    lines = [f"v {x:.1f} {y:.1f} {z:.1f}"
             for x, y, z in zip(east.ravel(), north.ravel(), heights.ravel())]
    for row in range(points - 1):
        for column in range(points - 1):
            a = row * points + column + 1
            b = a + 1
            c = a + points
            d = c + 1
            lines.append(f"f {a} {c} {b}")
            lines.append(f"f {b} {c} {d}")
    return "\n".join(lines) + "\n"


def horizon_profile(site_elevation_m: float, elevations_m: np.ndarray | None,
                    distances_m: Sequence[float], source: SourceRef,
                    blocked_deg: np.ndarray | None = None) -> HorizonProfile:
    blocked = blocked_deg if blocked_deg is not None else horizon_from_elevations(
        site_elevation_m, elevations_m, distances_m)
    azimuths = horizon_azimuths_deg(len(blocked))
    return HorizonProfile(azimuths_deg=tuple(round(float(value), 3) for value in azimuths),
                          blocked_altitude_deg=tuple(round(float(value), 3)
                                                     for value in blocked),
                          source=source)


def ground_temperatures(monthly_air_c: Sequence[float], source: SourceRef,
                        depth_m: float = GROUND_DEPTH_M,
                        diffusivity_m2_day: float = SOIL_DIFFUSIVITY_M2_DAY
                        ) -> GroundTemperatures:
    values = np.asarray(monthly_air_c, dtype=float)
    if values.shape != (12,) or not np.isfinite(values).all():
        raise WeatherError("ground temperatures need twelve finite monthly air means")
    if depth_m <= 0.0 or diffusivity_m2_day <= 0.0:
        raise WeatherError("depth and soil diffusivity must be positive")
    mean = float(values.mean())
    amplitude = float(values.max() - values.min()) / 2.0
    coldest_day = float(MONTH_MID_DAYS[int(np.argmin(values))])
    damping = math.sqrt(math.pi / (DAYS_PER_YEAR * diffusivity_m2_day))
    lag_days = depth_m / 2.0 * math.sqrt(DAYS_PER_YEAR / (math.pi * diffusivity_m2_day))
    temperatures = mean - amplitude * math.exp(-depth_m * damping) * np.cos(
        2.0 * math.pi / DAYS_PER_YEAR * (MONTH_MID_DAYS - coldest_day - lag_days))
    return GroundTemperatures(monthly_c=tuple(round(float(value), 2) for value in temperatures),
                              depth_m=depth_m, source=source)


def climate_objective(heating_degree_days: float,
                      cooling_degree_days: float) -> ClimateObjective:
    if not (math.isfinite(heating_degree_days) and math.isfinite(cooling_degree_days)):
        raise WeatherError("degree days must be finite to classify a climate")
    if cooling_degree_days > heating_degree_days:
        return ClimateObjective.COOLING_DOMINATED
    return ClimateObjective.HEATING_DOMINATED


def components_from_wind(speed_m_s: np.ndarray,
                         direction_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    speed = np.asarray(speed_m_s, dtype=float)
    radians = np.radians(np.asarray(direction_deg, dtype=float))
    return -speed * np.sin(radians), -speed * np.cos(radians)


def wind_from_components(east_m_s: np.ndarray,
                         north_m_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    east = np.asarray(east_m_s, dtype=float)
    north = np.asarray(north_m_s, dtype=float)
    speed = np.hypot(east, north)
    direction = (np.degrees(np.arctan2(-east, -north)) + 360.0) % 360.0
    return speed, direction


def local_standard_hours(year: int) -> np.ndarray:
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    days = days[~((days.month == 2) & (days.day == 29))]
    hours = days.values[:, None] + np.arange(1, 25)[None, :] * np.timedelta64(1, "h")
    return hours.ravel().astype("datetime64[s]")


def resample_to_local_standard(utc_times: np.ndarray, columns: dict[str, np.ndarray],
                               year: int, utc_offset_h: float) -> dict[str, np.ndarray]:
    source = np.asarray(utc_times).astype("datetime64[s]").astype(np.int64)
    if source.ndim != 1 or len(source) < 2 or np.any(np.diff(source) <= 0):
        raise WeatherError("the hourly weather timestamps must be strictly increasing")
    if not -14.0 <= utc_offset_h <= 14.0:
        raise WeatherError("the time zone offset must lie between -14 and 14 hours")
    local = local_standard_hours(year).astype(np.int64)
    target = local - int(round(utc_offset_h * 3600.0))
    if target[0] < source[0] or target[-1] > source[-1]:
        raise WeatherError(
            "the hourly weather does not cover the whole local year once shifted by the "
            f"{utc_offset_h:+g} h time zone offset")
    resampled: dict[str, np.ndarray] = {}
    for name, values in columns.items():
        array = np.asarray(values, dtype=float)
        if array.shape != source.shape:
            raise WeatherError(f"{name} does not have one value per timestamp")
        if not np.isfinite(array).all():
            raise WeatherError(f"{name} contains missing values")
        resampled[name] = np.interp(target, source, array)
    return resampled
