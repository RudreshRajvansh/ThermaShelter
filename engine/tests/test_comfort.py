from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

import fixtures
from shelter_engine.comfort import (comfort_bounds_frame, evaluate_comfort,
                                    load_catalogue, running_mean_series)
from shelter_engine.errors import EngineError
from shelter_engine.schema import OccupancySpec, RunPeriod

PERIOD = RunPeriod(start=date(2019, 1, 1), end=date(2019, 1, 31), label="january")


def _index(days: int = 31) -> pd.DatetimeIndex:
    return pd.date_range("2019-01-01", periods=days * 24, freq="1h")


def _outdoor(days: int = 31, mean: float = -8.0, swing: float = 12.0) -> pd.Series:
    index = _index(days)
    hours = index.hour.to_numpy()
    return pd.Series(mean - 0.5 * swing * np.cos(2 * np.pi * (hours - 15) / 24.0),
                     index=index)


def _indoor(offset: float = 20.0, swing: float = 4.0,
            days: int = 31) -> pd.Series:
    index = _index(days)
    hours = index.hour.to_numpy()
    return pd.Series(offset - 0.5 * swing * np.cos(2 * np.pi * (hours - 18) / 24.0),
                     index=index)


def test_catalogue_loads_and_flags_pending_sources():
    catalogue = load_catalogue()
    assert catalogue.adaptive
    assert catalogue.fuel("diesel").lower_heating_value_kwh_per_unit > 0.0
    with pytest.raises(EngineError):
        catalogue.adaptive_model("nope")
    with pytest.raises(EngineError):
        catalogue.fuel("plutonium")


def test_comfort_model_bounds_are_clamped_to_applicability():
    model = fixtures.comfort_model()
    cold = model.neutral_temperature(-40.0)
    at_limit = model.neutral_temperature(13.0)
    assert cold == pytest.approx(at_limit)
    lower, upper = model.bounds(-40.0)
    assert upper - lower == pytest.approx(2 * model.band_half_width_k)


def test_absolute_bounds_win_over_the_regression():
    model = fixtures.comfort_model().model_copy(
        update={"absolute_lower_bound_c": 18.0})
    lower, _ = model.bounds(13.0)
    assert lower == 18.0


def test_empty_band_is_rejected():
    model = fixtures.comfort_model().model_copy(update={
        "absolute_lower_bound_c": 30.0, "absolute_upper_bound_c": 20.0})
    with pytest.raises(ValueError):
        model.bounds(20.0)


def test_running_mean_damps_and_lags_an_annual_cycle():
    index = pd.date_range("2019-01-01", periods=365, freq="1D")
    daily = pd.Series(6.0 - 14.0 * np.cos(2 * np.pi * (np.arange(365) - 15) / 365.0),
                      index=index)
    running = running_mean_series(daily, 0.8)
    assert len(running) == 365
    assert running.max() - running.min() < daily.max() - daily.min()
    assert running.idxmax() > daily.idxmax()
    assert running.mean() == pytest.approx(daily.mean(), abs=0.5)


def test_running_mean_is_stable_across_repeated_calls():
    index = pd.date_range("2019-01-01", periods=120, freq="1D")
    daily = pd.Series(np.linspace(-15.0, 5.0, 120), index=index)
    first = running_mean_series(daily, 0.8)
    second = running_mean_series(daily, 0.8)
    assert (first == second).all()


def test_running_mean_rejects_empty_input():
    with pytest.raises(EngineError):
        running_mean_series(pd.Series(dtype=float), 0.8)


def test_comfort_bounds_frame_is_aligned():
    outdoor = _outdoor()
    frame = comfort_bounds_frame(outdoor, fixtures.comfort_model())
    assert len(frame) == len(outdoor)
    assert (frame["upper_c"] > frame["lower_c"]).all()


def test_degree_hours_are_zero_inside_the_band():
    model = fixtures.comfort_model().model_copy(update={
        "absolute_lower_bound_c": 15.0, "absolute_upper_bound_c": 25.0})
    metrics = evaluate_comfort(_indoor(20.0, 1.0), _outdoor(), model, PERIOD)
    assert metrics.degree_hours_total_kh == pytest.approx(0.0)
    assert metrics.hours_outside_band == 0.0


