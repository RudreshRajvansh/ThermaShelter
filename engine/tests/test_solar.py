from __future__ import annotations

from datetime import date

import pytest

import fixtures
from shelter_engine.schema import HorizonProfile
from shelter_engine.solar import (daylight_lost_to_terrain_h, declination_deg,
                                  equation_of_time_minutes, horizon_altitude_deg,
                                  sun_path, sun_position, terrain_mesh)

LEH_LAT = 34.15
LEH_LON = 77.57
TZ = 5.5


def test_declination_hits_the_solstices_and_equinoxes():
    assert declination_deg(date(2019, 6, 21)) == pytest.approx(23.4, abs=0.3)
    assert declination_deg(date(2019, 12, 21)) == pytest.approx(-23.4, abs=0.3)
    assert declination_deg(date(2019, 3, 21)) == pytest.approx(0.0, abs=1.0)
    assert declination_deg(date(2019, 9, 23)) == pytest.approx(0.0, abs=1.0)


def test_equation_of_time_stays_inside_its_known_envelope():
    values = [equation_of_time_minutes(date(2019, month, 15)) for month in range(1, 13)]
    assert min(values) > -16.0
    assert max(values) < 17.0


def test_noon_altitude_matches_the_closed_form():
    for day in (date(2019, 1, 15), date(2019, 6, 21), date(2019, 9, 23)):
        path = sun_path(fixtures.site("x.epw"), day, step_minutes=5)
        expected = 90.0 - LEH_LAT + declination_deg(day)
        assert path.noon.altitude_deg == pytest.approx(expected, abs=0.6)


def test_sun_is_due_south_at_solar_noon():
    path = sun_path(fixtures.site("x.epw"), date(2019, 1, 15), step_minutes=5)
    assert path.noon.azimuth_deg == pytest.approx(180.0, abs=10.0)


def test_morning_sun_is_east_and_afternoon_sun_is_west():
    for hour in (7.0, 9.0, 11.0):
        _, azimuth = sun_position(LEH_LAT, LEH_LON, TZ, date(2019, 1, 15), hour)
        assert azimuth < 180.0
    for hour in (13.0, 15.0, 17.0):
        _, azimuth = sun_position(LEH_LAT, LEH_LON, TZ, date(2019, 1, 15), hour)
        assert azimuth > 180.0


def test_winter_sunrise_is_south_of_east_and_summer_north_of_east():
    winter = sun_path(fixtures.site("x.epw"), date(2019, 1, 15), step_minutes=5)
    summer = sun_path(fixtures.site("x.epw"), date(2019, 6, 21), step_minutes=5)
    winter_rise = [item for item in winter.positions if item.above_horizon][0]
    summer_rise = [item for item in summer.positions if item.above_horizon][0]
    assert winter_rise.azimuth_deg > 90.0
    assert summer_rise.azimuth_deg < 90.0


def test_summer_days_are_longer_than_winter_days():
    winter = sun_path(fixtures.site("x.epw"), date(2019, 1, 15), step_minutes=5)
    summer = sun_path(fixtures.site("x.epw"), date(2019, 6, 21), step_minutes=5)
    winter_length = winter.astronomical_sunset_hour - winter.astronomical_sunrise_hour
    summer_length = summer.astronomical_sunset_hour - summer.astronomical_sunrise_hour
    assert summer_length > winter_length + 3.0


def test_sun_is_below_the_horizon_at_midnight():
    altitude, _ = sun_position(LEH_LAT, LEH_LON, TZ, date(2019, 1, 15), 0.0)
    assert altitude < 0.0


def test_terrain_delays_sunrise_and_brings_sunset_forward():
    path = sun_path(fixtures.site("x.epw"), date(2019, 1, 15), step_minutes=5)
    assert path.has_horizon
    assert path.visible_sunrise_hour > path.astronomical_sunrise_hour
    assert path.visible_sunset_hour < path.astronomical_sunset_hour
    assert daylight_lost_to_terrain_h(path) > 0.0


def test_a_flat_horizon_loses_no_daylight():
    site = fixtures.site("x.epw")
    flat = site.model_copy(update={"horizon": HorizonProfile(
        azimuths_deg=tuple(float(index * 10) for index in range(36)),
        blocked_altitude_deg=tuple(0.0 for _ in range(36)),
        source=fixtures.SYNTHETIC)})
    path = sun_path(flat, date(2019, 1, 15), step_minutes=5)
    assert daylight_lost_to_terrain_h(path) == pytest.approx(0.0)


def test_site_without_a_horizon_reports_it():
    site = fixtures.site("x.epw").model_copy(update={"horizon": None})
    path = sun_path(site, date(2019, 1, 15))
    assert path.has_horizon is False
    assert path.visible_sunrise_hour == path.astronomical_sunrise_hour


def test_horizon_interpolation_wraps_around_north():
    profile = HorizonProfile(azimuths_deg=(0.0, 90.0, 180.0, 270.0),
                             blocked_altitude_deg=(10.0, 20.0, 30.0, 40.0),
                             source=fixtures.SYNTHETIC)
    assert horizon_altitude_deg(profile, 0.0) == pytest.approx(10.0)
    assert horizon_altitude_deg(profile, 90.0) == pytest.approx(20.0)
    assert horizon_altitude_deg(profile, 45.0) == pytest.approx(15.0)
    assert horizon_altitude_deg(profile, 315.0) == pytest.approx(25.0)
    assert horizon_altitude_deg(profile, 359.0) == pytest.approx(10.3, abs=0.3)


def test_horizon_is_zero_without_a_profile():
    assert horizon_altitude_deg(None, 123.0) == 0.0


def test_sun_path_step_must_divide_the_day():
    with pytest.raises(ValueError):
        sun_path(fixtures.site("x.epw"), date(2019, 1, 15), step_minutes=7)


def test_sun_path_positions_cover_the_whole_day():
    path = sun_path(fixtures.site("x.epw"), date(2019, 1, 15), step_minutes=60)
    assert len(path.positions) == 25
    assert path.positions[0].hour == 0.0
    assert path.positions[-1].hour == 24.0


def test_terrain_mesh_is_built_from_the_horizon():
    mesh = terrain_mesh(fixtures.site("x.epw"))
    assert mesh.derived_from_horizon
    assert len(mesh.positions) % 3 == 0
    assert len(mesh.indices) % 3 == 0
    assert max(mesh.indices) < len(mesh.positions) // 3
    assert mesh.ridge_max_m > 0.0


def test_terrain_ridge_height_follows_the_blocked_angle():
    site = fixtures.site("x.epw")
    steep = site.model_copy(update={"horizon": HorizonProfile(
        azimuths_deg=tuple(float(index * 10) for index in range(36)),
        blocked_altitude_deg=tuple(30.0 for _ in range(36)),
        source=fixtures.SYNTHETIC)})
    mesh = terrain_mesh(steep, outer_radius_m=100.0)
    assert mesh.ridge_max_m == pytest.approx(57.7, abs=0.5)


def test_terrain_without_a_horizon_says_so():
    site = fixtures.site("x.epw").model_copy(update={"horizon": None})
    mesh = terrain_mesh(site)
    assert mesh.derived_from_horizon is False
    assert mesh.positions == ()
    assert "flat horizon" in mesh.note
