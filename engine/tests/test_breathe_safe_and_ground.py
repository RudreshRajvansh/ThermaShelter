from __future__ import annotations

import math
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest

import fixtures
from conftest import requires_energyplus
from shelter_engine.air_quality import (CO2_M3_S_PER_W_REFERENCE, OUTDOOR_CO2_PPM,
                                        SEA_LEVEL_PRESSURE_KPA, co2_rate_m3_s_per_w,
                                        size_fresh_air, standard_pressure_kpa,
                                        steady_state_ppm)
from shelter_engine.comfort import (comfort_bounds_frame, evaluate_comfort,
                                    load_catalogue, trailing_mean_series)
from shelter_engine.constructions import build_constructions
from shelter_engine.geometry import Polygon, build_geometry
from shelter_engine.idf import build_idf, exposed_perimeter_m
from shelter_engine.schema import (Arrangement, ArrangementParams, RunOptions,
                                   RunPeriod, SurfaceKind, VentilationSpec)

WEEK = RunPeriod(start=date(2019, 1, 10), end=date(2019, 1, 16), label="week")


def test_standard_pressure_is_sea_level_at_zero_and_falls_with_height():
    assert standard_pressure_kpa(0.0) == pytest.approx(SEA_LEVEL_PRESSURE_KPA)
    assert standard_pressure_kpa(3256.0) == pytest.approx(67.9, abs=0.1)
    heights = [0.0, 1000.0, 3000.0, 5000.0, 8000.0]
    pressures = [standard_pressure_kpa(value) for value in heights]
    assert all(a > b for a, b in zip(pressures, pressures[1:]))


def test_standard_pressure_refuses_nonsense():
    with pytest.raises(ValueError):
        standard_pressure_kpa(math.nan)
    with pytest.raises(ValueError):
        standard_pressure_kpa(60_000.0)


def test_persily_rate_agrees_with_the_energyplus_default_at_sea_level():
    assert CO2_M3_S_PER_W_REFERENCE == pytest.approx(4.147e-8, rel=1e-3)
    assert CO2_M3_S_PER_W_REFERENCE == pytest.approx(3.82e-8, rel=0.10)


def test_co2_volume_grows_inversely_with_pressure():
    sea = co2_rate_m3_s_per_w(SEA_LEVEL_PRESSURE_KPA)
    leh = co2_rate_m3_s_per_w(67.9)
    assert leh / sea == pytest.approx(SEA_LEVEL_PRESSURE_KPA / 67.9, rel=1e-9)


def test_co2_rate_refuses_impossible_conditions():
    with pytest.raises(ValueError):
        co2_rate_m3_s_per_w(0.0)
    with pytest.raises(ValueError):
        co2_rate_m3_s_per_w(50.0, room_c=-300.0)


def test_fresh_air_at_leh_is_about_one_and_a_half_times_sea_level():
    sizing = size_fresh_air(3256.0, 110.0)
    assert sizing.fresh_air_l_s_per_person == pytest.approx(12.6, abs=0.1)
    assert sizing.altitude_factor == pytest.approx(SEA_LEVEL_PRESSURE_KPA
                                                   / sizing.site_pressure_kpa)


def test_fresh_air_holds_the_limit_in_steady_state():
    sizing = size_fresh_air(3256.0, 90.0, limit_ppm=1200.0)
    people = 9
    ppm = steady_state_ppm(people, sizing.co2_l_s_per_person,
                           people * sizing.fresh_air_l_s_per_person)
    assert ppm == pytest.approx(1200.0)


def test_a_limit_at_or_below_outdoor_cannot_be_held():
    with pytest.raises(ValueError, match="no amount of fresh air"):
        size_fresh_air(3256.0, 100.0, limit_ppm=OUTDOOR_CO2_PPM)


def test_fresh_air_needs_a_metabolic_rate():
    with pytest.raises(ValueError):
        size_fresh_air(3256.0, 0.0)


def test_steady_state_edge_cases():
    assert steady_state_ppm(0, 0.007, 50.0) == OUTDOOR_CO2_PPM
    assert steady_state_ppm(9, 0.007, 0.0) == math.inf


def test_trailing_mean_wraps_the_year_for_early_january():
    index = pd.date_range("2019-01-01", periods=365, freq="D")
    values = pd.Series(np.where(index.month == 12, 10.0, 0.0), index=index)
    running = trailing_mean_series(values, 30)
    assert running.iloc[0] == pytest.approx(10.0)
    assert running.iloc[40] == pytest.approx(0.0)


def test_trailing_mean_with_a_short_series_uses_what_there_is():
    index = pd.date_range("2019-01-01", periods=5, freq="D")
    running = trailing_mean_series(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=index), 30)
    assert running.iloc[-1] == pytest.approx(3.0)


