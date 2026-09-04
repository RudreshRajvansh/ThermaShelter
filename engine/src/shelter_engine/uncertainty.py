from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from .errors import EngineError
from .materials import MaterialLibrary
from .schema import (DesignSpec, SourceRef, SourceStatus, UncertainRange,
                     UncertaintyBand, UncertaintyContribution)

MAX_PARAMETERS = 3
MIN_RELATIVE_WIDTH = 0.02

INFILTRATION_SOURCE = SourceRef(
    citation="Infiltration depends on workmanship that cannot be known in advance, so "
             "the plausible range is carried through the result rather than a single "
             "assumed rate",
    status=SourceStatus.ASSUMED)
COMPLIANCE_SOURCE = SourceRef(
    citation="Night shutters only work if somebody closes them; performance is reported "
             "for reliable operation and for realistic partial compliance",
    status=SourceStatus.ASSUMED)

Evaluator = Callable[[DesignSpec, MaterialLibrary], float]


@dataclass(frozen=True)
class UncertainParameter:
    name: str
    label: str
    nominal: float
    low: float
    high: float
    source: SourceRef
    kind: str = "material_conductivity"
    target: str = ""

    @property
    def relative_width(self) -> float:
        if self.nominal == 0.0:
            return 0.0
        return abs(self.high - self.low) / abs(self.nominal)


def _replace_conductivity(library: MaterialLibrary, material_id: str,
                          value: float) -> MaterialLibrary:
    materials = []
    for material in library.materials:
        if material.id == material_id:
            widened = None
            if material.conductivity_range is not None:
                widened = UncertainRange(
                    low=min(material.conductivity_range.low, value),
                    high=max(material.conductivity_range.high, value))
            materials.append(material.model_copy(update={
                "conductivity_w_mk": value, "conductivity_range": widened}))
        else:
            materials.append(material)
    return MaterialLibrary(materials, library.glazing_systems,
                           library.phase_change_materials,
                           origin=f"{library.origin} (perturbed {material_id})")


def _set_infiltration(design: DesignSpec, value: float) -> DesignSpec:
    ventilation = design.ventilation
    widened = ventilation.infiltration_range
    if widened is not None and not widened.brackets(value):
        widened = UncertainRange(low=min(widened.low, value),
                                 high=max(widened.high, value))
    return design.variant(ventilation=ventilation.model_copy(update={
        "infiltration_ach": value, "infiltration_range": widened}))


def _set_compliance(design: DesignSpec, value: float) -> DesignSpec:
    windows: dict = {}
    for facade, window in design.windows.items():
        if window.shutter.present:
            shutter = window.shutter.model_copy(update={"compliance": value})
            windows[facade] = window.model_copy(update={"shutter": shutter})
        else:
            windows[facade] = window
    return design.variant(windows=windows)


def apply_parameter(design: DesignSpec, library: MaterialLibrary,
                    parameter: UncertainParameter,
                    value: float) -> tuple[DesignSpec, MaterialLibrary]:
    if parameter.kind == "material_conductivity":
        return design, _replace_conductivity(library, parameter.target, value)
    if parameter.kind == "infiltration_ach":
        return _set_infiltration(design, value), library
    if parameter.kind == "shutter_compliance":
        return _set_compliance(design, value), library
    raise EngineError(f"unknown uncertain parameter kind {parameter.kind}")


def _construction_materials(design: DesignSpec) -> dict[str, float]:
    weights: dict[str, float] = {}
    constructions = [design.wall, design.roof, design.floor]
    if design.partition is not None:
        constructions.append(design.partition)
    for construction in constructions:
        for layer in construction.layers:
            weights[layer.material_id] = weights.get(layer.material_id, 0.0) \
                + layer.thickness_m
    return weights


