from __future__ import annotations

import math

import pytest

import fixtures
from shelter_engine.errors import OptimisationError
from shelter_engine.geometry import build_geometry
from shelter_engine.constructions import build_constructions
from shelter_engine.costing import cost_breakdown
from shelter_engine.hashing import canonical_json, stable_hash
from shelter_engine.optimize import (INFEASIBLE_PENALTY, apply_variable,
                                     cold_climate_variables,
                                     constraint_count, hot_dry_variables,
                                     materialise, objective_vector, optimise,
                                     validate_spec)
from shelter_engine.schema import (DesignSpec, DesignVariable, Facade,
                                   OptimisationConstraints, OptimisationSpec)
from shelter_engine.uncertainty import (apply_parameter, describe_band,
                                        select_parameters, uncertainty_band)


def analytic_metrics(design: DesignSpec, library) -> dict[str, float]:
    resistance = library.resistance(design.wall) + library.resistance(design.roof)
    mass = library.areal_heat_capacity(design.wall) / 1.0e6
    leakage = design.ventilation.effective_air_change_ach
    solar = sum(window.wwr for window in design.windows.values())
    shutters = sum(1.0 for window in design.windows.values()
                   if window.shutter.present and window.shutter.compliance > 0.5)
    built = build_constructions(design, library)
    model = build_geometry(design, built.names)
    cost = cost_breakdown(design, model, library)
    minimum = (-4.0 + 6.0 * math.log1p(resistance) + 1.4 * math.sqrt(mass)
               - 5.0 * leakage + 8.0 * solar + 1.5 * shutters
               - 2.0 * design.plan.surface_to_volume)
    degree_hours = max(0.0, (18.0 - minimum) * 300.0)
    return {
        "minimum_overnight_operative_c": minimum,
        "worst_night_margin_k": minimum - 18.0,
        "degree_hours_total_kh": degree_hours,
        "degree_hours_below_kh": degree_hours,
        "degree_hours_above_kh": 0.0,
        "hours_outside_band": min(2160.0, degree_hours / 3.0),
        "total_cost_inr": cost.total_inr,
        "condensation_hours": max(0.0, 120.0 - 40.0 * solar - 30.0 * shutters),
        "wall_thickness_m": design.wall.thickness_m,
        "surface_to_volume": design.plan.surface_to_volume,
    }


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert canonical_json([1, 2]) != canonical_json([2, 1])


def test_stable_hash_is_stable_and_sensitive():
    design = fixtures.design()
    assert stable_hash(design) == stable_hash(design.model_copy(deep=True))
    assert stable_hash(design) != stable_hash(design.variant(name="other"))


def test_stable_hash_ignores_float_noise():
    design = fixtures.design()
    nudged = design.variant(plan=design.plan.model_copy(
        update={"floor_area_m2": design.plan.floor_area_m2 + 1e-12}))
    assert stable_hash(design) == stable_hash(nudged)


def test_apply_variable_rejects_unknown_paths():
    payload = fixtures.design().model_dump(mode="json")
    with pytest.raises(OptimisationError, match="no field"):
        apply_variable(payload, "plan.nonsense", 1.0)
    with pytest.raises(OptimisationError, match="no segment"):
        apply_variable(payload, "windows.north.wwr", 0.2)
    with pytest.raises(OptimisationError, match="indexes"):
        apply_variable(payload, "wall.layers.99.thickness_m", 0.2)
    with pytest.raises(OptimisationError):
        apply_variable(payload, "", 1.0)


def test_materialise_applies_every_kind_of_variable():
    design = fixtures.design()
    updated = materialise(design, {
        "plan.aspect_ratio": 2.4,
        "wall.layers.2.thickness_m": 0.5,
        "wall.layers.2.material_id": "mud_brick",
        "windows.south.wwr": 0.4,
        "windows.south.shutter.present": True,
        "ventilation.infiltration_ach": 0.25,
    })
    assert updated.plan.aspect_ratio == 2.4
    assert updated.wall.layers[2].material_id == "mud_brick"
    assert updated.windows[Facade.SOUTH].wwr == 0.4
    assert updated.ventilation.infiltration_ach == 0.25


