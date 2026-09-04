from __future__ import annotations

from datetime import date

import pytest

import fixtures
from shelter_engine.errors import WeatherError
from shelter_engine.schema import RunPeriod, Severity
from shelter_engine.weather import (air_density_kg_m3, dew_point_c, load_epw,
                                    saturation_pressure_pa, standard_pressure_pa)
from synthetic_epw import write_synthetic_epw


def test_standard_pressure_falls_with_altitude():
    assert standard_pressure_pa(0.0) == pytest.approx(101325.0)
    leh = standard_pressure_pa(3500.0)
    assert 64000.0 < leh < 68000.0
    assert leh < standard_pressure_pa(1000.0) < standard_pressure_pa(0.0)


def test_standard_pressure_rejects_impossible_elevations():
    with pytest.raises(WeatherError):
        standard_pressure_pa(20000.0)
    with pytest.raises(WeatherError):
        standard_pressure_pa(-1000.0)


def test_air_density_drops_with_pressure():
    sea_level = air_density_kg_m3(101325.0, 0.0)
    altitude = air_density_kg_m3(standard_pressure_pa(3500.0), 0.0)
    assert sea_level == pytest.approx(1.29, abs=0.02)
    assert altitude < 0.9 * sea_level


def test_dew_point_is_below_dry_bulb():
    assert float(dew_point_c(20.0, 50.0)) < 20.0
    assert float(dew_point_c(20.0, 100.0)) == pytest.approx(20.0, abs=0.1)
    assert float(dew_point_c(-10.0, 60.0)) < -10.0


def test_saturation_pressure_increases_with_temperature():
    assert float(saturation_pressure_pa(0.0)) < float(saturation_pressure_pa(20.0))


def test_epw_loads_with_no_warnings(epw, site):
    weather = load_epw(epw, site)
    assert weather.hours == 8760
    assert not weather.warnings
    assert weather.annual_mean_c() == pytest.approx(6.0, abs=0.5)


def test_epw_slice_covers_the_period(epw, site):
    weather = load_epw(epw, site)
    window = weather.slice(RunPeriod(start=date(2019, 1, 1), end=date(2019, 1, 31)))
    assert len(window) == 31 * 24


def test_epw_slice_outside_the_year_raises(epw, site):
    weather = load_epw(epw, site)
    weather.frame = weather.frame.iloc[: 24 * 40]
    with pytest.raises(WeatherError):
        weather.slice(RunPeriod(start=date(2019, 6, 1), end=date(2019, 6, 2)))


def test_pressure_mismatch_is_reported_as_severe(tmp_path, site):
    path = write_synthetic_epw(tmp_path / "sea_level.epw", elevation_m=10.0)
    weather = load_epw(path, fixtures.site(str(path)))
    codes = {item.code: item.severity for item in weather.warnings}
    assert codes["weather_pressure_mismatch"] is Severity.SEVERE


def test_timezone_mismatch_is_reported_as_severe(tmp_path):
    path = write_synthetic_epw(tmp_path / "wrong_tz.epw", time_zone_h=8.0)
    weather = load_epw(path, fixtures.site(str(path)))
    codes = {item.code: item.severity for item in weather.warnings}
    assert codes["weather_timezone_mismatch"] is Severity.SEVERE


def test_missing_dry_bulb_sentinels_are_flagged(tmp_path, site):
    path = write_synthetic_epw(tmp_path / "gappy.epw")
    lines = path.read_text(encoding="latin-1").splitlines()
    for index in range(8, len(lines), 40):
        parts = lines[index].split(",")
        parts[6] = "99.9"
        lines[index] = ",".join(parts)
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")
    weather = load_epw(path, fixtures.site(str(path)))
    flagged = [item for item in weather.warnings
               if item.code == "weather_missing_values"]
    assert flagged
    assert any(item.severity is Severity.SEVERE for item in flagged)


def test_unfillable_gap_is_refused(tmp_path):
    path = write_synthetic_epw(tmp_path / "big_gap.epw")
    lines = path.read_text(encoding="latin-1").splitlines()
    for index in range(8, 8 + 200):
        parts = lines[index].split(",")
        parts[6] = "99.9"
        lines[index] = ",".join(parts)
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")
    with pytest.raises(WeatherError, match="unusable after interpolation"):
        load_epw(path, fixtures.site(str(path)))


def test_truncated_file_is_reported(tmp_path):
    path = write_synthetic_epw(tmp_path / "short.epw")
    lines = path.read_text(encoding="latin-1").splitlines()
    path.write_text("\n".join(lines[: 8 + 24 * 30]) + "\n", encoding="latin-1")
    weather = load_epw(path, fixtures.site(str(path)))
    assert any(item.code == "weather_row_count" and item.severity is Severity.SEVERE
               for item in weather.warnings)


def test_missing_file_raises():
    with pytest.raises(WeatherError):
        load_epw("nowhere/at/all.epw")


def test_non_epw_file_raises(tmp_path):
    path = tmp_path / "not_weather.epw"
    path.write_text("this is not a weather file\n" * 20, encoding="latin-1")
    with pytest.raises(WeatherError):
        load_epw(path)


def test_running_mean_lags_the_daily_mean(epw, site):
    weather = load_epw(epw, site)
    daily = weather.daily_mean()
    running = weather.running_mean_outdoor_c()
    assert len(running) == len(daily)
    assert running.min() > daily.min()
    assert running.max() < daily.max()


def test_coldest_and_hottest_days_are_in_the_right_season(epw, site):
    weather = load_epw(epw, site)
    assert weather.coldest_day().month in (12, 1, 2)
    assert weather.hottest_day().month in (6, 7, 8)


def test_degree_days_are_positive_for_a_cold_year(epw, site):
    weather = load_epw(epw, site)
    assert weather.heating_degree_days() > weather.cooling_degree_days()


def test_leap_year_file_is_indexed_correctly(tmp_path):
    path = write_synthetic_epw(tmp_path / "leap.epw", year=2020)
    weather = load_epw(path)
    assert weather.hours == 8784
    assert any(stamp.month == 2 and stamp.day == 29 for stamp in weather.frame.index)
