from __future__ import annotations

import math
import time
from typing import Any, Callable, Sequence

import numpy as np
from pydantic import ValidationError

from .cache import Cache, LRUCache
from .errors import OptimisationError
from .hashing import stable_hash
from .schema import (DesignSpec, DesignVariable, EngineWarning, Facade,
                     OptimisationConstraints, OptimisationSpec, ParetoPoint,
                     ParetoResult, Severity)

INFEASIBLE_PENALTY = 1.0e9
INVALID_VIOLATION = 1.0e6

MAXIMISE = frozenset({
    "worst_night_margin_k", "minimum_overnight_operative_c", "minimum_operative_c",
    "minimum_on_design_day_c", "time_lag_h",
})

KNOWN_OBJECTIVES = frozenset({
    "degree_hours_total_kh", "degree_hours_below_kh", "degree_hours_above_kh",
    "hours_outside_band", "occupied_hours_outside_band", "total_cost_inr",
    "heating_demand_kwh", "cooling_demand_kwh", "fuel_units", "condensation_hours",
    "wall_thickness_m", "surface_to_volume",
}) | MAXIMISE

Metrics = dict[str, float]
Evaluator = Callable[[DesignSpec], Metrics]


def _navigate(payload: Any, segments: Sequence[str], path: str) -> Any:
    node = payload
    for segment in segments:
        if isinstance(node, list):
            try:
                index = int(segment)
            except ValueError:
                raise OptimisationError(
                    f"variable path {path!r} indexes a list with {segment!r}") from None
            if not 0 <= index < len(node):
                raise OptimisationError(
                    f"variable path {path!r} indexes position {index} of a list with "
                    f"{len(node)} entries")
            node = node[index]
        elif isinstance(node, dict):
            if segment not in node:
                raise OptimisationError(
                    f"variable path {path!r} has no segment {segment!r}; available: "
                    f"{sorted(node)}")
            node = node[segment]
        else:
            raise OptimisationError(
                f"variable path {path!r} cannot descend past {segment!r}")
    return node


def apply_variable(payload: dict[str, Any], path: str, value: Any) -> None:
    segments = [segment for segment in path.split(".") if segment]
    if not segments:
        raise OptimisationError("an empty variable path cannot be applied")
    parent = _navigate(payload, segments[:-1], path)
    leaf = segments[-1]
    if isinstance(parent, list):
        try:
            index = int(leaf)
        except ValueError:
            raise OptimisationError(
                f"variable path {path!r} indexes a list with {leaf!r}") from None
        if not 0 <= index < len(parent):
            raise OptimisationError(f"variable path {path!r} indexes outside the list")
        parent[index] = value
        return
    if not isinstance(parent, dict):
        raise OptimisationError(f"variable path {path!r} does not address a field")
    if leaf not in parent:
        raise OptimisationError(
            f"variable path {path!r} has no field {leaf!r}; available: {sorted(parent)}")
    parent[leaf] = value


def _rescale_range(span: dict[str, Any] | None, old_anchor: float,
                   new_anchor: float) -> dict[str, Any] | None:
    if span is None or old_anchor == new_anchor:
        return span
    low, high = float(span["low"]), float(span["high"])
    if low <= new_anchor <= high:
        return span
    if old_anchor > 0.0 and new_anchor > 0.0:
        factor = new_anchor / old_anchor
        return {"low": low * factor, "high": high * factor}
    shift = new_anchor - old_anchor
    return {"low": low + shift, "high": high + shift}


def materialise(base: DesignSpec, assignment: dict[str, Any]) -> DesignSpec:
    payload = base.model_dump(mode="json")
    for path, value in assignment.items():
        apply_variable(payload, path, value)
    ventilation = payload.get("ventilation", {})
    ventilation["infiltration_range"] = _rescale_range(
        ventilation.get("infiltration_range"),
        base.ventilation.infiltration_ach,
        float(ventilation.get("infiltration_ach", base.ventilation.infiltration_ach)))
    return DesignSpec.model_validate(payload)


def _pymoo_variable(variable: DesignVariable):
    from pymoo.core.variable import Binary, Choice, Integer, Real
    if variable.kind == "real":
        return Real(bounds=(float(variable.low), float(variable.high)))
    if variable.kind == "integer":
        return Integer(bounds=(int(variable.low), int(variable.high)))
    if variable.kind == "binary":
        return Binary()
    if variable.kind == "choice":
        return Choice(options=list(variable.options or ()))
    raise OptimisationError(f"unknown variable kind {variable.kind}")


