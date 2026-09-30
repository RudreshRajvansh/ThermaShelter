from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

import fixtures
from shelter_engine.commander import (TARGET_C, commander_card, nominal_index,
                                      splice_forecast, terrain_sunset)
from shelter_engine.errors import WeatherError
from shelter_engine.schema import Arrangement, HorizonProfile, SourceRef


def base_frame() -> pd.DataFrame:
    index = pd.date_range("2019-01-01 01:00", periods=8760, freq="h")
    return pd.DataFrame({
        "dry_bulb_c": np.zeros(8760), "dew_point_c": np.full(8760, -5.0),
        "relative_humidity_pct": np.full(8760, 50.0), "pressure_pa": np.full(8760, 68000.0),
        "global_horizontal_wh_m2": np.zeros(8760), "direct_normal_wh_m2": np.zeros(8760),
        "diffuse_horizontal_wh_m2": np.zeros(8760), "wind_speed_m_s": np.full(8760, 2.0),
        "wind_direction_deg": np.zeros(8760), "total_sky_cover": np.full(8760, 3.0)},
        index=index)


def test_the_forecast_replaces_only_its_own_days():
    utc = np.arange(np.datetime64("2026-12-10T00:00"), np.datetime64("2026-12-14T00:00"),
                    np.timedelta64(1, "h")).astype("datetime64[s]")
    values = {"dry_bulb_c": np.linspace(-20.0, -10.0, len(utc)),
              "dew_point_c": np.full(len(utc), 5.0), "sky_cover_tenths": np.full(len(utc), 9.4)}
    spliced = splice_forecast(base_frame(), utc, values, 5.5, date(2026, 12, 11),
                              date(2026, 12, 12))
    first = nominal_index(date(2026, 12, 11), 1)
    assert spliced["dry_bulb_c"][first] < -10.0
    assert spliced["dry_bulb_c"][first - 1] == 0.0
    assert spliced["dry_bulb_c"][nominal_index(date(2026, 12, 13), 1)] == 0.0
    assert spliced["dew_point_c"][first] <= spliced["dry_bulb_c"][first]
    assert spliced["sky_cover_tenths"][first] == 9.0
    with pytest.raises(WeatherError, match="does not reach"):
        splice_forecast(base_frame(), utc, values, 5.5, date(2026, 12, 11), date(2026, 12, 14))
    with pytest.raises(WeatherError):
        splice_forecast(base_frame().iloc[:100], utc, values, 5.5, date(2026, 12, 11),
                        date(2026, 12, 12))
    with pytest.raises(WeatherError):
        nominal_index(date(2028, 2, 29), 1)
    assert nominal_index(date(2026, 1, 1), 1) == 0
    assert nominal_index(date(2026, 12, 31), 24) == 8759


def test_a_western_ridge_brings_sunset_forward():
    site = fixtures.site("leh.epw")
    flat = site.model_copy(update={"horizon": None})
    ridge = HorizonProfile(azimuths_deg=tuple(float(value) for value in range(0, 360, 10)),
                           blocked_altitude_deg=tuple(20.0 if 200 <= value <= 300 else 0.0
                                                      for value in range(0, 360, 10)),
                           source=SourceRef(citation="test"))
    shaded = site.model_copy(update={"horizon": ridge})
    open_sky = terrain_sunset(flat, date(2026, 12, 14))
    early = terrain_sunset(shaded, date(2026, 12, 14))
    assert open_sky is not None and early is not None
    assert 16 <= open_sky.hour <= 18
    assert (open_sky - early).total_seconds() > 3600


def night_series(night_of: date, values) -> pd.Series:
    index = pd.date_range(datetime.combine(night_of, datetime.min.time()), periods=48, freq="h")
    return pd.Series(values, index=index, dtype=float)


def card(night_of, operative, heating_w, stock, lang="en", design=None, sunspace=None,
         room=None):
    design = design or fixtures.design()
    site = fixtures.site("leh.epw")
    return commander_card(
        post_id="P-17", post_name="Post 17", design=design, site=site, night_of=night_of,
        issued_at=datetime(2026, 12, 14, 18, 5), lang=lang, people=9,
        operative_c=night_series(night_of, operative),
        room_air_c=room if room is not None else night_series(night_of, operative),
        outdoor_c=night_series(night_of, np.full(48, -20.0)),
        sunspace_air_c=sunspace, heating_j=night_series(night_of, np.full(48, heating_w * 3600)),
        fuel=fixtures.fuel_model(), stock_units=stock, forecast_source="forecast",
        forecast_age_h=0.2, model_error_k=1.8, calibrated_on=date(2026, 12, 12))


def test_a_cold_night_orders_the_bukhari_at_the_crossing_and_warns_on_fuel():
    night_of = date(2026, 12, 14)
    operative = np.concatenate([np.full(20, 16.0), np.full(28, 8.0)])
    result = card(night_of, operative, 1500.0, stock=3.0)
    keys = [order.key for order in result.orders]
    assert "close_shutters" in keys and "fresh_air" in keys and "light_bukhari" in keys
    bukhari = next(order for order in result.orders if order.key == "light_bukhari")
    assert bukhari.time == "20:00"
    assert result.inside_min_c == 8.0 and result.hours_below_target == 10.0
    assert result.fuel_tonight_units > 0.0
    assert result.status in ("amber", "red")
    assert "fuel_low" in keys and result.fuel_out_hours is not None
    assert result.safety and "CO alarm" in result.safety[0]
    fresh = next(order for order in result.orders if order.key == "fresh_air")
    assert "never close every vent" in fresh.action


def test_a_warm_night_needs_no_bukhari_and_stays_green():
    result = card(date(2026, 12, 14), np.full(48, 18.0), 0.0, stock=40.0)
    keys = [order.key for order in result.orders]
    assert "no_bukhari" in keys and "light_bukhari" not in keys
    assert result.status == "green" and result.fuel_tonight_units == 0.0
    assert result.fuel_out_hours is None


def test_running_out_tonight_is_red_and_hindi_is_written_in_hindi():
    result = card(date(2026, 12, 14), np.full(48, 12.0), 3000.0, stock=0.1, lang="hi")
    assert result.status == "red"
    assert all(any("\u0900" <= ch <= "\u097f" for ch in order.action)
               for order in result.orders)
    assert "CO" in result.safety[0]
    with pytest.raises(ValueError):
        card(date(2026, 12, 14), np.full(48, 12.0), 0.0, stock=None, lang="fr")


def test_the_sunspace_door_closes_after_its_last_warm_hour():
    night_of = date(2026, 12, 14)
    design = fixtures.design(Arrangement.SUNSPACE)
    room = night_series(night_of, np.full(48, 15.0))
    warm = np.full(48, 10.0)
    warm[10:15] = 20.0
    result = card(night_of, np.full(48, 15.0), 0.0, 10.0, design=design,
                  sunspace=night_series(night_of, warm), room=room)
    door = next(order for order in result.orders if order.key == "close_sunspace_door")
    assert door.time == "15:00"
    cold = card(night_of, np.full(48, 15.0), 0.0, 10.0, design=design,
                sunspace=night_series(night_of, np.full(48, 5.0)), room=room)
    assert any(order.key == "keep_sunspace_closed" for order in cold.orders)
    assert TARGET_C == 14.0
