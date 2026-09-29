from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import SETTINGS
from .errors import EngineError, MaterialError
from .materials import MaterialLibrary
from .schema import DesignSpec, ReferenceComparison, SimulationResult, SourceStatus


@dataclass(frozen=True)
class ReferenceCatalogue:
    designs: tuple[DesignSpec, ...]
    default_name: str
    required_material_ids: tuple[str, ...]
    required_glazing_ids: tuple[str, ...]

    def get(self, name: str) -> DesignSpec:
        for design in self.designs:
            if design.name == name:
                return design
        raise EngineError(
            f"unknown reference design {name!r}; available: "
            f"{[design.name for design in self.designs]}")

    @property
    def default(self) -> DesignSpec:
        return self.get(self.default_name)

    def names(self) -> tuple[str, ...]:
        return tuple(design.name for design in self.designs)

    def uncited(self) -> tuple[str, ...]:
        return tuple(design.name for design in self.designs
                     if design.citation is None
                     or design.citation.status is not SourceStatus.VERIFIED)


@lru_cache(maxsize=4)
def load_references(path: str | None = None) -> ReferenceCatalogue:
    source = Path(path) if path else SETTINGS.data_dir / "reference_designs.json"
    if not source.is_file():
        raise EngineError(f"reference design catalogue not found at {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    designs = tuple(DesignSpec.model_validate(item)
                    for item in payload.get("designs", []))
    if not designs:
        raise EngineError(f"{source} defines no reference designs")
    names = [design.name for design in designs]
    if len(set(names)) != len(names):
        raise EngineError(f"{source} has duplicate reference design names")
    default_name = payload.get("default_reference") or names[0]
    if default_name not in names:
        raise EngineError(
            f"{source} names {default_name!r} as the default reference but does not "
            "define it")
    for design in designs:
        if not design.is_reference:
            raise EngineError(
                f"reference design {design.name} does not carry is_reference true")
    return ReferenceCatalogue(
        designs=designs, default_name=default_name,
        required_material_ids=tuple(payload.get("required_material_ids", [])),
        required_glazing_ids=tuple(payload.get("required_glazing_ids", [])))


def check_library_covers_references(library: MaterialLibrary,
                                    catalogue: ReferenceCatalogue | None = None
                                    ) -> tuple[str, ...]:
    catalogue = catalogue or load_references()
    missing: list[str] = []
    for identifier in catalogue.required_material_ids:
        try:
            library.material(identifier)
        except MaterialError:
            missing.append(f"material:{identifier}")
    for identifier in catalogue.required_glazing_ids:
        try:
            library.glazing(identifier)
        except MaterialError:
            missing.append(f"glazing:{identifier}")
    for design in catalogue.designs:
        for construction in (design.wall, design.roof, design.floor, design.partition):
            if construction is None:
                continue
            for layer in construction.layers:
                try:
                    library.material(layer.material_id)
                except MaterialError:
                    missing.append(f"material:{layer.material_id}")
        for window in design.windows.values():
            try:
                library.glazing(window.glazing_id)
            except MaterialError:
                missing.append(f"glazing:{window.glazing_id}")
    ordered: list[str] = []
    for item in missing:
        if item not in ordered:
            ordered.append(item)
    return tuple(ordered)


def _delta(candidate: float | None, reference: float | None) -> float | None:
    if candidate is None or reference is None:
        return None
    return candidate - reference


def compare_to_reference(candidate: SimulationResult,
                         reference: SimulationResult) -> ReferenceComparison:
    fuel_saved = None
    rupees_saved = None
    if (candidate.energy.fuel_units is not None
            and reference.energy.fuel_units is not None):
        fuel_saved = reference.energy.fuel_units - candidate.energy.fuel_units
    if (candidate.energy.fuel_cost_inr is not None
            and reference.energy.fuel_cost_inr is not None):
        rupees_saved = reference.energy.fuel_cost_inr - candidate.energy.fuel_cost_inr
    return ReferenceComparison(
        reference_name=reference.design.name,
        reference_hash=reference.design_hash,
        citation=reference.design.citation,
        delta_minimum_overnight_k=_delta(
            candidate.comfort.minimum_overnight_operative_c,
            reference.comfort.minimum_overnight_operative_c),
        delta_maximum_daytime_k=_delta(
            candidate.comfort.maximum_daytime_operative_c,
            reference.comfort.maximum_daytime_operative_c),
        delta_degree_hours_kh=_delta(candidate.comfort.degree_hours_total_kh,
                                     reference.comfort.degree_hours_total_kh),
        delta_hours_below_band_h=_delta(candidate.comfort.hours_below_band,
                                        reference.comfort.hours_below_band),
        delta_heating_demand_kwh=_delta(candidate.energy.heating_demand_kwh,
                                        reference.energy.heating_demand_kwh),
        fuel_saved_units=fuel_saved,
        fuel_unit_name=candidate.energy.fuel_unit_name
        or reference.energy.fuel_unit_name,
        rupees_saved_per_year=rupees_saved,
        delta_cost_inr=_delta(candidate.cost.total_inr, reference.cost.total_inr))
