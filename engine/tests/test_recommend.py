from __future__ import annotations

import pytest

import fixtures
from shelter_engine.constructions import build_constructions
from shelter_engine.costing import buildability, cost_breakdown
from shelter_engine.geometry import build_geometry
from shelter_engine.recommend import (RecommendationInputs,
                                      generate_recommendations)
from shelter_engine.schema import (ClimateObjective, ComfortMetrics,
                                   CondensationMetrics, Facade,
                                   HeatBalanceBreakdown, PCMSpec, ShutterSpec,
                                   WindowSpec)


def _inputs(library, design=None, **overrides):
    design = design or fixtures.design()
    built = build_constructions(design, library)
    model = build_geometry(design, built.names)
    payload = dict(
        design=design, library=library, model=model,
        comfort=ComfortMetrics(comfort_model_id="test",
                               minimum_overnight_operative_c=16.0,
                               mean_operative_c=18.0, decrement_factor=0.2,
                               time_lag_h=7.0),
        breakdown=HeatBalanceBreakdown(walls_kwh=-100.0, roof_kwh=-40.0,
                                       floor_kwh=-20.0,
                                       windows_conduction_kwh=-30.0,
                                       infiltration_kwh=-30.0),
        condensation=CondensationMetrics(hours=0.0),
        buildability=buildability(design, model, library),
        cost=cost_breakdown(design, model, library),
        climate_objective=ClimateObjective.HEATING_DOMINATED,
        incident_solar_kwh_by_facade={Facade.SOUTH: 900.0, Facade.EAST: 300.0},
        comfort_lower_bound_c=17.0)
    payload.update(overrides)
    return RecommendationInputs(**payload)


def _codes(recommendations):
    return {item.code for item in recommendations}


def test_recommendations_are_ordered_by_priority(library):
    recommendations = generate_recommendations(_inputs(library))
    order = {"high": 0, "medium": 1, "low": 2}
    priorities = [order[item.priority] for item in recommendations]
    assert priorities == sorted(priorities)


def test_every_recommendation_carries_its_trigger_numbers(library):
    for item in generate_recommendations(_inputs(library)):
        assert item.triggered_by
        assert all(isinstance(value, float) for value in item.triggered_by.values())
        assert item.detail


def test_unshuttered_glazing_loss_triggers_shutters(library):
    design = fixtures.design(windows={
        Facade.SOUTH: WindowSpec(wwr=0.3, glazing_id="single_clear_4mm",
                                 sill_height_m=0.6)})
    inputs = _inputs(library, design=design,
                     breakdown=HeatBalanceBreakdown(walls_kwh=-50.0,
                                                    windows_conduction_kwh=-60.0))
    assert "fit_night_shutters" in _codes(generate_recommendations(inputs))


def test_shuttered_glazing_does_not_trigger_shutters(library):
    inputs = _inputs(library,
                     breakdown=HeatBalanceBreakdown(walls_kwh=-50.0,
                                                    windows_conduction_kwh=-60.0))
    codes = _codes(generate_recommendations(inputs))
    assert "fit_night_shutters" not in codes


def test_low_shutter_compliance_is_reported(library):
    design = fixtures.design(windows={
        Facade.SOUTH: WindowSpec(wwr=0.3, glazing_id="single_clear_4mm",
                                 sill_height_m=0.6,
                                 shutter=ShutterSpec(present=True, compliance=0.5))})
    assert "shutter_operation_dependency" in _codes(
        generate_recommendations(_inputs(library, design=design)))


def test_high_infiltration_share_triggers_sealing(library):
    inputs = _inputs(library,
                     breakdown=HeatBalanceBreakdown(walls_kwh=-40.0,
                                                    infiltration_kwh=-60.0))
    assert "reduce_infiltration" in _codes(generate_recommendations(inputs))


def test_uninsulated_roof_triggers_insulation(library):
    design = fixtures.design(roof=fixtures.roof_construction(insulation_m=0.005))
    assert "insulate_roof" in _codes(
        generate_recommendations(_inputs(library, design=design)))


def test_thin_wall_triggers_the_code_limit(library):
    design = fixtures.design(wall=fixtures.wall_construction(mass_m=0.1,
                                                             insulation_m=0.002))
    assert "wall_u_above_code" in _codes(
        generate_recommendations(_inputs(library, design=design)))


def test_thermal_bridge_share_triggers_junction_advice(library):
    inputs = _inputs(library,
                     breakdown=HeatBalanceBreakdown(walls_kwh=-100.0,
                                                    thermal_bridge_kwh=30.0))
    assert "treat_thermal_bridges" in _codes(generate_recommendations(inputs))


def test_small_south_glazing_with_good_solar_triggers_more_glazing(library):
    design = fixtures.design(windows={
        Facade.SOUTH: WindowSpec(wwr=0.08, glazing_id="single_clear_4mm",
                                 sill_height_m=0.6)})
    assert "increase_south_glazing" in _codes(
        generate_recommendations(_inputs(library, design=design)))


