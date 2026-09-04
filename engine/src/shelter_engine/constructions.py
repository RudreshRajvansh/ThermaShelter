from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import MaterialError
from .geometry import ConstructionNames, reversed_construction
from .materials import MaterialLibrary
from .schema import (Arrangement, Assumption, ConstructionSpec, DesignSpec,
                     EngineWarning, Facade, Provenance, Severity, ShutterSpec,
                     SourceRef, SourceStatus)

THIN_LAYER_M = 0.003
TROMBE_SOLAR_ABSORPTANCE = 0.95
SHUTTER_THICKNESS_M = 0.025
SHUTTER_SOLAR_TRANSMITTANCE = 0.0
SHUTTER_SOLAR_REFLECTANCE = 0.50
SHUTTER_IR_EMISSIVITY = 0.90
SHUTTER_GLASS_DISTANCE_M = 0.05
SHUTTER_AIR_PERMEABILITY = 0.0
MIN_NOMASS_RESISTANCE_M2K_W = 0.001
PCM_CURVE_LOW_C = -30.0
PCM_CURVE_HIGH_C = 60.0
INTERNAL_MASS_THICKNESS_M = 0.10

INTERNAL_MASS_SOURCE = SourceRef(
    citation=("Internal partitions are modelled as a 0.10 m leaf of the innermost "
              "wall material. No partition build up was supplied"),
    status=SourceStatus.ASSUMED,
    note=("Internal mass damps the swing between day and night without changing the "
          "heat lost through the envelope. Too much of it flattens the overnight "
          "minimum in a way the real building will not, so the partition area comes "
          "from the drawn layout rather than from a guess."))

TROMBE_ABSORPTANCE_SOURCE = SourceRef(
    citation="Dark selective absorber finish assumed for the Trombe mass wall; "
             "matt black paint solar absorptance from the ASHRAE Handbook of "
             "Fundamentals surface property tables",
    status=SourceStatus.ASSUMED)
SHUTTER_SOURCE = SourceRef(
    citation="Insulated night shutter modelled as an interior opaque shade; "
             "thickness and optical properties assumed, thermal resistance taken "
             "from the design specification",
    status=SourceStatus.ASSUMED)


@dataclass
class EpMaterial:
    name: str
    kind: str
    fields: dict[str, Any]
    source_id: str = ""


@dataclass
class EpConstruction:
    name: str
    layers: list[str]


@dataclass
class PhaseChangeCurve:
    material_name: str
    points: list[tuple[float, float]]


@dataclass
class ConstructionSet:
    names: ConstructionNames
    materials: list[EpMaterial] = field(default_factory=list)
    constructions: list[EpConstruction] = field(default_factory=list)
    phase_change: list[PhaseChangeCurve] = field(default_factory=list)
    shades: dict[str, str] = field(default_factory=dict)
    shutter_specs: dict[str, ShutterSpec] = field(default_factory=dict)
    assumptions: list[Assumption] = field(default_factory=list)
    warnings: list[EngineWarning] = field(default_factory=list)
    requires_conduction_finite_difference: bool = False

    def construction_names(self) -> list[str]:
        return [construction.name for construction in self.constructions]

    def material_index(self) -> dict[str, EpMaterial]:
        return {material.name: material for material in self.materials}

    def construction_index(self) -> dict[str, EpConstruction]:
        return {construction.name: construction for construction in self.constructions}


def _material_name(material_id: str, thickness_m: float, suffix: str = "") -> str:
    millimetres = round(thickness_m * 1000.0, 1)
    label = f"{material_id}__{millimetres:g}MM{suffix}".upper()
    return label.replace(" ", "_")[:100]