def test_materialise_rejects_a_design_that_breaks_its_own_rules():
    design = fixtures.design()
    with pytest.raises(Exception):
        materialise(design, {"windows.south.wwr": 0.88,
                             "windows.south.sill_height_m": 2.0})


def test_objective_vector_negates_maximised_metrics():
    values = objective_vector({"degree_hours_total_kh": 100.0,
                               "worst_night_margin_k": 2.0},
                              ("degree_hours_total_kh", "worst_night_margin_k"))
    assert values == [100.0, -2.0]


def test_objective_vector_penalises_missing_metrics():
    values = objective_vector({}, ("degree_hours_total_kh",))
    assert values == [INFEASIBLE_PENALTY]


def test_constraint_count_tracks_optional_constraints():
    assert constraint_count(OptimisationConstraints()) == 3
    assert constraint_count(OptimisationConstraints(max_total_cost_inr=1.0)) == 4
    assert constraint_count(OptimisationConstraints(
        max_total_cost_inr=1.0, min_worst_night_margin_k=0.0)) == 5


def test_validate_spec_rejects_unknown_objectives():
    spec = OptimisationSpec(
        base_design=fixtures.design(),
        variables=(DesignVariable(name="plan.aspect_ratio", kind="real",
                                  low=1.0, high=2.0),),
        objectives=("something_invented",))
    with pytest.raises(OptimisationError, match="unknown objective"):
        validate_spec(spec)


def test_validate_spec_rejects_a_broken_midpoint():
    spec = OptimisationSpec(
        base_design=fixtures.design(),
        variables=(DesignVariable(name="windows.south.wwr", kind="real",
                                  low=0.85, high=0.9),))
    with pytest.raises(OptimisationError, match="midpoint"):
        validate_spec(spec)


def test_duplicate_variable_names_are_refused():
    variable = DesignVariable(name="plan.aspect_ratio", kind="real", low=1.0, high=2.0)
    with pytest.raises(Exception):
        OptimisationSpec(base_design=fixtures.design(), variables=(variable, variable))


def test_choice_variable_needs_distinct_options():
    with pytest.raises(Exception):
        DesignVariable(name="x", kind="choice", options=("a", "a"))


def test_real_variable_needs_an_ordered_range():
    with pytest.raises(Exception):
        DesignVariable(name="x", kind="real", low=2.0, high=1.0)


def test_optimisation_returns_a_pareto_front(library):
    spec = OptimisationSpec(
        base_design=fixtures.design(),
        variables=cold_climate_variables(
            fixtures.design(), mass_options=("rammed_earth", "mud_brick",
                                             "stone_granite"),
            glazing_options=("single_clear_4mm", "double_clear_6_12_6",
                             "double_lowe_argon")),
        population_size=16, generations=6, seed=3)
    result = optimise(spec, lambda design: analytic_metrics(design, library),
                      site_id="leh")
    assert result.front
    assert result.evaluated >= len(result.front)
    assert set(result.objectives) == set(spec.objectives)
    assert result.knee_hash in {point.design_hash for point in result.front}
    for point in result.front:
        assert set(point.objectives) == set(spec.objectives)
        assert point.design.wall.thickness_m > 0.0


def test_optimisation_respects_the_wall_thickness_constraint(library):
    constraints = OptimisationConstraints(max_wall_thickness_m=0.30,
                                          max_condensation_hours=200.0)
    spec = OptimisationSpec(
        base_design=fixtures.design(),
        variables=(DesignVariable(name="wall.layers.2.thickness_m", kind="real",
                                  low=0.10, high=0.60),),
        constraints=constraints, population_size=12, generations=5, seed=1,
        objectives=("degree_hours_total_kh", "total_cost_inr"))
    result = optimise(spec, lambda design: analytic_metrics(design, library))
    feasible = [point for point in result.front if point.feasible]
    assert feasible
    for point in feasible:
        assert point.design.wall.thickness_m <= constraints.max_wall_thickness_m + 1e-9


