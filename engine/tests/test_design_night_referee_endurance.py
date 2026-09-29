from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

import fixtures
from conftest import requires_energyplus
from shelter_engine.constructions import build_constructions
from shelter_engine.design_night import (design_night, gumbel_minima, nightly_minima,
                                         return_level_c, winter_minima, winter_of)
from shelter_engine.endurance import coast_down, cutoff_for, night_window, window_sum
from shelter_engine.errors import EngineError, WeatherError
from shelter_engine.geometry import build_geometry
from shelter_engine.idf import build_idf
from shelter_engine.referee import monthly_mean, referee, station_for
from shelter_engine.schema import RunOptions, RunPeriod


def synthetic_years(first: int, last: int, coldest: dict[int, float]) -> pd.Series:
    index = pd.date_range(f"{first}-07-01", f"{last}-06-30 23:00", freq="h")
    day = index.dayofyear.to_numpy()
    seasonal = -10.0 * np.cos(2.0 * np.pi * (day - 15) / 365.0)
    daily = 6.0 * np.cos(2.0 * np.pi * (index.hour.to_numpy() - 15) / 24.0)
    values = seasonal + daily
    series = pd.Series(values, index=index)
    for winter, value in coldest.items():
        moment = pd.Timestamp(f"{winter}-01-15 04:00")
        series.loc[moment] = value
    return series


def test_winter_runs_july_to_june():
    assert winter_of(date(2020, 12, 31)) == 2021
    assert winter_of(date(2021, 1, 1)) == 2021
    assert winter_of(date(2021, 6, 30)) == 2021
    assert winter_of(date(2021, 7, 1)) == 2022


def test_a_night_runs_from_six_in_the_evening_to_six_in_the_morning():
    index = pd.date_range("2021-01-10 12:00", "2021-01-11 12:00", freq="h")
    series = pd.Series(10.0, index=index)
    series.loc["2021-01-11 05:00"] = -20.0
    series.loc["2021-01-11 07:00"] = -40.0
    nights = nightly_minima(series)
    assert nights.loc["2021-01-10"] == -20.0


def test_incomplete_winters_are_left_out():
    series = synthetic_years(2018, 2021, {})
    series = series[series.index < "2020-09-01"]
    assert [item.winter for item in winter_minima(series)] == [2019, 2020]


def test_gumbel_return_level_is_colder_than_the_typical_winter():
    rng = np.random.default_rng(3)
    minima = -16.0 - 2.0 * rng.gumbel(size=40)
    location, scale = gumbel_minima(minima)
    assert scale > 0
    assert return_level_c(location, scale, 10.0) < np.median(minima)
    assert return_level_c(location, scale, 50.0) < return_level_c(location, scale, 10.0)


def test_gumbel_refuses_degenerate_data():
    with pytest.raises(WeatherError):
        gumbel_minima(np.array([-15.0]))
    with pytest.raises(WeatherError):
        gumbel_minima(np.array([-15.0, -15.0, -15.0]))
    with pytest.raises(WeatherError):
        return_level_c(-16.0, 2.0, 1.0)


def test_the_design_night_is_a_real_night_near_the_return_level():
    coldest = {2018: -18.0, 2019: -22.0, 2020: -17.0, 2021: -20.0, 2022: -19.0,
               2023: -25.0, 2024: -18.5}
    series = synthetic_years(2017, 2024, coldest)
    night = design_night(series, "synthetic")
    assert night.winter_count == 7
    assert night.night_of.month == 1 and night.night_of.day == 14
    assert abs(night.minimum_c - night.return_level_c) <= min(
        abs(value - night.return_level_c) for value in coldest.values()) + 1e-9
    assert any("extrapolation" in note for note in night.notes)


def test_the_design_night_reports_bias_against_observations():
    coldest = {2018: -18.0, 2019: -22.0, 2020: -17.0, 2021: -20.0, 2022: -19.0}
    series = synthetic_years(2017, 2022, coldest)
    observed = {winter: value + 2.0 for winter, value in coldest.items()}
    night = design_night(series, "synthetic", observed=observed)
    assert any("-2.0 K" in note for note in night.notes)


def test_too_few_winters_is_refused():
    with pytest.raises(WeatherError, match="complete winters"):
        design_night(synthetic_years(2020, 2022, {}), "synthetic")


STATION = {"station": "Test", "latitude_deg": 34.15, "longitude_deg": 77.57,
           "match_radius_km": 15.0,
           "months": {"1": {"mean_daily_minimum_c": -13.0, "mean_daily_maximum_c": 2.0,
                            "record_low_c": -28.0, "record_low_date": "1899-01-11"}}}


def january(low: float, high: float) -> pd.DataFrame:
    index = pd.date_range("2019-01-01", "2019-01-31 23:00", freq="h")
    shape = np.cos(2.0 * np.pi * (index.hour.to_numpy() - 15) / 24.0)
    dry = (low + high) / 2.0 + (high - low) / 2.0 * shape
    ghi = np.clip(np.sin(np.pi * (index.hour.to_numpy() - 7) / 10.0), 0.0, None) * 600.0
    return pd.DataFrame({"dry_bulb_c": dry, "global_horizontal_wh_m2": ghi}, index=index)


def test_station_matching_uses_the_radius():
    assert station_for(34.14, 77.55, [STATION])[0] is STATION
    assert station_for(34.60, 77.55, [STATION]) == (None, None)


def test_a_file_with_nights_too_cold_is_flagged():
    report = referee(january(-25.0, 2.0), 34.14, 77.55, [STATION])
    assert report.night_minimum[0].difference == pytest.approx(-12.0, abs=0.1)
    assert any("colder than Test" in flag for flag in report.flags)