def select_parameters(design: DesignSpec, library: MaterialLibrary,
                      limit: int = MAX_PARAMETERS) -> list[UncertainParameter]:
    candidates: list[tuple[float, UncertainParameter]] = []
    weights = _construction_materials(design)
    for material in library.uncertain_materials():
        if material.id not in weights:
            continue
        span = material.conductivity_range
        if span is None or span.is_degenerate:
            continue
        parameter = UncertainParameter(
            name=f"conductivity::{material.id}",
            label=f"{material.name} thermal conductivity",
            nominal=material.conductivity_w_mk, low=span.low, high=span.high,
            source=material.source, kind="material_conductivity",
            target=material.id)
        if parameter.relative_width < MIN_RELATIVE_WIDTH:
            continue
        influence = parameter.relative_width * weights[material.id]
        candidates.append((influence, parameter))

    ventilation = design.ventilation
    if ventilation.infiltration_range is not None \
            and not ventilation.infiltration_range.is_degenerate:
        parameter = UncertainParameter(
            name="infiltration_ach", label="Envelope air leakage",
            nominal=ventilation.infiltration_ach,
            low=ventilation.infiltration_range.low,
            high=ventilation.infiltration_range.high,
            source=INFILTRATION_SOURCE, kind="infiltration_ach")
        candidates.append((parameter.relative_width * 1.0, parameter))

    shutters = [window.shutter for window in design.windows.values()
                if window.shutter.present]
    if shutters:
        nominal = float(np.mean([shutter.compliance for shutter in shutters]))
        if nominal > 0.0:
            parameter = UncertainParameter(
                name="shutter_compliance", label="Night shutter operation",
                nominal=nominal, low=max(0.0, min(nominal, 0.5)), high=1.0,
                source=COMPLIANCE_SOURCE, kind="shutter_compliance")
            if parameter.high - parameter.low > 1e-6:
                candidates.append((parameter.relative_width * 0.8, parameter))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [parameter for _, parameter in candidates[:max(0, limit)]]


def uncertainty_band(evaluate: Evaluator, design: DesignSpec,
                     library: MaterialLibrary,
                     parameters: Sequence[UncertainParameter],
                     metric: str, unit: str,
                     nominal_value: float | None = None,
                     lower_is_better: bool = True) -> UncertaintyBand:
    if not parameters:
        value = nominal_value if nominal_value is not None \
            else evaluate(design, library)
        return UncertaintyBand(metric=metric, unit=unit, nominal=value, low=value,
                               high=value, runs=0 if nominal_value is not None else 1)

    corners = list(itertools.product(*[(parameter.low, parameter.high)
                                       for parameter in parameters]))
    values: list[float] = []
    for corner in corners:
        variant_design, variant_library = design, library
        for parameter, value in zip(parameters, corner):
            variant_design, variant_library = apply_parameter(
                variant_design, variant_library, parameter, value)
        values.append(evaluate(variant_design, variant_library))

    array = np.asarray(values, dtype=float)
    if not np.isfinite(array).all():
        raise EngineError("an uncertainty corner run produced a non finite metric")

    contributions: list[UncertaintyContribution] = []
    effects: list[float] = []
    for index, parameter in enumerate(parameters):
        high_mask = np.array([corner[index] == parameter.high for corner in corners])
        effect = float(array[high_mask].mean() - array[~high_mask].mean())
        effects.append(abs(effect))
        contributions.append(UncertaintyContribution(
            parameter=parameter.label, low_value=parameter.low,
            high_value=parameter.high, effect_on_metric=effect, share=0.0))
    total = float(sum(effects))
    if total > 0.0:
        contributions = [item.model_copy(update={"share": effects[index] / total})
                         for index, item in enumerate(contributions)]
    dominant = None
    if contributions:
        dominant = max(contributions, key=lambda item: item.share).parameter

    nominal = nominal_value if nominal_value is not None \
        else evaluate(design, library)
    low = float(array.min())
    high = float(array.max())
    return UncertaintyBand(
        metric=metric, unit=unit, nominal=nominal,
        low=min(low, nominal), high=max(high, nominal),
        dominant_parameter=dominant,
        contributions=tuple(sorted(contributions, key=lambda item: item.share,
                                   reverse=True)),
        runs=len(corners) + (0 if nominal_value is not None else 1))


def describe_band(band: UncertaintyBand, decimals: int = 1) -> str:
    span = f"{band.low:.{decimals}f} to {band.high:.{decimals}f} {band.unit}"
    if band.dominant_parameter and band.high > band.low:
        return f"{span}, driven mainly by {band.dominant_parameter.lower()}"
    return span
