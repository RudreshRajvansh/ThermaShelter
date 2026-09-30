from __future__ import annotations

import math

import numpy as np
import pytest

from shelter_engine.errors import WeatherError
from shelter_engine.schema import ClimateObjective, SourceRef, SourceStatus
from shelter_engine.site_data import (EARTH_RADIUS_M, REFRACTION_COEFFICIENT, TILE_SIZE,
                                      DemMosaic, climate_objective, components_from_wind,
                                      destination_points, great_circle_km,
                                      ground_temperatures, horizon_azimuths_deg,
                                      horizon_distances_m, horizon_from_elevations,
                                      horizon_from_mosaics, horizon_profile,
                                      local_standard_hours, resample_to_local_standard,
                                      terrain_obj, tile_range, wind_from_components,
                                      world_pixels, zoom_for)
from shelter_engine.terrain import load_terrain_obj


def mosaic_for(latitude, longitude, radius_m, zoom, height):
    x0, x1, y0, y1 = tile_range(latitude, longitude, radius_m, zoom)
    rows = (y1 - y0 + 1) * TILE_SIZE
    columns = (x1 - x0 + 1) * TILE_SIZE
    row_index, column_index = np.mgrid[0:rows, 0:columns]
    world = 2 ** zoom * TILE_SIZE
    pixel_y = (y0 * TILE_SIZE + row_index + 0.5)
    pixel_x = (x0 * TILE_SIZE + column_index + 0.5)
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * pixel_y / world))))
    lon = pixel_x / world * 360.0 - 180.0
    return DemMosaic(zoom=zoom, tile_x0=x0, tile_y0=y0, heights_m=height(lat, lon))

SOURCE = SourceRef(citation="test", status=SourceStatus.DERIVED)


def test_destination_points_travel_the_requested_distance_and_bearing():
    distances = np.array([1000.0, 25000.0])
    points = destination_points(34.136, 77.547, [0.0, 90.0, 180.0, 270.0], distances)
    assert points.shape == (4, 2, 2)
    for row in points:
        for (lat, lon), distance in zip(row, distances):
            assert great_circle_km(34.136, 77.547, lat, lon) == pytest.approx(
                distance / 1000.0, rel=1e-6)
    assert points[0, 1, 0] > 34.136 and points[2, 1, 0] < 34.136
    assert points[1, 1, 1] > 77.547 and points[3, 1, 1] < 77.547


def test_destination_points_wrap_across_the_antimeridian():
    points = destination_points(0.0, 179.99, [90.0], [5000.0])
    assert -180.0 <= points[0, 0, 1] < -179.9


def test_a_flat_plain_has_no_horizon_and_a_ridge_blocks_its_angle():
    distances = horizon_distances_m(4, 1000.0, 20000.0)
    flat = np.full((2, 4), 3000.0)
    assert np.allclose(horizon_from_elevations(3000.0, flat, distances), 0.0)
    ridge = flat.copy()
    ridge[0, 2] = 4000.0
    blocked = horizon_from_elevations(3000.0, ridge, distances)
    effective = EARTH_RADIUS_M / (1.0 - REFRACTION_COEFFICIENT)
    d = distances[2]
    expected = math.degrees(math.atan2(1000.0 - d * d / (2.0 * effective), d))
    assert blocked[0] == pytest.approx(expected)
    assert blocked[1] == 0.0


def test_a_site_below_the_samples_never_reports_a_negative_or_vertical_horizon():
    distances = horizon_distances_m(3, 150.0, 1000.0)
    blocked = horizon_from_elevations(0.0, np.full((1, 3), 9000.0), distances)
    assert 0.0 < blocked[0] <= 89.9


@pytest.mark.parametrize("grid, distances", [
    (np.zeros((2, 3)), [1.0, 2.0]), (np.array([[0.0, float("nan")]]), [1.0, 2.0]),
    (np.zeros(3), [1.0, 2.0, 3.0])])
