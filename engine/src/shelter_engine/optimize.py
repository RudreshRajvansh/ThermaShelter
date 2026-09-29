from __future__ import annotations

import math
from dataclasses import dataclass
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

HEATING_OBJECTIVES = frozenset({"heating_demand_kwh", "fuel_units", "heating_litres"})

KNOWN_OBJECTIVES = frozenset({
    "degree_hours_total_kh", "degree_hours_below_kh", "degree_hours_above_kh",
    "hours_outside_band", "occupied_hours_outside_band", "total_cost_inr",
    "cooling_demand_kwh", "condensation_hours", "wall_thickness_m", "surface_to_volume",
    "night_degree_hours_below_kh", "night_hours_below_target", "imported_tonnes",
    "material_tonnes", "peak_night_co2_ppm",
}) | MAXIMISE | HEATING_OBJECTIVES

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


def _fit_arrangement(base: DesignSpec, payload: dict[str, Any]) -> None:
    arrangement = payload.get("arrangement", base.arrangement.value)
    if arrangement == base.arrangement.value:
        return
    if arrangement in ("sunspace", "trombe_wall", "vestibule") \
            and not payload.get("partition"):
        payload["partition"] = payload["wall"]
    windows = payload.get("windows", {})
    if arrangement == "trombe_wall" and "south" in windows:
        windows["south"]["wwr"] = 0.0
    if arrangement == "vestibule":
        facade = payload.get("arrangement_params", {}).get("vestibule_facade", "north")
        if facade in windows:
            windows[facade]["wwr"] = 0.0


def materialise(base: DesignSpec, assignment: dict[str, Any]) -> DesignSpec:
    payload = base.model_dump(mode="json")
    for path, value in assignment.items():
        apply_variable(payload, path, value)
    _fit_arrangement(base, payload)
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
    if constraints.enforce_co2_limit:
        peak = metrics.get("peak_night_co2_ppm")
        limit = metrics.get("co2_limit_ppm", design.ventilation.co2_limit_ppm)
        values.append(0.0 if peak is None
                      else peak - limit * (1.0 + constraints.co2_tolerance_fraction))
    return values


def constraint_count(constraints: OptimisationConstraints) -> int:
    count = 3
    if constraints.max_total_cost_inr is not None:
        count += 1
    if constraints.min_worst_night_margin_k is not None:
        count += 1
    if constraints.enforce_co2_limit:
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
                   failures: list[str], runner: Any = None):
    from pymoo.core.problem import ElementwiseProblem

    variables = {variable.name: _pymoo_variable(variable)
                 for variable in spec.variables}
    n_constraints = constraint_count(spec.constraints)
    objectives = spec.objectives
    base = spec.base_design
    constraints = spec.constraints
    records: dict[str, tuple[DesignSpec, Metrics]] = {}

    class ShelterProblem(ElementwiseProblem):
        def __init__(self, **kwargs):
            super().__init__(vars=variables, n_obj=len(objectives),
                             n_ieq_constr=n_constraints, **kwargs)

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

    problem = ShelterProblem(elementwise_runner=runner) if runner is not None \
        else ShelterProblem()
    return problem, records


def _algorithm(spec: OptimisationSpec):
    from pymoo.core.mixed import MixedVariableGA
    if len(spec.objectives) == 1:
        return MixedVariableGA(pop_size=spec.population_size)
    from pymoo.operators.survival.rank_and_crowding import RankAndCrowding
    return MixedVariableGA(pop_size=spec.population_size, survival=RankAndCrowding())


def _knee(points: Sequence[ParetoPoint], objectives: Sequence[str]) -> str | None:
    scores = knee_scores(points, objectives)
    if not scores:
        return None
    return points[int(np.argmax(scores))].design_hash


def knee_scores(points: Sequence[ParetoPoint], objectives: Sequence[str]) -> list[float]:
    if not points:
        return []
    if len(points) == 1:
        return [0.0]
    matrix = np.array([[point.objectives[name] for name in objectives]
                       for point in points], dtype=float)
    signs = np.array([-1.0 if name in MAXIMISE else 1.0 for name in objectives])
    oriented = matrix * signs
    low = oriented.min(axis=0)
    high = oriented.max(axis=0)
    span = np.where(high - low > 1e-12, high - low, 1.0)
    normalised = (oriented - low) / span
    count = normalised.shape[1]
    if count == 1:
        return [float(-value) for value in normalised[:, 0]]
    extremes = np.array([normalised[int(np.argmin(normalised[:, index]))]
                         for index in range(count)])
    normal = _hyperplane_normal(extremes)
    if normal is None:
        return [float(-value) for value in np.linalg.norm(normalised, axis=1)]
    offset = float(normal @ extremes[0])
    return [float(value) for value in offset - normalised @ normal]


