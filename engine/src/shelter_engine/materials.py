from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pydantic import ValidationError

from .errors import MaterialError
from .schema import (ConstructionSpec, GlazingSystem, Material,
                     MaterialAppearance, PhaseChangeMaterial, SourceRef,
                     SourceStatus)

R_SURFACE_INSIDE_WALL = 0.13
R_SURFACE_INSIDE_ROOF = 0.10
R_SURFACE_INSIDE_FLOOR = 0.17
R_SURFACE_OUTSIDE = 0.04
R_SURFACE_SOURCE = SourceRef(
    citation="ISO 6946 surface resistances, as tabulated in the ASHRAE Handbook of "
             "Fundamentals chapter on heat transfer",
    status=SourceStatus.PENDING)

_MATERIAL_TABLE = "materials"
_GLAZING_TABLE = "glazing_systems"
_PCM_TABLE = "phase_change_materials"

DENSE_SOLID_KG_M3 = 1500.0
MIN_DENSE_CONDUCTIVITY_W_MK = 0.40
MIN_MINERAL_SPECIFIC_HEAT_J_KGK = 600.0
MAX_LIGHT_CONDUCTIVITY_W_MK = 0.20
METAL_CONDUCTIVITY_W_MK = 5.0

_APPEARANCE_KEYS = ("colour", "color", "display_colour", "display_color")
_FINISH_KEYS = ("finish", "surface_finish", "roughness_finish")


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "t"):
        return True
    if text in ("0", "false", "no", "n", "f"):
        return False
    raise MaterialError(f"cannot interpret {value!r} as a boolean")