def test_large_unshuttered_south_glazing_is_flagged(library):
    design = fixtures.design(windows={
        Facade.SOUTH: WindowSpec(wwr=0.65, glazing_id="single_clear_4mm",
                                 sill_height_m=0.15)})
    assert "south_glazing_without_shutters" in _codes(
        generate_recommendations(_inputs(library, design=design)))


def test_no_glazing_advice_in_a_cooling_climate(library):
    design = fixtures.design(windows={
        Facade.SOUTH: WindowSpec(wwr=0.08, glazing_id="single_clear_4mm",
                                 sill_height_m=0.6)})
    inputs = _inputs(library, design=design,
                     climate_objective=ClimateObjective.COOLING_DOMINATED)
    assert "increase_south_glazing" not in _codes(generate_recommendations(inputs))


def test_high_decrement_factor_triggers_mass(library):
    inputs = _inputs(library,
                     comfort=ComfortMetrics(comfort_model_id="test",
                                            decrement_factor=0.7, time_lag_h=1.0,
                                            mean_operative_c=15.0))
    assert "add_thermal_mass" in _codes(generate_recommendations(inputs))


def test_short_time_lag_in_a_coupled_room_triggers_placement_advice(library):
    inputs = _inputs(library,
                     comfort=ComfortMetrics(comfort_model_id="test",
                                            decrement_factor=0.35, time_lag_h=1.0,
                                            mean_operative_c=18.0))
    assert "mass_placement" in _codes(generate_recommendations(inputs))


def test_short_time_lag_in_a_decoupled_room_says_nothing_about_mass(library):
    inputs = _inputs(library,
                     comfort=ComfortMetrics(comfort_model_id="test",
                                            decrement_factor=0.08, time_lag_h=1.0,
                                            mean_operative_c=18.0))
    codes = _codes(generate_recommendations(inputs))
    assert "mass_placement" not in codes
    assert "add_thermal_mass" not in codes


def test_condensation_hours_trigger_advice(library):
    inputs = _inputs(library,
                     condensation=CondensationMetrics(hours=150.0,
                                                      worst_surface="ROOM_WALL_SOUTH_WIN",
                                                      minimum_inner_glazing_c=-2.0))
    recommendations = generate_recommendations(inputs)
    assert "control_condensation" in _codes(recommendations)
    condensation = [item for item in recommendations
                    if item.code == "control_condensation"][0]
    assert condensation.priority == "high"


def test_thick_wall_triggers_the_seismic_flag(library):
    design = fixtures.design(wall=fixtures.wall_construction(mass_m=0.6))
    assert "seismic_check" in _codes(
        generate_recommendations(_inputs(library, design=design)))


def test_pcm_above_the_operating_band_is_flagged(library):
    design = fixtures.design(pcm=PCMSpec(present=True, pcm_id="pcm_rt25",
                                         thickness_m=0.02))
    inputs = _inputs(library, design=design,
                     comfort=ComfortMetrics(comfort_model_id="test",
                                            mean_operative_c=14.0,
                                            decrement_factor=0.2, time_lag_h=7.0))
    assert "pcm_melt_point_too_high" in _codes(generate_recommendations(inputs))


def test_pcm_inside_the_operating_band_is_not_flagged(library):
    design = fixtures.design(pcm=PCMSpec(present=True, pcm_id="pcm_rt25",
                                         thickness_m=0.02))
    inputs = _inputs(library, design=design,
                     comfort=ComfortMetrics(comfort_model_id="test",
                                            mean_operative_c=23.0,
                                            decrement_factor=0.2, time_lag_h=7.0))
    assert "pcm_melt_point_too_high" not in _codes(generate_recommendations(inputs))


def test_overnight_shortfall_is_stated(library):
    inputs = _inputs(library,
                     comfort=ComfortMetrics(comfort_model_id="test",
                                            minimum_overnight_operative_c=12.0,
                                            mean_operative_c=16.0,
                                            decrement_factor=0.2, time_lag_h=7.0),
                     comfort_lower_bound_c=17.5)
    recommendations = generate_recommendations(inputs)
    assert "overnight_shortfall" in _codes(recommendations)
    shortfall = [item for item in recommendations
                 if item.code == "overnight_shortfall"][0]
    assert shortfall.triggered_by["shortfall_k"] == pytest.approx(5.5)


def test_no_shortfall_when_the_band_is_met(library):
    inputs = _inputs(library,
                     comfort=ComfortMetrics(comfort_model_id="test",
                                            minimum_overnight_operative_c=20.0,
                                            mean_operative_c=21.0,
                                            decrement_factor=0.2, time_lag_h=7.0),
                     comfort_lower_bound_c=17.5)
    assert "overnight_shortfall" not in _codes(generate_recommendations(inputs))


def test_empty_breakdown_produces_no_share_driven_advice(library):
    inputs = _inputs(library, breakdown=HeatBalanceBreakdown())
    codes = _codes(generate_recommendations(inputs))
    assert "reduce_infiltration" not in codes
    assert "treat_thermal_bridges" not in codes


def test_imported_material_share_is_flagged(library):
    design = fixtures.design(
        wall=fixtures.wall_construction(mass_m=0.4, insulation_m=0.2))
    inputs = _inputs(library, design=design)
    assert "material_availability" in _codes(generate_recommendations(inputs))