def _hyperplane_normal(extremes: np.ndarray) -> np.ndarray | None:
    count = extremes.shape[1]
    if len({tuple(np.round(row, 12)) for row in extremes}) < count:
        return None
    base = extremes[0]
    directions = extremes[1:] - base
    if count == 2:
        normal = np.array([-directions[0, 1], directions[0, 0]])
    else:
        _, singular, vh = np.linalg.svd(directions)
        if singular.size < count - 1 or singular[-1] < 1e-9:
            return None
        normal = vh[-1]
    length = float(np.linalg.norm(normal))
    if length < 1e-12:
        return None
    normal = normal / length
    if float(normal.sum()) < 0.0:
        normal = -normal
    return normal


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


class _StageReporter:
    def __init__(self, spec: OptimisationSpec, site_id: str, records: dict,
                 failures: list[str], started: float,
                 emit: Callable[[ParetoResult], None]) -> None:
        self.spec = spec
        self.site_id = site_id
        self.records = records
        self.failures = failures
        self.started = started
        self.emit = emit
        self.generation = 0

    def __call__(self, algorithm: Any) -> None:
        self.generation += 1
        try:
            front = _build_front(self.spec, algorithm, self.records)
        except Exception:
            return
        if not front:
            return
        feasible = sum(1 for point in front if point.feasible)
        result = ParetoResult(
            spec_hash=stable_hash(self.spec),
            site_id=self.site_id,
            front=tuple(front),
            evaluated=len(self.records) + len(self.failures),
            feasible_evaluated=feasible,
            failed=len(self.failures),
            objectives=self.spec.objectives,
            runtime_s=time.perf_counter() - self.started,
            generation=self.generation,
            generations_planned=self.spec.generations,
            complete=False,
            warnings=(),
            knee_hash=_knee([point for point in front if point.feasible] or front,
                            self.spec.objectives))
        try:
            self.emit(result)
        except Exception:
            return


def optimise(spec: OptimisationSpec, evaluate: Evaluator, site_id: str = "",
             cache: Cache | None = None,
             progress: Callable[[int, int], None] | None = None,
             on_stage: Callable[[ParetoResult], None] | None = None) -> ParetoResult:
    from pymoo.optimize import minimize

    validate_spec(spec)
    cache = cache if cache is not None else LRUCache(maxsize=4096)
    failures: list[str] = []
    pool = None
    runner = None
    if spec.workers > 1:
        from multiprocessing.pool import ThreadPool

        from pymoo.core.problem import StarmapParallelization
        pool = ThreadPool(spec.workers)
        runner = StarmapParallelization(pool.starmap)
    problem, records = _build_problem(spec, evaluate, cache, failures, runner)
    algorithm = _algorithm(spec)

    started = time.perf_counter()
    extra: dict[str, Any] = {}
    if on_stage is not None:
        extra["callback"] = _StageReporter(spec, site_id, records, failures,
                                           started, on_stage)
    try:
        result = minimize(problem, algorithm, ("n_gen", spec.generations),
                          seed=spec.seed, verbose=False, save_history=False,
                          return_least_infeasible=True, **extra)
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    runtime = time.perf_counter() - started

    warnings: list[EngineWarning] = []
    if failures:
        warnings.append(EngineWarning(
            code="optimisation_evaluation_failures", severity=Severity.WARNING,
            origin="optimiser", count=len(failures),
            message=(f"{len(failures)} of {len(records) + len(failures)} candidate "
                     "evaluations failed and were treated as infeasible. First failure: "
                     f"{failures[0]}")))

    front = _build_front(spec, result, records)

    feasible = sum(1 for point in front if point.feasible)
    if front and feasible == 0:
        warnings.append(EngineWarning(
            code="no_feasible_design", severity=Severity.SEVERE, origin="optimiser",
            message=("No candidate satisfied every constraint. The designs returned are "
                     "the least infeasible ones found and each carries its constraint "
                     "violation. Widen a bound or relax the search space rather than "
                     "reading these as recommendations.")))
    return ParetoResult(
        spec_hash=stable_hash(spec),
        site_id=site_id,
        front=tuple(front),
        evaluated=len(records) + len(failures),
        feasible_evaluated=feasible,
        failed=len(failures),
        objectives=spec.objectives,
        runtime_s=runtime,
        generation=spec.generations,
        generations_planned=spec.generations,
        complete=True,
        warnings=tuple(warnings),
        knee_hash=_knee([point for point in front if point.feasible] or front,
                        spec.objectives))


def _build_front(spec: OptimisationSpec, result: Any,
                 records: dict) -> list[ParetoPoint]:
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

    seen_hashes: set[str] = set()
    unique_front: list[ParetoPoint] = []
    for point in front:
        if point.design_hash in seen_hashes:
            continue
        seen_hashes.add(point.design_hash)
        unique_front.append(point)
    return unique_front


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