def test_the_drdo_band_is_fourteen_to_twenty_whatever_the_weather():
    model = load_catalogue().adaptive_model("drdo_tot_band")
    assert model.is_fixed_band
    index = pd.date_range("2019-01-01", periods=48, freq="h")
    for outdoor in (-30.0, 0.0, 35.0):
        frame = comfort_bounds_frame(pd.Series(outdoor, index=index), model)
        assert frame["lower_c"].unique().tolist() == [14.0]
        assert frame["upper_c"].unique().tolist() == [20.0]
        assert not frame["clamped"].any()


def test_imac_reports_every_hour_it_had_to_clamp():
    model = load_catalogue().adaptive_model("imac_2016_nv")
    index = pd.date_range("2019-01-10", periods=72, freq="h")
    outdoor = pd.Series(-12.0, index=index)
    history = pd.Series(-12.0, index=pd.date_range("2019-01-01", periods=24 * 365,
                                                   freq="h"))
    operative = pd.Series(5.0, index=index)
    metrics = evaluate_comfort(operative, outdoor, model, WEEK,
                               outdoor_history_c=history)
    assert metrics.hours_outside_applicability == 72
    assert metrics.running_mean_min_c == pytest.approx(-12.0)
    assert metrics.band_lower_c == pytest.approx(model.bounds(-12.0)[0])


def test_the_drdo_band_is_the_default_and_verified():
    catalogue = load_catalogue()
    assert catalogue.adaptive[0].id == "drdo_tot_band"
    assert catalogue.adaptive[0].source.status.value == "verified"
    night = catalogue.fixed_target("drdo_band_night")
    assert night.lower_bound_c == 14.0 and night.hours[0] == 18


def _floor(name: str, x0: float, y0: float, x1: float, y1: float) -> Polygon:
    return Polygon(name=name, zone=name, kind=SurfaceKind.FLOOR,
                   vertices=[(x0, y1, 0.0), (x1, y1, 0.0), (x1, y0, 0.0), (x0, y0, 0.0)],
                   construction="F", outside_boundary="Ground")


def test_a_lone_floor_exposes_its_whole_perimeter():
    floor = _floor("A", 0, 0, 8, 5)
    assert exposed_perimeter_m(floor, [floor]) == pytest.approx(26.0)


def test_a_shared_edge_is_not_exposed_on_either_side():
    room = _floor("ROOM", 0, 2, 8, 7)
    sunspace = _floor("SUN", 0, 0, 8, 2)
    assert exposed_perimeter_m(room, [room, sunspace]) == pytest.approx(26.0 - 8.0)
    assert exposed_perimeter_m(sunspace, [room, sunspace]) == pytest.approx(20.0 - 8.0)


def test_a_partly_shared_edge_only_hides_the_overlap():
    room = _floor("ROOM", 0, 2, 8, 7)
    vestibule = _floor("VEST", 3, 0, 5, 2)
    assert exposed_perimeter_m(room, [room, vestibule]) == pytest.approx(26.0 - 2.0)


def test_floors_that_only_touch_at_a_corner_share_nothing():
    a = _floor("A", 0, 0, 2, 2)
    b = _floor("B", 2, 2, 4, 4)
    assert exposed_perimeter_m(a, [a, b]) == pytest.approx(8.0)


def _idf(site, library, design, **options):
    constructions = build_constructions(design, library)
    model = build_geometry(design, constructions.names, design_hash="t",
                           horizon=site.horizon)
    run = RunOptions(period=WEEK, surface_output=False, **options)
    return build_idf(design, site, model, constructions, run,
                     conditioned=options.pop("conditioned", True))


def _field(text: str, object_type: str, label: str) -> list[str]:
    values = []
    for block in text.split("\n\n"):
        if not block.upper().startswith(object_type.upper() + ","):
            continue
        for line in block.splitlines():
            if line.rstrip().endswith(f"!- {label}"):
                values.append(line.split("!-")[0].strip().rstrip(",;"))
    return values


@requires_energyplus
def test_idf_carries_the_co2_balance_at_site_pressure(site, library):
    bundle = _idf(site, library, fixtures.design())
    assert "ZONEAIRCONTAMINANTBALANCE," in bundle.text
    rate = float(_field(bundle.text, "PEOPLE", "Carbon Dioxide Generation Rate")[0])
    expected = size_fresh_air(site.elevation_m, 110.0).co2_m3_s_per_w
    assert rate == pytest.approx(expected, rel=1e-3)
    assert bundle.fresh_air is not None


@requires_energyplus
def test_ventilation_tops_leakage_up_to_the_per_person_requirement(site, library):
    design = fixtures.design()
    bundle = _idf(site, library, design)
    per_person = bundle.fresh_air.fresh_air_l_s_per_person
    need = design.occupancy.occupants * per_person
    assert bundle.ventilation_design_l_s == pytest.approx(
        need - bundle.infiltration_design_l_s, rel=1e-6)
    flow = float(_field(bundle.text, "ZONEVENTILATION:DESIGNFLOWRATE",
                        "Design Flow Rate")[0])
    assert flow * 1000.0 == pytest.approx(bundle.ventilation_design_l_s, rel=1e-3)


