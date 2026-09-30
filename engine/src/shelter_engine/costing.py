from __future__ import annotations

from .geometry import GeometryModel
from .materials import MaterialLibrary
from .schema import (Assumption, BillOfQuantities, BuildabilityMetrics, ConstructionSpec,
                     CostBreakdown, QuantityLine,
                     DesignSpec, Facade, OptimisationConstraints, Provenance,
                     SourceRef, SourceStatus, SurfaceKind)

FRAME_COST_FACTOR = 0.5
FRAME_SOURCE = SourceRef(
    citation="Frame and sash priced as a fraction of the glazed unit cost, scaled by "
             "the frame area fraction from the glazing record. Thermally the frame is "
             "already inside the whole window U-value that EnergyPlus is given, so it "
             "is counted once, in cost only",
    status=SourceStatus.ASSUMED)
SEISMIC_SOURCE = SourceRef(
    citation="Ladakh falls in a high seismic zone under IS 1893; thick unreinforced "
             "earth or stone mass walls are thermally ideal and structurally demanding",
    status=SourceStatus.PENDING,
    note="Confirm the zone classification and the governing clause before quoting it.")


def _construction_for(polygon_kind: SurfaceKind, design: DesignSpec,
                      outside_boundary: str) -> ConstructionSpec | None:
    if polygon_kind is SurfaceKind.WINDOW:
        return None
    if polygon_kind is SurfaceKind.PARTITION or outside_boundary == "Surface":
        return design.partition or design.wall
    if polygon_kind is SurfaceKind.WALL:
        return design.wall
    if polygon_kind in (SurfaceKind.ROOF, SurfaceKind.CEILING):
        return design.roof
    if polygon_kind is SurfaceKind.FLOOR:
        return design.floor
    return None


def cost_breakdown(design: DesignSpec, model: GeometryModel,
                   library: MaterialLibrary) -> CostBreakdown:
    envelope = 0.0
    for polygon in model.polygons:
        construction = _construction_for(polygon.kind, design,
                                         polygon.outside_boundary)
        if construction is None:
            continue
        share = 0.5 if polygon.outside_boundary == "Surface" else 1.0
        envelope += (polygon.area_m2 * share
                     * library.construction_cost_inr_m2(construction))

    glazing_cost = 0.0
    shutter_cost = 0.0
    counted: set[str] = set()
    for polygon in model.windows:
        if polygon.name in counted:
            continue
        counted.add(polygon.name)
        share = 0.5 if polygon.outside_boundary == "Surface" else 1.0
        facade = polygon.facade
        spec = design.windows.get(facade) if facade is not None else None
        glazing_id = spec.glazing_id if spec is not None else None
        if glazing_id is None:
            params = design.arrangement_params
            glazing_id = params.sunspace_glazing_id or params.trombe_glazing_id
        if glazing_id is None:
            continue
        system = library.glazing(glazing_id)
        glazing_cost += (polygon.area_m2 * share * system.cost_inr_m2
                         * (1.0 + system.frame_fraction * FRAME_COST_FACTOR))
        if spec is not None and spec.shutter.present:
            shutter_cost += polygon.area_m2 * share * spec.shutter.cost_inr_m2

    pcm_cost = 0.0
    if design.pcm.present and design.pcm.pcm_id:
        pcm = library.pcm(design.pcm.pcm_id)
        target = {
            "mass_wall_inner": (SurfaceKind.WALL, SurfaceKind.PARTITION),
            "ceiling_inner": (SurfaceKind.ROOF, SurfaceKind.CEILING),
            "floor_inner": (SurfaceKind.FLOOR,),
        }[design.pcm.applied_to]
        area = sum(polygon.area_m2 for polygon in model.polygons
                   if polygon.kind in target and polygon.zone == "ROOM")
        pcm_cost = area * design.pcm.thickness_m * pcm.cost_inr_m3

    constructions = [design.wall, design.roof, design.floor]
    if design.partition is not None:
        constructions.append(design.partition)

    assumptions = [
        Assumption(name="glazing_frame_cost_factor", value=FRAME_COST_FACTOR,
                   unit="fraction of glazed unit cost per unit frame fraction",
                   source=FRAME_SOURCE, provenance=Provenance.ASSUMED),
        Assumption(name="cost_basis", unit="INR",
                   value="Delivered material cost only, excluding labour, foundations, "
                         "services and contractor margin",
                   source=SourceRef(
                       citation="Material volumes taken from the generated geometry and "
                                "priced from the Layer 0 material database delivered "
                                "cost, which already carries the transport multiplier",
                       status=SourceStatus.DERIVED),
                   provenance=Provenance.DERIVED),
    ]

    total = envelope + glazing_cost + shutter_cost + pcm_cost
    return CostBreakdown(
        envelope_inr=envelope, glazing_inr=glazing_cost, shutters_inr=shutter_cost,
        pcm_inr=pcm_cost, total_inr=total,
        max_mason_skill=library.max_mason_skill(constructions),
        non_local_material_fraction=library.non_local_fraction(constructions),
        assumptions=tuple(assumptions))