MASS_MATERIALS = ("rammed_earth", "mud_brick", "stone_granite", "brick_solid",
                  "concrete_dense")
INSULATION_MATERIALS = ("eps_thermocol", "xps_board", "rockwool", "puf_panel")


@dataclass(frozen=True)
class Lever:
    variable: DesignVariable
    label: str
    group: str


def _layer_index(construction, library, pick: str) -> int | None:
    scored = []
    for index, layer in enumerate(construction.layers):
        try:
            material = library.material(layer.material_id)
        except Exception:
            continue
        if pick == "mass":
            if material.density_kg_m3 < 800.0 or material.conductivity_w_mk > 5.0:
                continue
            scored.append((material.density_kg_m3 * material.specific_heat_j_kgk
                           * layer.thickness_m, index))
        elif material.conductivity_w_mk < 0.1:
            scored.append((-material.conductivity_w_mk, index))
    return max(scored)[1] if scored else None


def search_levers(base: DesignSpec, library, cold: bool = True) -> tuple[Lever, ...]:
    levers: list[Lever] = []
    known = {material.id for material in library.materials}
    mass = _layer_index(base.wall, library, "mass")
    insulation = _layer_index(base.wall, library, "insulation")
    roof_insulation = _layer_index(base.roof, library, "insulation")
    floor_insulation = _layer_index(base.floor, library, "insulation")
    if mass is not None:
        levers.append(Lever(DesignVariable(
            name=f"wall.layers.{mass}.thickness_m", kind="real", low=0.15, high=0.55),
            "wall mass thickness", "walls"))
        options = tuple(item for item in MASS_MATERIALS if item in known)
        if len(options) >= 2:
            levers.append(Lever(DesignVariable(
                name=f"wall.layers.{mass}.material_id", kind="choice", options=options),
                "wall mass material", "walls"))
    if insulation is not None:
        levers.append(Lever(DesignVariable(
            name=f"wall.layers.{insulation}.thickness_m", kind="real", low=0.02,
            high=0.18), "wall insulation thickness", "walls"))
        options = tuple(item for item in INSULATION_MATERIALS if item in known)
        if len(options) >= 2:
            levers.append(Lever(DesignVariable(
                name=f"wall.layers.{insulation}.material_id", kind="choice",
                options=options), "wall insulation material", "walls"))
    if roof_insulation is not None:
        levers.append(Lever(DesignVariable(
            name=f"roof.layers.{roof_insulation}.thickness_m", kind="real", low=0.05,
            high=0.30), "roof insulation thickness", "roof and floor"))
    if floor_insulation is not None:
        levers.append(Lever(DesignVariable(
            name=f"floor.layers.{floor_insulation}.thickness_m", kind="real",
            low=0.02, high=0.15), "floor insulation thickness", "roof and floor"))
    levers.append(Lever(DesignVariable(name="plan.aspect_ratio", kind="real", low=0.8,
                                       high=3.0), "plan proportion", "shape"))
    levers.append(Lever(DesignVariable(name="plan.ceiling_height_m", kind="real",
                                       low=2.4, high=3.2), "ceiling height", "shape"))
    if cold:
        levers.append(Lever(DesignVariable(name="plan.azimuth_deg", kind="real",
                                           low=135.0, high=225.0),
                            "orientation of the glazed face", "shape"))
    if Facade.SOUTH in base.windows and base.windows[Facade.SOUTH].wwr > 0.0:
        levers.append(Lever(DesignVariable(name="windows.south.wwr", kind="real",
                                           low=0.08, high=0.45),
                            "south glazing area", "glazing"))
        levers.append(Lever(DesignVariable(name="windows.south.shutter.present",
                                           kind="binary"),
                            "night shutters", "glazing"))
        glazing = tuple(system.id for system in library.glazing_systems)
        if len(glazing) >= 2:
            levers.append(Lever(DesignVariable(name="windows.south.glazing_id",
                                               kind="choice", options=glazing),
                                "glazing type", "glazing"))
        arrangements = ("single_room", "sunspace", "trombe_wall")
        if base.arrangement.value in arrangements:
            levers.append(Lever(DesignVariable(name="arrangement", kind="choice",
                                               options=arrangements),
                                "arrangement", "arrangement"))
    pcms = tuple(pcm.id for pcm in library.phase_change_materials)
    if len(pcms) >= 2 or (base.pcm.pcm_id and base.pcm.pcm_id in pcms):
        levers.append(Lever(DesignVariable(name="pcm.present", kind="binary"),
                            "phase change layer", "phase change"))
    if len(pcms) >= 2:
        levers.append(Lever(DesignVariable(name="pcm.pcm_id", kind="choice",
                                           options=pcms),
                            "phase change material", "phase change"))
    return tuple(levers)


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