class _Builder:
    def __init__(self, library: MaterialLibrary):
        self.library = library
        self.materials: dict[str, EpMaterial] = {}
        self.constructions: dict[str, EpConstruction] = {}
        self.phase_change: list[PhaseChangeCurve] = []
        self.assumptions: list[Assumption] = []
        self.warnings: list[EngineWarning] = []

    def opaque_layer(self, material_id: str, thickness_m: float,
                     solar_absorptance: float | None = None,
                     suffix: str = "") -> str:
        material = self.library.material(material_id)
        absorptance = material.solar_absorptance if solar_absorptance is None \
            else solar_absorptance
        name = _material_name(material_id, thickness_m, suffix)
        if name in self.materials:
            return name
        if thickness_m < THIN_LAYER_M:
            self.warnings.append(EngineWarning(
                code="thin_layer_massless", severity=Severity.INFO, origin="materials",
                message=(f"{material.name} at {thickness_m * 1000:.1f} mm is thinner than "
                         f"{THIN_LAYER_M * 1000:.0f} mm and is modelled as a massless "
                         "resistance layer to keep the conduction transfer functions stable.")))
            resistance = max(MIN_NOMASS_RESISTANCE_M2K_W,
                             thickness_m / material.conductivity_w_mk)
            self.materials[name] = EpMaterial(name=name, kind="nomass", source_id=material_id, fields={
                "Roughness": material.roughness,
                "Thermal_Resistance": resistance,
                "Thermal_Absorptance": material.thermal_absorptance,
                "Solar_Absorptance": absorptance,
                "Visible_Absorptance": material.visible_absorptance,
            })
            return name
        self.materials[name] = EpMaterial(name=name, kind="opaque", source_id=material_id, fields={
            "Roughness": material.roughness,
            "Thickness": thickness_m,
            "Conductivity": material.conductivity_w_mk,
            "Density": material.density_kg_m3,
            "Specific_Heat": material.specific_heat_j_kgk,
            "Thermal_Absorptance": material.thermal_absorptance,
            "Solar_Absorptance": absorptance,
            "Visible_Absorptance": material.visible_absorptance,
        })
        return name

    def pcm_layer(self, pcm_id: str, thickness_m: float) -> str:
        pcm = self.library.pcm(pcm_id)
        name = _material_name(pcm_id, thickness_m, "_PCM")
        if name in self.materials:
            return name
        self.materials[name] = EpMaterial(name=name, kind="opaque", source_id=pcm_id, fields={
            "Roughness": "Smooth",
            "Thickness": thickness_m,
            "Conductivity": pcm.conductivity_w_mk,
            "Density": pcm.density_kg_m3,
            "Specific_Heat": pcm.specific_heat_j_kgk,
            "Thermal_Absorptance": 0.9,
            "Solar_Absorptance": 0.6,
            "Visible_Absorptance": 0.6,
        })
        self.phase_change.append(PhaseChangeCurve(
            material_name=name, points=phase_change_curve(
                pcm.melt_temperature_c, pcm.melt_band_k,
                pcm.effective_latent_heat_j_kg, pcm.specific_heat_j_kgk)))
        if pcm.cycle_capacity_retention < 1.0:
            self.assumptions.append(Assumption(
                name="pcm_cycle_capacity_retention", value=pcm.cycle_capacity_retention,
                unit="fraction", source=pcm.source, provenance=Provenance.LITERATURE))
        return name

    def glazing(self, glazing_id: str) -> str:
        system = self.library.glazing(glazing_id)
        name = f"GLZ_{glazing_id}".upper().replace(" ", "_")[:100]
        if name in self.materials:
            return name
        self.materials[name] = EpMaterial(name=name, kind="simple_glazing", source_id=glazing_id, fields={
            "UFactor": system.u_value_w_m2k,
            "Solar_Heat_Gain_Coefficient": system.shgc,
            "Visible_Transmittance": system.visible_transmittance,
        })
        return name

    def shade(self, shutter: ShutterSpec, tag: str) -> str:
        name = f"SHUTTER_{tag}".upper()[:100]
        if name in self.materials:
            return name
        conductivity = SHUTTER_THICKNESS_M / shutter.thermal_resistance_m2k_w
        self.materials[name] = EpMaterial(name=name, kind="shade", source_id=f"shutter_{tag}", fields={
            "Solar_Transmittance": SHUTTER_SOLAR_TRANSMITTANCE,
            "Solar_Reflectance": SHUTTER_SOLAR_REFLECTANCE,
            "Visible_Transmittance": SHUTTER_SOLAR_TRANSMITTANCE,
            "Visible_Reflectance": SHUTTER_SOLAR_REFLECTANCE,
            "Infrared_Hemispherical_Emissivity": SHUTTER_IR_EMISSIVITY,
            "Infrared_Transmittance": 0.0,
            "Thickness": SHUTTER_THICKNESS_M,
            "Conductivity": conductivity,
            "Shade_to_Glass_Distance": SHUTTER_GLASS_DISTANCE_M,
            "Top_Opening_Multiplier": SHUTTER_AIR_PERMEABILITY,
            "Bottom_Opening_Multiplier": SHUTTER_AIR_PERMEABILITY,
            "LeftSide_Opening_Multiplier": SHUTTER_AIR_PERMEABILITY,
            "RightSide_Opening_Multiplier": SHUTTER_AIR_PERMEABILITY,
            "Airflow_Permeability": SHUTTER_AIR_PERMEABILITY,
        })
        self.assumptions.append(Assumption(
            name=f"shutter_resistance_{tag.lower()}",
            value=shutter.thermal_resistance_m2k_w, unit="m2K/W",
            source=shutter.source or SHUTTER_SOURCE, provenance=Provenance.ASSUMED
            if shutter.source is None else Provenance.LITERATURE))
        return name

    def construction(self, name: str, layers: list[str],
                     with_reverse: bool = False) -> str:
        key = name.upper()[:100]
        if key not in self.constructions:
            self.constructions[key] = EpConstruction(name=key, layers=list(layers))
        if with_reverse:
            reverse = reversed_construction(key)[:100]
            if reverse not in self.constructions:
                self.constructions[reverse] = EpConstruction(
                    name=reverse, layers=list(reversed(layers)))
        return key

    def shaded_construction(self, base: str, shade_material: str) -> str:
        name = f"{base}_SHADED"[:100]
        if name not in self.constructions:
            layers = list(self.constructions[base].layers) + [shade_material]
            self.constructions[name] = EpConstruction(name=name, layers=layers)
        return name