def test_horizon_input_errors_are_refused(grid, distances):
    with pytest.raises(WeatherError):
        horizon_from_elevations(0.0, grid, distances)


def test_horizon_sampling_parameters_are_checked():
    with pytest.raises(WeatherError):
        horizon_distances_m(1)
    with pytest.raises(WeatherError):
        horizon_distances_m(5, 1000.0, 10.0)
    with pytest.raises(WeatherError):
        horizon_azimuths_deg(1)
    with pytest.raises(WeatherError):
        destination_points(95.0, 0.0, [0.0], [1.0])


def test_horizon_profile_is_a_valid_engine_record():
    distances = horizon_distances_m()
    grid = np.full((36, len(distances)), 3000.0)
    grid[0, -5] = 5000.0
    profile = horizon_profile(3000.0, grid, distances, SOURCE)
    assert len(profile.azimuths_deg) == 36 and profile.azimuths_deg[9] == 90.0
    assert profile.blocked_altitude_deg[0] > 0.0
    assert profile.max_blocked_deg == profile.blocked_altitude_deg[0]


def test_world_pixels_match_the_web_mercator_tile_scheme():
    x, y = world_pixels(0.0, 0.0, 0)
    assert float(x) == pytest.approx(128.0) and float(y) == pytest.approx(128.0)
    x, y = world_pixels(34.136, 77.547, 12)
    assert int(float(x) // TILE_SIZE) == 2930 and int(float(y) // TILE_SIZE) == 1634


def test_tile_ranges_cover_the_radius_and_zoom_steps_down_to_stay_small():
    x0, x1, y0, y1 = tile_range(34.136, 77.547, 6000.0, 12)
    assert x1 >= x0 and y1 >= y0 and (x1 - x0 + 1) * (y1 - y0 + 1) <= 9
    assert zoom_for(34.136, 77.547, 50000.0, 12, max_tiles=16) <= 10
    with pytest.raises(WeatherError):
        tile_range(89.0, 0.0, 1000.0, 5)
    with pytest.raises(WeatherError):
        tile_range(10.0, 0.0, 0.0, 5)


def test_a_mosaic_samples_heights_bilinearly_and_leaves_outside_empty():
    mosaic = mosaic_for(34.136, 77.547, 3000.0, 12, lambda lat, lon: 1000.0 + 0.0 * lat)
    assert float(mosaic.sample(34.136, 77.547)) == pytest.approx(1000.0)
    assert np.isnan(mosaic.sample(40.0, 77.547))
    sloped = mosaic_for(34.136, 77.547, 3000.0, 12,
                        lambda lat, lon: 3000.0 + 100000.0 * (lat - 34.136))
    assert float(sloped.sample(34.146, 77.547)) == pytest.approx(4000.0, abs=15.0)


def test_horizon_from_tiles_sees_a_ridge_to_the_north_only():
    def height(lat, lon):
        return np.where(lat > 34.136 + 0.05, 4500.0, 3000.0)

    near = mosaic_for(34.136, 77.547, 6000.0, 12, height)
    far = mosaic_for(34.136, 77.547, 50000.0, 9, height)
    azimuths = horizon_azimuths_deg()
    distances = horizon_distances_m()
    blocked = horizon_from_mosaics(34.136, 77.547, 3000.0, near, far, 6000.0,
                                   azimuths, distances)
    assert blocked[0] > 10.0
    assert blocked[18] == 0.0
    with pytest.raises(WeatherError):
        horizon_from_mosaics(34.136, 77.547, 3000.0, near, near, 1e9, azimuths, distances)


def test_terrain_obj_is_read_back_as_a_site_relative_mesh(tmp_path):
    from shelter_engine.schema import SiteSpec

    mosaic = mosaic_for(34.136, 77.547, 8000.0, 12,
                        lambda lat, lon: 3000.0 + 20000.0 * (lat - 34.136))
    text = terrain_obj(34.136, 77.547, 3000.0, mosaic, half_width_m=5000.0, points=51)
    path = tmp_path / "site.obj"
    path.write_text(text, encoding="utf-8")
    site = SiteSpec(id="n34.13600_e77.54700", name="test", latitude_deg=34.136,
                    longitude_deg=77.547, elevation_m=3000.0, time_zone_h=5.5,
                    epw_path="weather/x.epw")
    mesh = load_terrain_obj(path, site)
    assert mesh is not None and mesh.vertex_count > 51 * 51
    points = np.asarray(mesh.positions).reshape(-1, 3)
    corner = points[(points[:, 0] == -5000.0) & (points[:, 1] == -5000.0)]
    original = [line for line in text.splitlines() if line.startswith("v -5000.0 -5000.0 ")]
    assert corner[0, 2] == pytest.approx(float(original[0].split()[3]))
    assert mesh.derived_from_horizon is False
    assert mesh.elevation_min_m < 0.0 < mesh.elevation_max_m
    with pytest.raises(WeatherError):
        terrain_obj(34.136, 77.547, 3000.0, mosaic, half_width_m=50000.0)
    with pytest.raises(WeatherError):
        terrain_obj(34.136, 77.547, 3000.0, mosaic, points=1)


def test_ground_temperature_keeps_the_mean_damps_the_swing_and_lags_the_air():
    air = [-8.0, -5.0, 1.0, 7.0, 12.0, 17.0, 20.0, 19.0, 14.0, 7.0, 0.0, -6.0]
    ground = ground_temperatures(air, SOURCE)
    values = np.asarray(ground.monthly_c)
    assert values.mean() == pytest.approx(np.mean(air), abs=0.2)
    assert values.max() - values.min() < max(air) - min(air)
    assert int(np.argmin(values)) == 0
    assert ground.depth_m == 0.5
    deep = np.asarray(ground_temperatures(air, SOURCE, depth_m=2.0).monthly_c)
    assert int(np.argmin(deep)) in (1, 2)
    assert deep.max() - deep.min() < values.max() - values.min()


def test_ground_temperature_follows_a_southern_hemisphere_winter():
    air = [22.0, 22.0, 20.0, 16.0, 12.0, 9.0, 8.0, 9.0, 12.0, 15.0, 18.0, 21.0]
    values = np.asarray(ground_temperatures(air, SOURCE).monthly_c)
    assert int(np.argmin(values)) in (6, 7)


@pytest.mark.parametrize("air, kwargs", [([1.0] * 11, {}), ([float("nan")] * 12, {}),
                                         ([1.0] * 12, {"depth_m": 0.0}),
                                         ([1.0] * 12, {"diffusivity_m2_day": -1.0})])
def test_ground_temperature_inputs_are_checked(air, kwargs):
    with pytest.raises(WeatherError):
        ground_temperatures(air, SOURCE, **kwargs)


def test_climate_objective_follows_the_larger_degree_day_total():
    assert climate_objective(5800.0, 12.0) is ClimateObjective.HEATING_DOMINATED
    assert climate_objective(300.0, 3100.0) is ClimateObjective.COOLING_DOMINATED
    assert climate_objective(0.0, 0.0) is ClimateObjective.HEATING_DOMINATED
    with pytest.raises(WeatherError):
        climate_objective(float("nan"), 1.0)


def test_local_standard_hours_are_hour_ending_and_skip_the_leap_day():
    hours = local_standard_hours(2024)
    assert len(hours) == 8760
    assert str(hours[0]) == "2024-01-01T01:00:00"
    assert str(hours[-1]) == "2025-01-01T00:00:00"
    leap = [str(value) for value in hours if str(value).startswith("2024-02-29")]
    assert leap == ["2024-02-29T00:00:00"]
    assert "2024-03-01T01:00:00" in {str(value) for value in hours}


def test_resampling_shifts_by_a_half_hour_offset_exactly():
    utc = np.arange(np.datetime64("2024-12-31T00:00"), np.datetime64("2026-01-02T00:00"),
                    np.timedelta64(1, "h")).astype("datetime64[s]")
    ramp = utc.astype(np.int64) / 3600.0
    shifted = resample_to_local_standard(utc, {"ramp": ramp}, 2025, 5.5)["ramp"]
    local = local_standard_hours(2025).astype(np.int64)
    assert len(shifted) == 8760
    assert np.allclose(shifted, (local - 5.5 * 3600) / 3600.0)


def test_resampling_refuses_short_or_broken_input():
    utc = np.arange(np.datetime64("2025-01-01T00:00"), np.datetime64("2025-12-31T00:00"),
                    np.timedelta64(1, "h")).astype("datetime64[s]")
    values = np.zeros(len(utc))
    with pytest.raises(WeatherError, match="cover"):
        resample_to_local_standard(utc, {"x": values}, 2025, 0.0)
    full = np.arange(np.datetime64("2024-12-31T00:00"), np.datetime64("2026-01-02T00:00"),
                     np.timedelta64(1, "h")).astype("datetime64[s]")
    broken = np.zeros(len(full))
    broken[10] = np.nan
    with pytest.raises(WeatherError, match="missing"):
        resample_to_local_standard(full, {"x": broken}, 2025, 0.0)
    with pytest.raises(WeatherError, match="one value"):
        resample_to_local_standard(full, {"x": broken[:-1]}, 2025, 0.0)
    with pytest.raises(WeatherError, match="between"):
        resample_to_local_standard(full, {"x": np.zeros(len(full))}, 2025, 20.0)
    with pytest.raises(WeatherError, match="increasing"):
        resample_to_local_standard(full[::-1], {"x": np.zeros(len(full))}, 2025, 0.0)


def test_wind_direction_is_interpolated_through_north_not_south():
    east, north = components_from_wind(np.array([4.0, 4.0]), np.array([350.0, 10.0]))
    speed, direction = wind_from_components(east.mean(keepdims=True),
                                            north.mean(keepdims=True))
    assert min(direction[0], 360.0 - direction[0]) < 1e-6
    back_speed, back_direction = wind_from_components(east, north)
    assert np.allclose(back_speed, 4.0)
    assert np.allclose(back_direction, [350.0, 10.0])


def test_the_shelter_sits_on_a_levelled_pad_on_sloping_ground(tmp_path):
    import math

    import fixtures
    from shelter_engine.terrain import PAD_BLEND_M, PAD_RADIUS_M, load_terrain_obj

    offsets = np.linspace(-5000.0, 5000.0, 51)
    east, north = np.meshgrid(offsets, offsets)
    rise = 0.2 * north
    lines = [f"v {x:.1f} {y:.1f} {z:.3f}" for x, y, z in zip(east.ravel(), north.ravel(),
                                                             rise.ravel())]
    for row in range(50):
        for column in range(50):
            a = row * 51 + column + 1
            lines += [f"f {a} {a + 51} {a + 1}", f"f {a + 1} {a + 51} {a + 52}"]
    path = tmp_path / "slope.obj"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mesh = load_terrain_obj(path, fixtures.site("x.epw"))
    points = np.asarray(mesh.positions).reshape(-1, 3)
    distance = np.hypot(points[:, 0], points[:, 1])
    assert np.allclose(points[distance <= PAD_RADIUS_M, 2], 0.0)
    far = distance >= PAD_BLEND_M
    assert np.allclose(points[far, 2], 0.2 * points[far, 1], atol=1e-6)
    assert mesh.pad_cut_fill_m == pytest.approx(0.2 * PAD_RADIUS_M, rel=0.05)
    assert mesh.ground_slope_deg == pytest.approx(math.degrees(math.atan(0.2)), abs=0.05)
    assert mesh.vertex_count <= 40000 and "levelled pad" in mesh.note
