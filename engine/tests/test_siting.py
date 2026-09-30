from __future__ import annotations

import math

import numpy as np
import pytest

import fixtures
from shelter_engine.errors import WeatherError
from shelter_engine.site_data import (EARTH_RADIUS_M, TILE_SIZE, DemMosaic, tile_range,
                                      world_pixels)
from shelter_engine.siting import blocked_at, siting_map, sun_hours, winter_days

NEAR_RADIUS_M = 6000.0


def mosaic(site, zoom: int, radius_m: float, ridge_north_m: float | None = None,
           ridge_height_m: float = 0.0) -> DemMosaic:
    x0, x1, y0, y1 = tile_range(site.latitude_deg, site.longitude_deg, radius_m, zoom)
    rows = (y1 - y0 + 1) * TILE_SIZE
    columns = (x1 - x0 + 1) * TILE_SIZE
    heights = np.full((rows, columns), 3500.0)
    if ridge_north_m is not None:
        latitude = site.latitude_deg + math.degrees(ridge_north_m / EARTH_RADIUS_M)
        _, y = world_pixels(latitude, site.longitude_deg, zoom)
        row = int(y - y0 * TILE_SIZE)
        heights[row:row + 6, :] += ridge_height_m
    return DemMosaic(zoom=zoom, tile_x0=x0, tile_y0=y0, heights_m=heights)


@pytest.fixture(scope="module")
def site():
    return fixtures.site("leh.epw").model_copy(update={"latitude_deg": 34.15,
                                                       "longitude_deg": 77.57})


def test_flat_ground_gives_every_cell_the_open_sky(site):
    near = mosaic(site, 12, NEAR_RADIUS_M)
    far = mosaic(site, 9, 50_000.0)
    result = siting_map(site, near, far, NEAR_RADIUS_M, half_width_m=500.0, step_m=250.0)
    assert result.columns == 5 and len(result.cells) == 25
    hours = {round(cell.winter_sun_h, 3) for cell in result.cells}
    assert len(hours) == 1
    assert result.cells[0].winter_sun_h == pytest.approx(result.flat_winter_sun_h)
    assert all(cell.slope_deg < 0.5 for cell in result.cells)
    assert 8.0 < result.flat_winter_sun_h < 12.0
    assert len(result.best) == 3 and result.site_cell == 12


def test_a_ridge_to_the_south_shades_the_cells_close_behind_it(site):
    near = mosaic(site, 12, NEAR_RADIUS_M, ridge_north_m=-900.0, ridge_height_m=600.0)
    far = mosaic(site, 9, 50_000.0)
    result = siting_map(site, near, far, NEAR_RADIUS_M, half_width_m=500.0, step_m=250.0)
    south_row = [cell for cell in result.cells if cell.north_m == -500.0]
    north_row = [cell for cell in result.cells if cell.north_m == 500.0]
    assert max(cell.winter_sun_h for cell in south_row) < \
        min(cell.winter_sun_h for cell in north_row)
    assert all(result.cells[index].north_m == 500.0 for index in result.best)
    assert all(cell.lost_to_terrain_h > 0.0 for cell in south_row)


def test_the_grid_is_bounded(site):
    near = mosaic(site, 12, NEAR_RADIUS_M)
    far = mosaic(site, 9, 50_000.0)
    with pytest.raises(WeatherError):
        siting_map(site, near, far, NEAR_RADIUS_M, half_width_m=5000.0, step_m=100.0)
    with pytest.raises(WeatherError):
        siting_map(site, near, far, NEAR_RADIUS_M, half_width_m=0.0)
    with pytest.raises(WeatherError, match="past the detailed"):
        siting_map(site, near, far, NEAR_RADIUS_M, half_width_m=5500.0, step_m=1000.0)


def test_sun_hours_follow_the_horizon():
    azimuths = np.arange(36) * 10.0
    altitude = np.array([[-5.0, 5.0, 15.0, 25.0]])
    azimuth = np.array([[180.0, 180.0, 180.0, 180.0]])
    assert sun_hours(azimuths, np.zeros(36), altitude, azimuth, 60)[0] == 3.0
    assert sun_hours(azimuths, np.full(36, 20.0), altitude, azimuth, 60)[0] == 1.0
    assert blocked_at(azimuths, azimuths / 10.0, np.array([355.0]))[0] == pytest.approx(17.5)
    days = winter_days(2019)
    assert days[0].month == 11 and days[-1] <= days[-1].replace(month=2, day=28)