def phase_change_curve(melt_c: float, band_k: float, latent_j_kg: float,
                       specific_heat_j_kgk: float) -> list[tuple[float, float]]:
    half = band_k / 2.0
    solidus = melt_c - half
    liquidus = melt_c + half
    low = min(PCM_CURVE_LOW_C, solidus - 20.0)
    high = max(PCM_CURVE_HIGH_C, liquidus + 20.0)
    points: list[tuple[float, float]] = []
    enthalpy = 0.0
    points.append((low, enthalpy))
    enthalpy += specific_heat_j_kgk * (solidus - low)
    points.append((solidus, enthalpy))
    enthalpy += specific_heat_j_kgk * (melt_c - solidus) + latent_j_kg / 2.0
    points.append((melt_c, enthalpy))
    enthalpy += specific_heat_j_kgk * (liquidus - melt_c) + latent_j_kg / 2.0
    points.append((liquidus, enthalpy))
    enthalpy += specific_heat_j_kgk * (high - liquidus)
    points.append((high, enthalpy))
    cleaned: list[tuple[float, float]] = []
    for temperature, value in points:
        if cleaned and temperature <= cleaned[-1][0]:
            continue
        cleaned.append((round(temperature, 4), round(value, 3)))
    if len(cleaned) < 4:
        raise MaterialError("phase change curve collapsed to fewer than four points")
    return cleaned


def _layers_outside_in(builder: _Builder, spec: ConstructionSpec,
                       solar_absorptance: float | None = None,
                       suffix: str = "") -> list[str]:
    layers: list[str] = []
    for index, layer in enumerate(spec.layers):
        override = solar_absorptance if index == 0 else None
        layers.append(builder.opaque_layer(layer.material_id, layer.thickness_m,
                                           override, suffix if index == 0 else ""))
    return layers