def _element(polygon_kind: SurfaceKind, outside_boundary: str) -> str:
    if polygon_kind is SurfaceKind.PARTITION or outside_boundary == "Surface":
        return "partition"
    if polygon_kind in (SurfaceKind.ROOF, SurfaceKind.CEILING):
        return "roof"
    return polygon_kind.value


def bill_of_quantities(design: DesignSpec, model: GeometryModel,
                       library: MaterialLibrary) -> BillOfQuantities:
    materials: dict[str, dict] = {}
    for polygon in model.polygons:
        construction = _construction_for(polygon.kind, design, polygon.outside_boundary)
        if construction is None:
            continue
        share = 0.5 if polygon.outside_boundary == "Surface" else 1.0
        element = _element(polygon.kind, polygon.outside_boundary)
        for layer in library.layers(construction):
            material = layer.material
            entry = materials.setdefault(material.id, {
                "material": material, "area": 0.0, "volume": 0.0, "used": set()})
            area = polygon.area_m2 * share
            entry["area"] += area
            entry["volume"] += area * layer.thickness_m
            entry["used"].add(element)
    lines = []
    for identifier, entry in sorted(materials.items()):
        material = entry["material"]
        lines.append(QuantityLine(
            item_id=identifier, name=material.name, kind="material",
            used_in=tuple(sorted(entry["used"])), area_m2=entry["area"],
            volume_m3=entry["volume"], mass_kg=entry["volume"] * material.density_kg_m3,
            locally_available=material.locally_available,
            cost_inr=entry["volume"] * material.delivered_cost_inr_m3))

    glazing: dict[str, dict] = {}
    shutters = 0.0
    shutter_cost = 0.0
    counted: set[str] = set()
    for polygon in model.windows:
        if polygon.name in counted:
            continue
        counted.add(polygon.name)
        share = 0.5 if polygon.outside_boundary == "Surface" else 1.0
        facade = polygon.facade
        spec = design.windows.get(facade) if facade is not None else None
        glazing_id = spec.glazing_id if spec is not None else None
        if glazing_id is None:
            params = design.arrangement_params
            glazing_id = params.sunspace_glazing_id or params.trombe_glazing_id
        if glazing_id is None:
            continue
        system = library.glazing(glazing_id)
        entry = glazing.setdefault(glazing_id, {"system": system, "area": 0.0,
                                                "used": set()})
        entry["area"] += polygon.area_m2 * share
        entry["used"].add(facade.value if facade is not None else "buffer")
        if spec is not None and spec.shutter.present:
            shutters += polygon.area_m2 * share
            shutter_cost += polygon.area_m2 * share * spec.shutter.cost_inr_m2
    unweighed = []
    for identifier, entry in sorted(glazing.items()):
        system = entry["system"]
        unweighed.append(system.name)
        lines.append(QuantityLine(
            item_id=identifier, name=system.name, kind="glazing",
            used_in=tuple(sorted(entry["used"])), area_m2=entry["area"],
            locally_available=system.locally_available,
            cost_inr=entry["area"] * system.cost_inr_m2
            * (1.0 + system.frame_fraction * FRAME_COST_FACTOR),
            note="The glazing record carries no pane thickness, so its weight is not "
                 "computed."))
    if shutters > 0.0:
        unweighed.append("night shutters")
        lines.append(QuantityLine(
            item_id="night_shutter", name="Insulated night shutter", kind="shutter",
            used_in=("windows",), area_m2=shutters, cost_inr=shutter_cost,
            note="Shutters are specified by thermal resistance and cost only, so their "
                 "weight is not computed."))
    if design.pcm.present and design.pcm.pcm_id:
        pcm = library.pcm(design.pcm.pcm_id)
        target = {
            "mass_wall_inner": (SurfaceKind.WALL, SurfaceKind.PARTITION),
            "ceiling_inner": (SurfaceKind.ROOF, SurfaceKind.CEILING),
            "floor_inner": (SurfaceKind.FLOOR,),
        }[design.pcm.applied_to]
        area = sum(polygon.area_m2 for polygon in model.polygons
                   if polygon.kind in target and polygon.zone == "ROOM")
        volume = area * design.pcm.thickness_m
        lines.append(QuantityLine(
            item_id=pcm.id, name=pcm.name, kind="pcm", used_in=(design.pcm.applied_to,),
            area_m2=area, volume_m3=volume, mass_kg=volume * pcm.density_kg_m3,
            cost_inr=volume * pcm.cost_inr_m3))
    weighed = [line for line in lines if line.mass_kg is not None]
    return BillOfQuantities(
        lines=tuple(lines), total_mass_kg=sum(line.mass_kg for line in weighed),
        imported_mass_kg=sum(line.mass_kg for line in weighed
                             if line.kind == "material" and not line.locally_available),
        total_cost_inr=sum(line.cost_inr for line in lines), unweighed=tuple(unweighed),
        notes=("Areas and volumes come from the generated geometry; a wall shared by two "
               "zones is counted half on each side. Costs are delivered material only, "
               "without labour, foundations or margin.",))