def validate_spec(spec: OptimisationSpec) -> None:
    for objective in spec.objectives:
        if objective not in KNOWN_OBJECTIVES:
            raise OptimisationError(
                f"unknown objective {objective!r}; known objectives are "
                f"{sorted(KNOWN_OBJECTIVES)}")
    midpoint: dict[str, Any] = {}
    for variable in spec.variables:
        if variable.kind == "real":
            midpoint[variable.name] = 0.5 * (float(variable.low) + float(variable.high))
        elif variable.kind == "integer":
            midpoint[variable.name] = int(round(0.5 * (variable.low + variable.high)))
        elif variable.kind == "binary":
            midpoint[variable.name] = False
        else:
            midpoint[variable.name] = (variable.options or ("",))[0]
    try:
        materialise(spec.base_design, midpoint)
    except ValidationError as error:
        raise OptimisationError(
            "the midpoint of the search space does not produce a valid design: "
            f"{error}") from error


def constraint_vector(metrics: Metrics, design: DesignSpec | None,
                      constraints: OptimisationConstraints) -> list[float]:
    values: list[float] = []
    if design is None:
        return [INVALID_VIOLATION] * constraint_count(constraints)
    values.append(constraints.minimum_fresh_air_ach
                  - design.ventilation.effective_air_change_ach)
    values.append(design.wall.thickness_m - constraints.max_wall_thickness_m)
    values.append(metrics.get("condensation_hours", 0.0)
                  - constraints.max_condensation_hours)
    if constraints.max_total_cost_inr is not None:
        values.append(metrics.get("total_cost_inr", 0.0)
                      - constraints.max_total_cost_inr)
    if constraints.min_worst_night_margin_k is not None:
        values.append(constraints.min_worst_night_margin_k
                      - metrics.get("worst_night_margin_k", -INFEASIBLE_PENALTY))
    return values


def constraint_count(constraints: OptimisationConstraints) -> int:
    count = 3
    if constraints.max_total_cost_inr is not None:
        count += 1
    if constraints.min_worst_night_margin_k is not None:
        count += 1
    return count


def objective_vector(metrics: Metrics, objectives: Sequence[str]) -> list[float]:
    values: list[float] = []
    for name in objectives:
        raw = metrics.get(name)
        if raw is None or not math.isfinite(raw):
            values.append(INFEASIBLE_PENALTY)
            continue
        values.append(-float(raw) if name in MAXIMISE else float(raw))
    return values


def _build_problem(spec: OptimisationSpec, evaluate: Evaluator, cache: Cache,
                   failures: list[str]):
    from pymoo.core.problem import ElementwiseProblem

    variables = {variable.name: _pymoo_variable(variable)
                 for variable in spec.variables}
    n_constraints = constraint_count(spec.constraints)
    objectives = spec.objectives
    base = spec.base_design
    constraints = spec.constraints
    records: dict[str, tuple[DesignSpec, Metrics]] = {}

    class ShelterProblem(ElementwiseProblem):
        def __init__(self):
            super().__init__(vars=variables, n_obj=len(objectives),
                             n_ieq_constr=n_constraints)

        def _evaluate(self, assignment, out, *args, **kwargs):
            key = stable_hash(assignment)
            cached = cache.get(key)
            if cached is not None:
                design, metrics = cached
            else:
                try:
                    design = materialise(base, dict(assignment))
                except (ValidationError, OptimisationError) as error:
                    failures.append(str(error)[:300])
                    out["F"] = [INFEASIBLE_PENALTY] * len(objectives)
                    out["G"] = [INVALID_VIOLATION] * n_constraints
                    return
                try:
                    metrics = evaluate(design)
                except Exception as error:
                    failures.append(f"{type(error).__name__}: {error}"[:300])
                    out["F"] = [INFEASIBLE_PENALTY] * len(objectives)
                    out["G"] = [INVALID_VIOLATION] * n_constraints
                    return
                cache.set(key, (design, metrics))
            records[stable_hash(design)] = (design, metrics)
            out["F"] = objective_vector(metrics, objectives)
            out["G"] = constraint_vector(metrics, design, constraints)

    return ShelterProblem(), records