def build_constructions(design: DesignSpec, library: MaterialLibrary) -> ConstructionSet:
    builder = _Builder(library)
    glazing_names: dict[Facade, str] = {}
    shades: dict[str, str] = {}
    shutter_specs: dict[str, ShutterSpec] = {}

    wall_layers = _layers_outside_in(builder, design.wall)
    roof_layers = _layers_outside_in(builder, design.roof)
    floor_layers = _layers_outside_in(builder, design.floor)

    if design.pcm.present and design.pcm.pcm_id:
        pcm_layer = builder.pcm_layer(design.pcm.pcm_id, design.pcm.thickness_m)
        if design.pcm.applied_to == "mass_wall_inner":
            wall_layers = wall_layers + [pcm_layer]
        elif design.pcm.applied_to == "ceiling_inner":
            roof_layers = roof_layers + [pcm_layer]
        else:
            floor_layers = floor_layers + [pcm_layer]

    wall = builder.construction(f"{design.wall.name}_WALL", wall_layers)
    roof = builder.construction(f"{design.roof.name}_ROOF", roof_layers)
    floor = builder.construction(f"{design.floor.name}_FLOOR", floor_layers)
    ceiling = roof

    inner = design.partition.layers[-1] if design.partition else design.wall.layers[-1]
    internal_mass = builder.construction(
        "INTERNAL_PARTITION_MASS",
        [builder.opaque_layer(inner.material_id, INTERNAL_MASS_THICKNESS_M,
                              None, "_IMASS")])
    builder.assumptions.append(Assumption(
        name="internal_partition_thickness", value=INTERNAL_MASS_THICKNESS_M,
        unit="m", source=INTERNAL_MASS_SOURCE,
        provenance=Provenance.ASSUMED))

    needs_partition = design.arrangement is not Arrangement.SINGLE_ROOM
    partition = wall
    if needs_partition and design.partition is not None:
        partition_layers = _layers_outside_in(builder, design.partition)
        partition = builder.construction(f"{design.partition.name}_PARTITION",
                                         partition_layers, with_reverse=True)
    elif needs_partition:
        builder.construction(f"{design.wall.name}_WALL", wall_layers, with_reverse=True)

    trombe_wall = ""
    trombe_glazing = ""
    sunspace_glazing = ""
    if design.arrangement is Arrangement.TROMBE_WALL:
        source_spec = design.partition or design.wall
        dark_layers = _layers_outside_in(builder, source_spec,
                                         solar_absorptance=TROMBE_SOLAR_ABSORPTANCE,
                                         suffix="_DARK")
        trombe_wall = builder.construction(f"{source_spec.name}_TROMBE", dark_layers,
                                           with_reverse=True)
        builder.assumptions.append(Assumption(
            name="trombe_wall_solar_absorptance", value=TROMBE_SOLAR_ABSORPTANCE,
            unit="fraction", source=TROMBE_ABSORPTANCE_SOURCE,
            provenance=Provenance.ASSUMED))

    for facade, window in design.windows.items():
        if window.wwr <= 0.0 and design.arrangement is not Arrangement.SUNSPACE:
            continue
        material = builder.glazing(window.glazing_id)
        name = builder.construction(f"WIN_{window.glazing_id}", [material])
        glazing_names[facade] = name
        if window.shutter.present:
            shade_material = builder.shade(window.shutter, facade.value)
            builder.shaded_construction(name, shade_material)
            shades[facade.value] = shade_material
            shutter_specs[facade.value] = window.shutter

    params = design.arrangement_params
    if design.arrangement is Arrangement.SUNSPACE:
        glazing_id = params.sunspace_glazing_id or design.windows[Facade.SOUTH].glazing_id
        material = builder.glazing(glazing_id)
        sunspace_glazing = builder.construction(f"WIN_{glazing_id}", [material])
    if design.arrangement is Arrangement.TROMBE_WALL:
        south = design.windows.get(Facade.SOUTH)
        glazing_id = params.trombe_glazing_id or (south.glazing_id if south else None)
        if not glazing_id:
            raise MaterialError(
                "the Trombe wall needs a glazing system; set "
                "arrangement_params.trombe_glazing_id or provide a south window glazing")
        material = builder.glazing(glazing_id)
        trombe_glazing = builder.construction(f"WIN_{glazing_id}", [material])

    names = ConstructionNames(
        wall=wall, roof=roof, floor=floor, partition=partition, internal_mass=internal_mass, ceiling=ceiling,
        glazing=glazing_names, sunspace_glazing=sunspace_glazing,
        trombe_glazing=trombe_glazing, trombe_wall=trombe_wall,
        sunspace_floor=floor, interior_door=partition)

    return ConstructionSet(
        names=names,
        materials=list(builder.materials.values()),
        constructions=list(builder.constructions.values()),
        phase_change=builder.phase_change,
        shades=shades,
        shutter_specs=shutter_specs,
        assumptions=builder.assumptions,
        warnings=builder.warnings,
        requires_conduction_finite_difference=bool(builder.phase_change),
    )