def test_optimisation_enforces_the_ventilation_floor(library):
    constraints = OptimisationConstraints(minimum_fresh_air_ach=0.5,
                                          max_condensation_hours=200.0)
    base = fixtures.design()
    base = base.variant(ventilation=base.ventilation.model_copy(
        update={"minimum_fresh_air_ach": 0.0, "infiltration_range": None}))
    spec = OptimisationSpec(
        base_design=base,
        variables=(DesignVariable(name="ventilation.infiltration_ach", kind="real",
                                  low=0.05, high=1.0),),
        constraints=constraints, population_size=12, generations=5, seed=2,
        objectives=("degree_hours_total_kh", "total_cost_inr"))
    result = optimise(spec, lambda design: analytic_metrics(design, library))
    for point in result.front:
        if point.feasible:
            assert point.design.ventilation.effective_air_change_ach >= 0.5 - 1e-9


def test_optimisation_reports_when_nothing_is_feasible(library):
    spec = OptimisationSpec(
        base_design=fixtures.design(),
        variables=(DesignVariable(name="wall.layers.2.thickness_m", kind="real",
                                  low=0.1, high=0.6),),
        constraints=OptimisationConstraints(max_condensation_hours=0.0),
        population_size=8, generations=3, seed=11,
        objectives=("degree_hours_total_kh", "total_cost_inr"))
    result = optimise(spec, lambda design: analytic_metrics(design, library))
    assert result.feasible_evaluated == 0
    assert any(item.code == "no_feasible_design" for item in result.warnings)
    assert all(point.constraint_violation > 0.0 for point in result.front)


def test_optimisation_survives_a_failing_evaluator(library):
    calls = {"count": 0}

    def flaky(design):
        calls["count"] += 1
        if calls["count"] % 3 == 0:
            raise RuntimeError("simulated EnergyPlus failure")
        return analytic_metrics(design, library)

    spec = OptimisationSpec(
        base_design=fixtures.design(),
        variables=(DesignVariable(name="wall.layers.2.thickness_m", kind="real",
                                  low=0.1, high=0.6),),
        population_size=10, generations=4, seed=5,
        objectives=("degree_hours_total_kh", "total_cost_inr"))
    result = optimise(spec, flaky)
    assert result.failed > 0
    assert any(item.code == "optimisation_evaluation_failures"
               for item in result.warnings)
    assert result.front


def test_optimisation_caches_repeat_designs(library):
    seen: list[str] = []

    def counting(design):
        seen.append(stable_hash(design))
        return analytic_metrics(design, library)

    spec = OptimisationSpec(
        base_design=fixtures.design(),
        variables=(DesignVariable(name="windows.south.shutter.present",
                                  kind="binary"),),
        population_size=10, generations=6, seed=7,
        objectives=("degree_hours_total_kh", "total_cost_inr"))
    result = optimise(spec, counting)
    assert len(set(seen)) <= 2
    assert len(seen) < spec.evaluations
    assert result.front


def test_hot_dry_variables_add_the_cooling_levers():
    design = fixtures.design()
    names = {variable.name for variable in hot_dry_variables(design)}
    assert "ventilation.night_ventilation" in names
    assert "ventilation.night_ventilation_ach" in names
    assert any(name.endswith("overhang_depth_m") for name in names)


def test_select_parameters_prefers_the_widest_ranges(library):
    design = fixtures.design()
    parameters = select_parameters(design, library)
    assert parameters
    assert len(parameters) <= 3
    names = {parameter.name for parameter in parameters}
    assert "conductivity::rammed_earth" in names
    assert "infiltration_ach" in names