def _algorithm(spec: OptimisationSpec):
    from pymoo.core.mixed import MixedVariableGA
    if len(spec.objectives) == 1:
        return MixedVariableGA(pop_size=spec.population_size)
    from pymoo.operators.survival.rank_and_crowding import RankAndCrowding
    return MixedVariableGA(pop_size=spec.population_size, survival=RankAndCrowding())


def _knee(points: Sequence[ParetoPoint], objectives: Sequence[str]) -> str | None:
    if not points:
        return None
    if len(points) == 1:
        return points[0].design_hash
    matrix = np.array([[point.objectives[name] for name in objectives]
                       for point in points], dtype=float)
    signs = np.array([-1.0 if name in MAXIMISE else 1.0 for name in objectives])
    oriented = matrix * signs
    low = oriented.min(axis=0)
    high = oriented.max(axis=0)
    span = np.where(high - low > 1e-12, high - low, 1.0)
    normalised = (oriented - low) / span
    distances = np.linalg.norm(normalised, axis=1)
    return points[int(np.argmin(distances))].design_hash


def _extract_population(result) -> tuple[list[Any], np.ndarray, np.ndarray]:
    population = None
    for candidate in (getattr(result, "opt", None), getattr(result, "pop", None)):
        if candidate is not None and len(candidate) > 0:
            population = candidate
            break
    if population is not None:
        solutions = list(population.get("X"))
        raw_f = population.get("F")
        raw_cv = population.get("CV")
        objectives = np.atleast_2d(raw_f) if raw_f is not None else np.empty((0, 0))
        violations = np.atleast_1d(raw_cv).ravel() if raw_cv is not None \
            else np.zeros(len(solutions))
        return solutions, objectives, violations
    if result.X is None:
        return [], np.empty((0, 0)), np.zeros(0)
    solutions = list(result.X) if isinstance(result.X, (list, np.ndarray)) \
        else [result.X]
    objectives = np.atleast_2d(result.F) if result.F is not None else np.empty((0, 0))
    violations = np.atleast_1d(result.CV).ravel() if result.CV is not None \
        else np.zeros(len(solutions))
    return solutions, objectives, violations


def optimise(spec: OptimisationSpec, evaluate: Evaluator, site_id: str = "",
             cache: Cache | None = None,
             progress: Callable[[int, int], None] | None = None) -> ParetoResult:
    from pymoo.optimize import minimize

    validate_spec(spec)
    cache = cache if cache is not None else LRUCache(maxsize=4096)
    failures: list[str] = []
    problem, records = _build_problem(spec, evaluate, cache, failures)
    algorithm = _algorithm(spec)

    started = time.perf_counter()
    result = minimize(problem, algorithm, ("n_gen", spec.generations),
                      seed=spec.seed, verbose=False, save_history=False,
                      return_least_infeasible=True)
    runtime = time.perf_counter() - started

    warnings: list[EngineWarning] = []
    if failures:
        warnings.append(EngineWarning(
            code="optimisation_evaluation_failures", severity=Severity.WARNING,
            origin="optimiser", count=len(failures),
            message=(f"{len(failures)} of {len(records) + len(failures)} candidate "
                     "evaluations failed and were treated as infeasible. First failure: "
                     f"{failures[0]}")))

    front: list[ParetoPoint] = []
    solutions, objective_matrix, violations = _extract_population(result)

    for index, assignment in enumerate(solutions):
        if not isinstance(assignment, dict):
            continue
        try:
            design = materialise(spec.base_design, dict(assignment))
        except (ValidationError, OptimisationError):
            continue
        design_hash = stable_hash(design)
        metrics = records.get(design_hash, (design, {}))[1]
        raw = objective_matrix[index] if index < len(objective_matrix) else []
        objectives = {}
        for position, name in enumerate(spec.objectives):
            if name in metrics:
                objectives[name] = float(metrics[name])
            elif position < len(raw):
                value = float(raw[position])
                objectives[name] = -value if name in MAXIMISE else value
        violation = float(violations[index]) if index < len(violations) else 0.0
        flags: list[str] = []
        if design.wall.thickness_m >= spec.constraints.seismic_wall_thickness_flag_m:
            flags.append("seismic_structural_check_required")
        if metrics.get("condensation_hours", 0.0) > 0.0:
            flags.append("condensation_hours_present")
        front.append(ParetoPoint(
            design_hash=design_hash, design=design, objectives=objectives,
            constraint_violation=violation, feasible=violation <= 1e-9,
            metrics={key: float(value) for key, value in metrics.items()
                     if isinstance(value, (int, float))},
            flags=tuple(flags)))

    feasible = sum(1 for point in front if point.feasible)
    if front and feasible == 0:
        warnings.append(EngineWarning(
            code="no_feasible_design", severity=Severity.SEVERE, origin="optimiser",
            message=("No candidate satisfied every constraint. The designs returned are "
                     "the least infeasible ones found and each carries its constraint "
                     "violation. Widen a bound or relax the search space rather than "
                     "reading these as recommendations.")))
    seen_hashes: set[str] = set()
    unique_front: list[ParetoPoint] = []
    for point in front:
        if point.design_hash in seen_hashes:
            continue
        seen_hashes.add(point.design_hash)
        unique_front.append(point)
    front = unique_front
    feasible = sum(1 for point in front if point.feasible)
    return ParetoResult(
        spec_hash=stable_hash(spec),
        site_id=site_id,
        front=tuple(front),
        evaluated=len(records) + len(failures),
        feasible_evaluated=feasible,
        failed=len(failures),
        objectives=spec.objectives,
        runtime_s=runtime,
        warnings=tuple(warnings),
        knee_hash=_knee([point for point in front if point.feasible] or front,
                        spec.objectives))