def _pick(row: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _source_from_row(row: Mapping[str, Any], identifier: str) -> SourceRef:
    citation = _pick(row, ("source", "source_citation", "citation", "reference"))
    if citation is None or not str(citation).strip():
        raise MaterialError(
            f"material {identifier} has no source; every property must be traceable")
    status = _pick(row, ("source_status", "status"))
    try:
        parsed = SourceStatus(str(status)) if status else SourceStatus.PENDING
    except ValueError:
        parsed = SourceStatus.PENDING
    return SourceRef(citation=str(citation), status=parsed,
                     url=_pick(row, ("source_url", "url")),
                     note=_pick(row, ("source_note", "note")))


def _appearance_from_row(row: Mapping[str, Any], identifier: str) -> MaterialAppearance:
    colour = _pick(row, _APPEARANCE_KEYS)
    if colour is None:
        raise MaterialError(
            f"material {identifier} has no display colour; Layer 5 renders from it")
    finish = _pick(row, _FINISH_KEYS) or "rough"
    try:
        return MaterialAppearance(colour=str(colour), finish=str(finish))
    except ValidationError as error:
        raise MaterialError(f"material {identifier} has an invalid appearance: {error}") from error


def _range_from_row(row: Mapping[str, Any], low_key: str, high_key: str) -> dict | None:
    low = row.get(low_key)
    high = row.get(high_key)
    if low is None or high is None:
        return None
    return {"low": float(low), "high": float(high)}


def _cost_source_from_row(row: Mapping[str, Any]) -> SourceRef | None:
    citation = _pick(row, ("cost_source", "cost_citation", "price_source"))
    if citation is None or not str(citation).strip():
        return None
    status = _pick(row, ("cost_source_status", "cost_status"))
    try:
        parsed = SourceStatus(str(status)) if status else SourceStatus.PENDING
    except ValueError:
        parsed = SourceStatus.PENDING
    return SourceRef(citation=str(citation), status=parsed,
                     note=_pick(row, ("cost_note",)))


def material_from_row(row: Mapping[str, Any]) -> Material:
    identifier = str(_pick(row, ("id", "material_id", "key")) or "")
    if not identifier:
        raise MaterialError("material row has no id")
    payload: dict[str, Any] = {
        "id": identifier,
        "name": str(_pick(row, ("name", "material_name")) or identifier),
        "conductivity_w_mk": _pick(row, ("conductivity_w_mk", "conductivity", "k")),
        "density_kg_m3": _pick(row, ("density_kg_m3", "density", "rho")),
        "specific_heat_j_kgk": _pick(row, ("specific_heat_j_kgk", "specific_heat", "cp")),
        "cost_inr_m3": _pick(row, ("cost_inr_m3", "cost_per_m3", "cost")),
        "appearance": _appearance_from_row(row, identifier),
        "source": _source_from_row(row, identifier),
        "cost_source": _cost_source_from_row(row),
    }
    for key, aliases in (
            ("thermal_absorptance", ("thermal_absorptance", "emissivity")),
            ("solar_absorptance", ("solar_absorptance", "absorptivity")),
            ("visible_absorptance", ("visible_absorptance",)),
            ("roughness", ("roughness",)),
            ("transport_multiplier", ("transport_multiplier",)),
            ("mason_skill", ("mason_skill", "skill")),
    ):
        value = _pick(row, aliases)
        if value is not None:
            payload[key] = value
    available = _pick(row, ("locally_available", "local", "available_locally"))
    if available is not None:
        payload["locally_available"] = _coerce_bool(available)
    conductivity_range = _range_from_row(row, "conductivity_low", "conductivity_high")
    if conductivity_range:
        payload["conductivity_range"] = conductivity_range
    density_range = _range_from_row(row, "density_low", "density_high")
    if density_range:
        payload["density_range"] = density_range
    missing = [key for key in ("conductivity_w_mk", "density_kg_m3",
                               "specific_heat_j_kgk", "cost_inr_m3")
               if payload.get(key) is None]
    if missing:
        raise MaterialError(f"material {identifier} is missing {', '.join(missing)}")
    try:
        return Material.model_validate(payload)
    except ValidationError as error:
        raise MaterialError(f"material {identifier} is invalid: {error}") from error


def glazing_from_row(row: Mapping[str, Any]) -> GlazingSystem:
    identifier = str(_pick(row, ("id", "glazing_id", "key")) or "")
    if not identifier:
        raise MaterialError("glazing row has no id")
    payload: dict[str, Any] = {
        "id": identifier,
        "name": str(_pick(row, ("name",)) or identifier),
        "u_value_w_m2k": _pick(row, ("u_value_w_m2k", "u_value", "u")),
        "shgc": _pick(row, ("shgc", "solar_heat_gain_coefficient", "g_value")),
        "visible_transmittance": _pick(row, ("visible_transmittance", "vt", "tvis")),
        "cost_inr_m2": _pick(row, ("cost_inr_m2", "cost_per_m2", "cost")),
        "appearance": _appearance_from_row(row, identifier),
        "source": _source_from_row(row, identifier),
        "cost_source": _cost_source_from_row(row),
    }
    for key, aliases in (("layers", ("layers", "panes")),
                         ("frame_fraction", ("frame_fraction",))):
        value = _pick(row, aliases)
        if value is not None:
            payload[key] = value
    available = _pick(row, ("locally_available", "local"))
    if available is not None:
        payload["locally_available"] = _coerce_bool(available)
    missing = [key for key in ("u_value_w_m2k", "shgc", "visible_transmittance",
                               "cost_inr_m2") if payload.get(key) is None]
    if missing:
        raise MaterialError(f"glazing {identifier} is missing {', '.join(missing)}")
    try:
        return GlazingSystem.model_validate(payload)
    except ValidationError as error:
        raise MaterialError(f"glazing {identifier} is invalid: {error}") from error


def pcm_from_row(row: Mapping[str, Any]) -> PhaseChangeMaterial:
    identifier = str(_pick(row, ("id", "pcm_id", "key")) or "")
    if not identifier:
        raise MaterialError("phase change material row has no id")
    payload: dict[str, Any] = {
        "id": identifier,
        "name": str(_pick(row, ("name",)) or identifier),
        "melt_temperature_c": _pick(row, ("melt_temperature_c", "melt_temperature",
                                          "melting_point_c")),
        "latent_heat_j_kg": _pick(row, ("latent_heat_j_kg", "latent_heat")),
        "conductivity_w_mk": _pick(row, ("conductivity_w_mk", "conductivity")),
        "density_kg_m3": _pick(row, ("density_kg_m3", "density")),
        "specific_heat_j_kgk": _pick(row, ("specific_heat_j_kgk", "specific_heat")),
        "cost_inr_m3": _pick(row, ("cost_inr_m3", "cost")),
        "appearance": _appearance_from_row(row, identifier),
        "source": _source_from_row(row, identifier),
    }
    for key, aliases in (("melt_band_k", ("melt_band_k", "melt_band")),
                         ("cycle_capacity_retention", ("cycle_capacity_retention",))):
        value = _pick(row, aliases)
        if value is not None:
            payload[key] = value
    missing = [key for key, value in payload.items() if value is None]
    if missing:
        raise MaterialError(
            f"phase change material {identifier} is missing {', '.join(missing)}")
    try:
        return PhaseChangeMaterial.model_validate(payload)
    except ValidationError as error:
        raise MaterialError(f"phase change material {identifier} is invalid: {error}") from error


@dataclass(frozen=True)
class LayerProperties:
    material: Material
    thickness_m: float

    @property
    def resistance_m2k_w(self) -> float:
        return self.thickness_m / self.material.conductivity_w_mk

    @property
    def areal_heat_capacity_j_m2k(self) -> float:
        return self.thickness_m * self.material.volumetric_heat_capacity_j_m3k

    @property
    def mass_kg_m2(self) -> float:
        return self.thickness_m * self.material.density_kg_m3


def implausible(material: Material) -> list[str]:
    problems: list[str] = []
    metal = material.conductivity_w_mk >= METAL_CONDUCTIVITY_W_MK
    dense = material.density_kg_m3 >= DENSE_SOLID_KG_M3 and not metal
    if dense and material.conductivity_w_mk < MIN_DENSE_CONDUCTIVITY_W_MK:
        problems.append(
            f"conductivity {material.conductivity_w_mk} W/mK is implausibly low for a "
            f"solid at {material.density_kg_m3:.0f} kg/m3; masonry and stone at that "
            f"density normally sit above {MIN_DENSE_CONDUCTIVITY_W_MK} W/mK")
    if dense and material.specific_heat_j_kgk < MIN_MINERAL_SPECIFIC_HEAT_J_KGK:
        problems.append(
            f"specific heat {material.specific_heat_j_kgk} J/kgK is implausibly low for "
            f"a mineral solid; brick, stone and concrete sit near 800 to 950 J/kgK")
    if (material.density_kg_m3 < 200.0
            and material.conductivity_w_mk > MAX_LIGHT_CONDUCTIVITY_W_MK):
        problems.append(
            f"conductivity {material.conductivity_w_mk} W/mK is implausibly high for a "
            f"material at {material.density_kg_m3:.0f} kg/m3")
    return problems


class MaterialLibrary:
    def __init__(self, materials: Iterable[Material],
                 glazing: Iterable[GlazingSystem] = (),
                 phase_change: Iterable[PhaseChangeMaterial] = (),
                 origin: str = "memory"):
        self._materials: dict[str, Material] = {}
        self._glazing: dict[str, GlazingSystem] = {}
        self._pcm: dict[str, PhaseChangeMaterial] = {}
        self._fingerprint: str | None = None
        self.origin = origin
        for material in materials:
            if material.id in self._materials:
                raise MaterialError(f"duplicate material id {material.id}")
            self._materials[material.id] = material
        for system in glazing:
            if system.id in self._glazing:
                raise MaterialError(f"duplicate glazing id {system.id}")
            self._glazing[system.id] = system
        for pcm in phase_change:
            if pcm.id in self._pcm:
                raise MaterialError(f"duplicate phase change material id {pcm.id}")
            self._pcm[pcm.id] = pcm
        if not self._materials:
            raise MaterialError("material library is empty")

    @classmethod
    def from_sqlite(cls, path: str | Path) -> "MaterialLibrary":
        database = Path(path)
        if not database.is_file():
            raise MaterialError(f"material database not found at {database}")
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
            if _MATERIAL_TABLE not in tables:
                raise MaterialError(
                    f"{database} has no '{_MATERIAL_TABLE}' table; Layer 0 owns this file")
            materials = [material_from_row(dict(row))
                         for row in connection.execute(f"SELECT * FROM {_MATERIAL_TABLE}")]
            glazing = [glazing_from_row(dict(row))
                       for row in connection.execute(f"SELECT * FROM {_GLAZING_TABLE}")] \
                if _GLAZING_TABLE in tables else []
            pcm = [pcm_from_row(dict(row))
                   for row in connection.execute(f"SELECT * FROM {_PCM_TABLE}")] \
                if _PCM_TABLE in tables else []
        finally:
            connection.close()
        return cls(materials, glazing, pcm, origin=str(database))

    @classmethod
    def from_json(cls, path: str | Path) -> "MaterialLibrary":
        source = Path(path)
        if not source.is_file():
            raise MaterialError(f"material file not found at {source}")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise MaterialError(f"{source} is not valid JSON: {error}") from error
        if not isinstance(payload, dict):
            raise MaterialError(f"{source} must hold a JSON object")
        materials = [material_from_row(row) for row in payload.get("materials", [])]
        glazing = [glazing_from_row(row) for row in payload.get("glazing_systems", [])]
        pcm = [pcm_from_row(row) for row in payload.get("phase_change_materials", [])]
        return cls(materials, glazing, pcm, origin=str(source))

    @classmethod
    def load(cls, path: str | Path) -> "MaterialLibrary":
        source = Path(path)
        if source.suffix.lower() in (".json",):
            return cls.from_json(source)
        return cls.from_sqlite(source)

    def material(self, identifier: str) -> Material:
        try:
            return self._materials[identifier]
        except KeyError:
            raise MaterialError(
                f"unknown material {identifier!r}; the library from {self.origin} holds "
                f"{len(self._materials)} materials") from None

    def glazing(self, identifier: str) -> GlazingSystem:
        try:
            return self._glazing[identifier]
        except KeyError:
            raise MaterialError(
                f"unknown glazing system {identifier!r}; the library from {self.origin} "
                f"holds {len(self._glazing)} glazing systems") from None

    def pcm(self, identifier: str) -> PhaseChangeMaterial:
        try:
            return self._pcm[identifier]
        except KeyError:
            raise MaterialError(
                f"unknown phase change material {identifier!r}") from None

    @property
    def materials(self) -> tuple[Material, ...]:
        return tuple(self._materials.values())

    @property
    def glazing_systems(self) -> tuple[GlazingSystem, ...]:
        return tuple(self._glazing.values())

    @property
    def phase_change_materials(self) -> tuple[PhaseChangeMaterial, ...]:
        return tuple(self._pcm.values())

    @property
    def fingerprint(self) -> str:
        from .hashing import stable_hash

        if self._fingerprint is None:
            self._fingerprint = stable_hash(self.materials, self.glazing_systems,
                                            self.phase_change_materials)
        return self._fingerprint

    def implausible(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for material in self._materials.values():
            problems = implausible(material)
            if problems:
                found[material.id] = problems
        return found

    def unsourced(self) -> tuple[str, ...]:
        pending: list[str] = []
        for item in (*self._materials.values(), *self._glazing.values(),
                     *self._pcm.values()):
            if item.source.status is SourceStatus.PENDING:
                pending.append(item.id)
        return tuple(pending)

    def unpriced(self) -> tuple[str, ...]:
        missing: list[str] = []
        for item in (*self._materials.values(), *self._glazing.values(),
                     *self._pcm.values()):
            price = getattr(item, "cost_inr_m3", None)
            if price is None:
                price = getattr(item, "cost_inr_m2", None)
            if price is None:
                continue
            cost_source = getattr(item, "cost_source", None)
            if price > 0.0 and (cost_source is None
                                or cost_source.status is not SourceStatus.VERIFIED):
                missing.append(item.id)
        return tuple(missing)

    def uncertain_materials(self) -> tuple[Material, ...]:
        return tuple(m for m in self._materials.values()
                     if m.conductivity_range is not None
                     and not m.conductivity_range.is_degenerate)

    def layers(self, construction: ConstructionSpec) -> list[LayerProperties]:
        return [LayerProperties(self.material(layer.material_id), layer.thickness_m)
                for layer in construction.layers]

    def resistance(self, construction: ConstructionSpec) -> float:
        return sum(layer.resistance_m2k_w for layer in self.layers(construction))

    def u_value(self, construction: ConstructionSpec,
                element: str = "wall") -> float:
        inside = {"wall": R_SURFACE_INSIDE_WALL, "roof": R_SURFACE_INSIDE_ROOF,
                  "ceiling": R_SURFACE_INSIDE_ROOF,
                  "floor": R_SURFACE_INSIDE_FLOOR}.get(element, R_SURFACE_INSIDE_WALL)
        outside = 0.0 if element == "floor" else R_SURFACE_OUTSIDE
        total = inside + outside + self.resistance(construction)
        if total <= 0.0:
            raise MaterialError(f"construction {construction.name} has zero resistance")
        return 1.0 / total

    def areal_heat_capacity(self, construction: ConstructionSpec) -> float:
        return sum(layer.areal_heat_capacity_j_m2k for layer in self.layers(construction))

    def mass_per_area(self, construction: ConstructionSpec) -> float:
        return sum(layer.mass_kg_m2 for layer in self.layers(construction))

    def construction_cost_inr_m2(self, construction: ConstructionSpec) -> float:
        return sum(layer.thickness_m * layer.material.delivered_cost_inr_m3
                   for layer in self.layers(construction))

    def max_mason_skill(self, constructions: Iterable[ConstructionSpec]) -> str:
        order = ["unskilled", "semiskilled", "skilled", "specialist"]
        highest = 0
        for construction in constructions:
            for layer in self.layers(construction):
                highest = max(highest, order.index(layer.material.mason_skill))
        return order[highest]

    def non_local_fraction(self, constructions: Iterable[ConstructionSpec]) -> float:
        total = 0.0
        non_local = 0.0
        for construction in constructions:
            for layer in self.layers(construction):
                volume_cost = layer.thickness_m * layer.material.delivered_cost_inr_m3
                total += volume_cost
                if not layer.material.locally_available:
                    non_local += volume_cost
        if total <= 0.0:
            return 0.0
        return non_local / total