def material_mass_kg(design: DesignSpec, model: GeometryModel,
                     library: MaterialLibrary) -> tuple[float, float]:
    bill = bill_of_quantities(design, model, library)
    materials = [line for line in bill.lines if line.kind == "material"]
    return (sum(line.mass_kg for line in materials if not line.locally_available),
            sum(line.mass_kg for line in materials))


def buildability(design: DesignSpec, model: GeometryModel, library: MaterialLibrary,
                 constraints: OptimisationConstraints | None = None
                 ) -> BuildabilityMetrics:
    constraints = constraints or OptimisationConstraints()
    thickness = design.wall.thickness_m
    seismic = thickness >= constraints.seismic_wall_thickness_flag_m
    note = None
    if seismic:
        note = (f"The wall assembly is {thickness * 1000:.0f} mm thick. Mass walls at or "
                f"above {constraints.seismic_wall_thickness_flag_m * 1000:.0f} mm in a "
                "high seismic zone need a structural check and probably confinement or "
                "reinforcement. The thermal benefit is real; the structure is not free.")
    ventilation = design.ventilation.effective_air_change_ach >= \
        constraints.minimum_fresh_air_ach - 1e-9
    heaviest = max(library.mass_per_area(design.wall),
                   library.mass_per_area(design.roof),
                   library.mass_per_area(design.partition) if design.partition
                   else 0.0)
    return BuildabilityMetrics(
        wall_thickness_m=thickness,
        surface_to_volume=design.plan.surface_to_volume,
        seismic_flag=seismic, seismic_note=note,
        minimum_ventilation_met=ventilation,
        heaviest_wall_kg_m2=heaviest)


def envelope_u_values(design: DesignSpec, library: MaterialLibrary) -> dict[str, float]:
    values = {
        "wall": library.u_value(design.wall, "wall"),
        "roof": library.u_value(design.roof, "roof"),
        "floor": library.u_value(design.floor, "floor"),
    }
    if design.partition is not None:
        values["partition"] = library.u_value(design.partition, "wall")
    return values


def glazing_u_values(design: DesignSpec, library: MaterialLibrary) -> dict[str, float]:
    return {facade.value: library.glazing(window.glazing_id).u_value_w_m2k
            for facade, window in design.windows.items() if window.wwr > 0.0}


def window_to_wall_ratios(design: DesignSpec) -> dict[Facade, float]:
    return {facade: window.wwr for facade, window in design.windows.items()}