def cold_climate_variables(base: DesignSpec, mass_layer: int = 2,
                           insulation_layer: int = 1,
                           mass_options: Sequence[str] = (),
                           glazing_options: Sequence[str] = (),
                           pcm_options: Sequence[str] = ()) -> tuple[DesignVariable, ...]:
    variables: list[DesignVariable] = [
        DesignVariable(name=f"wall.layers.{mass_layer}.thickness_m", kind="real",
                       low=0.10, high=0.60),
        DesignVariable(name=f"wall.layers.{insulation_layer}.thickness_m", kind="real",
                       low=0.02, high=0.20),
        DesignVariable(name="roof.layers.1.thickness_m", kind="real",
                       low=0.05, high=0.30),
        DesignVariable(name="plan.aspect_ratio", kind="real", low=0.8, high=3.0),
        DesignVariable(name="plan.azimuth_deg", kind="real", low=135.0, high=225.0),
        DesignVariable(name="ventilation.infiltration_ach", kind="real",
                       low=0.20, high=1.20),
    ]
    if Facade.SOUTH in base.windows:
        variables.append(DesignVariable(name="windows.south.wwr", kind="real",
                                        low=0.05, high=0.55))
        variables.append(DesignVariable(name="windows.south.shutter.present",
                                        kind="binary"))
        if glazing_options:
            variables.append(DesignVariable(name="windows.south.glazing_id",
                                            kind="choice",
                                            options=tuple(glazing_options)))
    if mass_options:
        variables.append(DesignVariable(name=f"wall.layers.{mass_layer}.material_id",
                                        kind="choice", options=tuple(mass_options)))
    if pcm_options:
        variables.append(DesignVariable(name="pcm.present", kind="binary"))
        variables.append(DesignVariable(name="pcm.pcm_id", kind="choice",
                                        options=tuple(pcm_options)))
    return tuple(variables)


def hot_dry_variables(base: DesignSpec, mass_layer: int = 2,
                      insulation_layer: int = 1,
                      mass_options: Sequence[str] = (),
                      glazing_options: Sequence[str] = ()
                      ) -> tuple[DesignVariable, ...]:
    variables = list(cold_climate_variables(base, mass_layer, insulation_layer,
                                            mass_options, glazing_options))
    variables.append(DesignVariable(name="ventilation.night_ventilation",
                                    kind="binary"))
    variables.append(DesignVariable(name="ventilation.night_ventilation_ach",
                                    kind="real", low=1.0, high=10.0))
    for facade in (Facade.SOUTH, Facade.EAST, Facade.WEST):
        if facade in base.windows:
            variables.append(DesignVariable(
                name=f"windows.{facade.value}.overhang_depth_m", kind="real",
                low=0.0, high=1.5))
    return tuple(variables)