def test_select_parameters_returns_nothing_without_ranges(library):
    design = fixtures.design()
    design = design.variant(ventilation=design.ventilation.model_copy(
        update={"infiltration_range": None}),
        windows={facade: window.model_copy(update={
            "shutter": window.shutter.model_copy(update={"present": False})})
            for facade, window in design.windows.items()},
        wall=fixtures.wall_construction(mass_material="mud_brick"),
        partition=None)
    assert select_parameters(design, library) == []


def test_apply_parameter_changes_the_right_thing(library):
    design = fixtures.design()
    parameters = {parameter.name: parameter
                  for parameter in select_parameters(design, library)}
    variant, perturbed = apply_parameter(
        design, library, parameters["conductivity::rammed_earth"], 1.3)
    assert perturbed.material("rammed_earth").conductivity_w_mk == 1.3
    assert variant is design
    variant, perturbed = apply_parameter(
        design, library, parameters["infiltration_ach"], 1.0)
    assert variant.ventilation.infiltration_ach == 1.0
    assert perturbed is library


def test_uncertainty_band_brackets_the_nominal(library):
    design = fixtures.design()
    parameters = select_parameters(design, library)

    def evaluate(variant, lib):
        return analytic_metrics(variant, lib)["minimum_overnight_operative_c"]

    nominal = evaluate(design, library)
    band = uncertainty_band(evaluate, design, library, parameters,
                            "minimum_overnight_operative_c", "C",
                            nominal_value=nominal)
    assert band.low <= band.nominal <= band.high
    assert band.runs == 2 ** len(parameters)
    assert band.dominant_parameter
    assert sum(item.share for item in band.contributions) == pytest.approx(1.0)


def test_uncertainty_band_collapses_without_parameters(library):
    design = fixtures.design()

    def evaluate(variant, lib):
        return 12.0

    band = uncertainty_band(evaluate, design, library, [], "metric", "C")
    assert band.low == band.high == band.nominal == 12.0
    assert band.runs == 1


def test_describe_band_names_the_dominant_input(library):
    design = fixtures.design()
    parameters = select_parameters(design, library)

    def evaluate(variant, lib):
        return analytic_metrics(variant, lib)["minimum_overnight_operative_c"]

    band = uncertainty_band(evaluate, design, library, parameters,
                            "minimum_overnight_operative_c", "C")
    text = describe_band(band)
    assert "to" in text
    assert "driven mainly by" in text


def _staged_spec(population_size: int, generations: int) -> OptimisationSpec:
    return OptimisationSpec(
        base_design=fixtures.design(),
        variables=(
            DesignVariable(name="wall.layers.1.thickness_m", kind="real",
                           low=0.05, high=0.30),
            DesignVariable(name="windows.south.shutter.present", kind="binary"),
        ),
        population_size=population_size, generations=generations, seed=5,
        objectives=("degree_hours_total_kh", "total_cost_inr"))


def test_the_search_publishes_a_front_before_it_finishes(library):
    spec = _staged_spec(8, 4)
    stages: list = []
    result = optimise(spec, lambda design: analytic_metrics(design, library),
                      site_id="leh", on_stage=stages.append)

    assert len(stages) >= 2
    assert [item.generation for item in stages] == sorted(
        item.generation for item in stages)
    assert all(item.complete is False for item in stages)
    assert all(item.front for item in stages)
    assert stages[-1].evaluated <= result.evaluated
    assert result.complete is True
    assert result.generation == spec.generations


def test_every_published_stage_names_a_best_design(library):
    spec = _staged_spec(8, 3)
    stages: list = []
    optimise(spec, lambda design: analytic_metrics(design, library),
             site_id="leh", on_stage=stages.append)

    for stage in stages:
        assert stage.knee_hash is not None
        assert any(point.design_hash == stage.knee_hash for point in stage.front)


def test_a_failing_stage_callback_cannot_kill_the_search(library):
    def explode(_result):
        raise RuntimeError("the consumer fell over")

    spec = _staged_spec(8, 3)
    result = optimise(spec, lambda design: analytic_metrics(design, library),
                      site_id="leh", on_stage=explode)
    assert result.front
    assert result.complete is True