def test_a_file_colder_than_the_record_is_flagged():
    report = referee(january(-35.0, -20.0), 34.14, 77.55, [STATION])
    assert report.hours_below_record_low > 0
    assert any("ever recorded" in flag for flag in report.flags)


def test_a_matching_file_raises_no_temperature_flag():
    report = referee(january(-13.0, 2.0), 34.14, 77.55, [STATION])
    assert not report.flags


def test_too_much_sun_is_flagged_and_a_factor_offered():
    frame = january(-13.0, 2.0)
    measured = frame["global_horizontal_wh_m2"].groupby(
        frame.index.normalize()).sum().mean() / 1000.0
    report = referee(frame, 34.14, 77.55, [STATION], {1: measured / 1.3}, "reference")
    assert report.solar[0].ratio == pytest.approx(1.3)
    assert report.solar_factors[1] == pytest.approx(1 / 1.3, rel=1e-3)
    assert any("sunshine" in flag for flag in report.flags)


def test_a_site_far_from_any_station_says_so():
    report = referee(january(-13.0, 2.0), 30.0, 70.0, [STATION])
    assert report.station is None and report.notes


def test_power_monthly_values_skip_the_annual_column_and_missing_flags():
    values = {"201101": 3.0, "201201": 5.0, "201113": 99.0, "201102": -999.0}
    assert monthly_mean(values) == {1: 4.0}


def test_cutoff_and_night_window():
    assert cutoff_for(date(2025, 1, 16)) == datetime(2025, 1, 16, 18)
    start, end = night_window(date(2025, 12, 31))
    assert end == datetime(2026, 1, 1, 6)
    with pytest.raises(EngineError):
        cutoff_for(date(2025, 1, 16), 24)


def test_coast_down_counts_hours_until_the_floor():
    index = pd.date_range("2019-01-16 12:00", periods=24, freq="h")
    series = pd.Series(np.linspace(14.0, 2.5, 24), index=index)
    reached, hours, start, lowest, _, span = coast_down(series,
                                                         datetime(2019, 1, 16, 18), 5.0)
    assert reached and hours == pytest.approx(13.0)
    assert start == pytest.approx(series.iloc[6])
    assert lowest == pytest.approx(2.5)


def test_coast_down_that_never_reaches_the_floor_reports_the_whole_span():
    index = pd.date_range("2019-01-16 12:00", periods=24, freq="h")
    series = pd.Series(10.0, index=index)
    reached, hours, *_, span = coast_down(series, datetime(2019, 1, 16, 18), 5.0)
    assert not reached and hours == span == pytest.approx(18.0)


def test_coast_down_after_the_run_is_an_error():
    index = pd.date_range("2019-01-16 00:00", periods=6, freq="h")
    with pytest.raises(EngineError):
        coast_down(pd.Series(10.0, index=index), datetime(2019, 1, 16, 18))


def test_window_sum_is_half_open():
    index = pd.date_range("2019-01-16 17:00", periods=4, freq="h")
    series = pd.Series([1.0, 2.0, 3.0, 4.0], index=index)
    assert window_sum(series, datetime(2019, 1, 16, 18),
                      datetime(2019, 1, 16, 20)) == 5.0


def test_leftover_heat_turns_the_night_into_him_tapak_hours():
    from shelter_engine.leftover_heat import leftover_heat
    fuel = fixtures.fuel_model().model_copy(update={"fuel": "kerosene"})
    spare = leftover_heat(30.0, 2500.0, fuel)
    litres = 30.0 / (fuel.lower_heating_value_kwh_per_unit * fuel.appliance_efficiency)
    assert spare.litres == pytest.approx(litres)
    assert spare.him_tapak_hours_low == pytest.approx(litres / 0.7)
    assert spare.him_tapak_hours_high == pytest.approx(litres / 0.5)
    assert spare.solar_storage_kwh == 30.0
    assert not spare.notes


def test_leftover_heat_warns_when_one_heater_cannot_keep_up():
    from shelter_engine.leftover_heat import leftover_heat
    fuel = fixtures.fuel_model().model_copy(update={"fuel": "kerosene"})
    assert any("cannot hold" in note for note in leftover_heat(30.0, 9000.0, fuel).notes)


def test_leftover_heat_with_no_heat_needed_and_no_fuel():
    from shelter_engine.leftover_heat import leftover_heat
    spare = leftover_heat(-2.0, None, None)
    assert spare.night_heating_kwh == 0.0 and spare.litres is None
    assert any("needs no heat" in note for note in spare.notes)


def test_a_cutoff_outside_the_run_is_refused():
    period = RunPeriod(start=date(2019, 1, 10), end=date(2019, 1, 16))
    with pytest.raises(ValueError):
        RunOptions(period=period, heating_cutoff=datetime(2019, 1, 20, 18))


@requires_energyplus
def test_the_heater_cutoff_schedule_turns_heating_off_for_good(site, library):
    design = fixtures.design()
    constructions = build_constructions(design, library)
    model = build_geometry(design, constructions.names, design_hash="t",
                           horizon=site.horizon)
    period = RunPeriod(start=date(2019, 1, 10), end=date(2019, 1, 18))
    options = RunOptions(period=period, surface_output=False,
                         heating_cutoff=datetime(2019, 1, 16, 18))
    text = build_idf(design, site, model, constructions, options, conditioned=True).text
    block = next(b for b in text.split("\n\n")
                 if b.startswith("SCHEDULE:COMPACT,") and "HEATING_SETPOINT" in b)
    assert "Through: 1/15" in block and "Through: 1/16" in block
    assert "Through: 12/31" in block
    tail = block.split("Through: 12/31")[1]
    assert "-60" in tail and "14" not in tail