def test_degree_hours_count_both_directions():
    model = fixtures.comfort_model().model_copy(update={
        "absolute_lower_bound_c": 18.0, "absolute_upper_bound_c": 22.0})
    metrics = evaluate_comfort(_indoor(20.0, 12.0), _outdoor(), model, PERIOD)
    assert metrics.degree_hours_below_kh > 0.0
    assert metrics.degree_hours_above_kh > 0.0
    assert metrics.degree_hours_total_kh == pytest.approx(
        metrics.degree_hours_below_kh + metrics.degree_hours_above_kh)


def test_cold_room_only_accumulates_below():
    model = fixtures.comfort_model().model_copy(update={
        "absolute_lower_bound_c": 18.0, "absolute_upper_bound_c": 30.0})
    metrics = evaluate_comfort(_indoor(8.0, 2.0), _outdoor(), model, PERIOD)
    assert metrics.degree_hours_above_kh == pytest.approx(0.0)
    assert metrics.degree_hours_below_kh > 0.0
    assert metrics.hours_below_band == metrics.hours_outside_band


def test_minimum_overnight_uses_january_night_hours():
    model = fixtures.comfort_model()
    indoor = _indoor(18.0, 8.0)
    metrics = evaluate_comfort(indoor, _outdoor(), model, PERIOD,
                               fixed_target=fixtures.fixed_target())
    assert metrics.minimum_overnight_month == 1
    assert metrics.minimum_overnight_operative_c is not None
    assert metrics.minimum_overnight_operative_c >= indoor.min() - 1e-9


def test_design_day_metrics_are_reported():
    model = fixtures.comfort_model()
    metrics = evaluate_comfort(_indoor(), _outdoor(), model, PERIOD,
                               design_day=date(2019, 1, 10))
    assert metrics.design_day_date == "2019-01-10"
    assert metrics.minimum_on_design_day_c is not None
    assert metrics.worst_night_margin_k is not None


def test_fixed_target_hours_are_counted():
    target = fixtures.fixed_target()
    metrics = evaluate_comfort(_indoor(15.0, 2.0), _outdoor(),
                               fixtures.comfort_model(), PERIOD,
                               fixed_target=target)
    assert metrics.fixed_target_id == target.id
    assert metrics.fixed_target_hours_below > 0.0


def test_unoccupied_case_reports_no_occupied_hours():
    metrics = evaluate_comfort(_indoor(5.0), _outdoor(), fixtures.comfort_model(),
                               PERIOD, occupancy=OccupancySpec(unoccupied=True))
    assert metrics.occupied_hours_outside_band == 0.0
    assert metrics.hours_outside_band > 0.0


def test_decrement_factor_and_time_lag_are_physical():
    outdoor = _outdoor(swing=20.0)
    indoor = _indoor(swing=4.0)
    metrics = evaluate_comfort(indoor, outdoor, fixtures.comfort_model(), PERIOD)
    assert metrics.decrement_factor == pytest.approx(0.2, abs=0.05)
    assert metrics.time_lag_h == pytest.approx(3.0, abs=1.0)


def test_non_overlapping_series_is_refused():
    indoor = _indoor()
    outdoor = _outdoor()
    outdoor.index = outdoor.index + pd.Timedelta(400, unit="D")
    with pytest.raises(EngineError):
        evaluate_comfort(indoor, outdoor, fixtures.comfort_model(), PERIOD)


def test_empty_series_is_refused():
    with pytest.raises(EngineError):
        evaluate_comfort(pd.Series(dtype=float, index=pd.DatetimeIndex([])),
                         _outdoor(), fixtures.comfort_model(), PERIOD)


def test_sub_hourly_input_is_resampled():
    index = pd.date_range("2019-01-01", periods=24 * 6 * 5, freq="10min")
    indoor = pd.Series(np.full(len(index), 19.0), index=index)
    outdoor = pd.Series(np.full(len(index), -10.0), index=index)
    metrics = evaluate_comfort(indoor, outdoor, fixtures.comfort_model(), PERIOD)
    assert metrics.mean_operative_c == pytest.approx(19.0)


def test_nan_values_do_not_propagate():
    indoor = _indoor()
    indoor.iloc[5:10] = np.nan
    metrics = evaluate_comfort(indoor, _outdoor(), fixtures.comfort_model(), PERIOD)
    assert np.isfinite(metrics.degree_hours_total_kh)
    assert np.isfinite(metrics.mean_operative_c)