@requires_energyplus
def test_a_leakier_shelter_gets_less_ventilation_not_less_fresh_air(site, library):
    tight = fixtures.design(ventilation=VentilationSpec(infiltration_ach=0.2))
    leaky = fixtures.design(ventilation=VentilationSpec(infiltration_ach=2.0))
    a = _idf(site, library, tight)
    b = _idf(site, library, leaky)
    assert a.ventilation_design_l_s + a.infiltration_design_l_s == pytest.approx(
        b.ventilation_design_l_s + b.infiltration_design_l_s, rel=1e-6)


@requires_energyplus
def test_a_very_leaky_shelter_needs_no_ventilation_top_up(site, library):
    bundle = _idf(site, library, fixtures.design(
        ventilation=VentilationSpec(infiltration_ach=12.0)))
    assert bundle.ventilation_design_l_s == 0.0
    assert "ROOM_FRESH_AIR" not in bundle.text


@requires_energyplus
def test_an_unoccupied_store_has_no_co2_model(site, library):
    design = fixtures.design(occupancy=fixtures.design().occupancy.model_copy(
        update={"unoccupied": True}))
    bundle = _idf(site, library, design)
    assert bundle.fresh_air is None
    assert "ZONEAIRCONTAMINANTBALANCE," not in bundle.text


@requires_energyplus
def test_heating_holds_fourteen_only_through_the_night(site, library):
    text = _idf(site, library, fixtures.design()).text
    block = next(b for b in text.split("\n\n")
                 if b.startswith("SCHEDULE:COMPACT,") and "HEATING_SETPOINT" in b)
    assert "14" in block and "-60" in block
    assert "Until: 06:00" in block and "Until: 18:00" in block


@requires_energyplus
def test_floors_sit_on_kiva_and_the_monthly_boundary_is_gone(site, library):
    text = _idf(site, library, fixtures.design(Arrangement.SUNSPACE)).text
    assert "FOUNDATION:KIVA," in text and "FOUNDATION:KIVA:SETTINGS," in text
    assert "SITE:GROUNDTEMPERATURE:BUILDINGSURFACE" not in text
    perimeters = _field(text, "SURFACEPROPERTY:EXPOSEDFOUNDATIONPERIMETER",
                        "Total Exposed Perimeter")
    assert len(perimeters) == 2 and all(float(value) > 0 for value in perimeters)
    assert len(re.findall(r"Foundation,\s+!- Outside Boundary Condition\n", text)) == 2


@requires_energyplus
def test_the_monthly_ground_boundary_is_still_available(site, library):
    text = _idf(site, library, fixtures.design(), ground_model="monthly").text
    assert "SITE:GROUNDTEMPERATURE:BUILDINGSURFACE" in text
    assert "FOUNDATION:KIVA," not in text


@requires_energyplus
@pytest.mark.parametrize("arrangement,expected", [
    (Arrangement.SUNSPACE, 1.0), (Arrangement.TROMBE_WALL, 1.0),
    (Arrangement.VESTIBULE, 0.0)])
def test_coupling_is_gated_only_where_a_door_or_vent_decides(site, library,
                                                           arrangement, expected):
    params = ArrangementParams(trombe_glazing_id="single_clear_4mm",
                               sunspace_glazing_id="single_clear_4mm")
    design = fixtures.design(arrangement, arrangement_params=params)
    if arrangement is Arrangement.VESTIBULE:
        windows = dict(design.windows)
        windows.pop(params.vestibule_facade, None)
        design = fixtures.design(arrangement, arrangement_params=params, windows=windows)
    text = _idf(site, library, design).text
    assert float(_field(text, "ZONECROSSMIXING", "Delta Temperature")[0]) == expected


@requires_energyplus
def test_energyplus_holds_night_co2_near_the_limit(site, library):
    from shelter_engine.engine import Engine
    engine = Engine(site, library, fuel="kerosene")
    options = RunOptions(period=WEEK, surface_output=False, conditioned_run=False)
    result = engine.simulate(fixtures.design(), options, with_uncertainty=False,
                             with_reference=False)
    air = result.air_quality
    assert air is not None and air.peak_night_co2_ppm is not None
    assert air.peak_night_co2_ppm <= air.co2_limit_ppm * 1.05
    assert air.mean_night_co2_ppm > air.outdoor_co2_ppm


@requires_energyplus
def test_sealing_the_shelter_no_longer_buys_warmth_at_night(site, library):
    from shelter_engine.engine import Engine
    engine = Engine(site, library)
    options = RunOptions(period=WEEK, surface_output=False, conditioned_run=False)
    minima = {}
    for ach in (0.1, 0.5):
        design = fixtures.design(ventilation=VentilationSpec(infiltration_ach=ach))
        minima[ach] = engine.metrics_for(design, options)["minimum_overnight_operative_c"]
    assert abs(minima[0.1] - minima[0.5]) < 0.25
