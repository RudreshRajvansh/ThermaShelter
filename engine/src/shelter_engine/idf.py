from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .air_quality import (LIMIT_SOURCE, OUTDOOR_CO2_SOURCE, PERSILY_SOURCE,
                          PRESSURE_SOURCE, FreshAirSizing, size_fresh_air)
from .config import EnergyPlusInstall, find_energyplus
from .constructions import INTERNAL_MASS_SOURCE, ConstructionSet
from .errors import ConfigurationError, EngineError
from .geometry import GeometryModel, Polygon, ZoneModel
from .programme import layout_for
from .schema import (Arrangement, Assumption, DesignSpec, EngineWarning, Facade,
                     Provenance, RunOptions, Severity, SiteSpec, SourceRef,
                     SourceStatus)

FRACTION_LIMITS = "FRACTION"
TEMPERATURE_LIMITS = "TEMPERATURE"
ACTIVITY_LIMITS = "ACTIVITY LEVEL"
CONTROL_LIMITS = "CONTROL TYPE"
ALWAYS_ON = "ALWAYS_ON"
ALWAYS_OFF = "ALWAYS_OFF"
OCCUPANCY_SCHEDULE = "OCCUPANCY_FRACTION"
ACTIVITY_SCHEDULE = "OCCUPANT_ACTIVITY"
EQUIPMENT_SCHEDULE = "EQUIPMENT_FRACTION"
LIGHTING_SCHEDULE = "LIGHTING_FRACTION"
INFILTRATION_SCHEDULE = "INFILTRATION_FRACTION"
FRESH_AIR_SCHEDULE = "FRESH_AIR_FRACTION"
NIGHT_VENT_SCHEDULE = "NIGHT_VENTILATION_FRACTION"
COUPLING_SCHEDULE = "ZONE_COUPLING_FRACTION"
HEATING_SETPOINT_SCHEDULE = "HEATING_SETPOINT"
COOLING_SETPOINT_SCHEDULE = "COOLING_SETPOINT"
CONTROL_TYPE_SCHEDULE = "THERMOSTAT_CONTROL_TYPE"
SHUTTER_SCHEDULE_PREFIX = "SHUTTER_CLOSED_"
MOISTURE_SCHEDULE = "ADDED_MOISTURE_FRACTION"
OUTDOOR_CO2_SCHEDULE = "OUTDOOR_CO2_PPM"
PPM_LIMITS = "CONCENTRATION PPM"
LATENT_HEAT_OF_VAPORISATION_J_KG = 2_450_000.0

DAY_ORDER = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")

CONDFD_TIMESTEP_PER_HOUR = 20
DEFAULT_WARMUP_DAYS = 7
IDEAL_LOADS_MAX_SUPPLY_C = 50.0
IDEAL_LOADS_MIN_SUPPLY_C = 13.0
HEATING_OFF_C = -60.0
KIVA_FOUNDATION = "SLAB_ON_GRADE"
KIVA_SOIL = {"conductivity_w_mk": 1.731, "density_kg_m3": 1842.3,
             "specific_heat_j_kgk": 418.7}
KIVA_MIN_CELL_M = 0.2
KIVA_CELL_GROWTH = 3.0
KIVA_DEEP_GROUND_M = 40.0
KIVA_FAR_FIELD_M = 40.0
EDGE_TOLERANCE_M = 1e-4

SURFACE_VARIABLES = (
    "Surface Inside Face Temperature",
    "Surface Outside Face Temperature",
    "Surface Inside Face Conduction Heat Transfer Rate",
    "Surface Outside Face Incident Solar Radiation Rate per Area",
)
WINDOW_VARIABLES = (
    "Surface Window Heat Loss Energy",
    "Surface Window Heat Gain Energy",
    "Surface Window Transmitted Solar Radiation Energy",
    "Surface Window Inside Face Glazing Condensation Status",
)
ZONE_VARIABLES = (
    "Zone Mean Air Temperature",
    "Zone Operative Temperature",
    "Zone Mean Radiant Temperature",
    "Zone Air Relative Humidity",
    "Zone Air Humidity Ratio",
    "Zone Infiltration Sensible Heat Loss Energy",
    "Zone Infiltration Sensible Heat Gain Energy",
    "Zone Ventilation Sensible Heat Loss Energy",
    "Zone Ventilation Sensible Heat Gain Energy",
    "Zone Mixing Sensible Heat Loss Energy",
    "Zone Mixing Sensible Heat Gain Energy",
    "Zone Windows Total Transmitted Solar Radiation Energy",
    "Enclosure Windows Total Transmitted Solar Radiation Energy",
    "Zone Total Internal Total Heating Energy",
    "Zone Total Internal Convective Heating Rate",
    "Zone Infiltration Air Change Rate",
    "Zone Infiltration Standard Density Volume Flow Rate",
    "Zone Ventilation Air Change Rate",
    "Zone Ventilation Standard Density Volume Flow Rate",
    "Zone Mixing Current Density Volume Flow Rate",
    "Zone Mixing Mass Flow Rate",
    "Zone Air CO2 Concentration",
)
SITE_VARIABLES = (
    "Site Outdoor Air Drybulb Temperature",
    "Site Outdoor Air Relative Humidity",
    "Site Outdoor Air Barometric Pressure",
    "Site Wind Speed",
    "Site Direct Solar Radiation Rate per Area",
    "Site Diffuse Solar Radiation Rate per Area",
)
IDEAL_LOADS_VARIABLES = (
    "Zone Ideal Loads Supply Air Total Heating Energy",
    "Zone Ideal Loads Supply Air Total Cooling Energy",
    "Zone Ideal Loads Supply Air Sensible Heating Rate",
)

SETPOINT_SOURCE = SourceRef(
    citation="Heating runs only in the night window and only up to the lower edge of the "
             "DRDO 17 plus or minus 3 C band, so the demand reported is the least heat "
             "that keeps the occupied night at 14 C; outside the window the heater is off. "
             "The conditioned run sizes that remaining demand and does not affect the free "
             "floating comfort result",
    status=SourceStatus.DERIVED,
    url="https://drdo.gov.in/drdo/sites/default/files/tot/DOSTTbSfSHoSiCC11022025_0.pdf")
COUPLING_SOURCE = SourceRef(
    citation="Inter zone air exchange modelled as a scheduled cross mixing flow rather "
             "than a full airflow network; the assumed rate is recorded here",
    status=SourceStatus.ASSUMED)
OVERHANG_SOURCE = SourceRef(
    citation="Window overhangs modelled as horizontal projections above the glazing, "
             "sized from the design specification; they block high summer sun and pass "
             "low winter sun, which is why they belong to the hot dry lever set and not "
             "to the Ladakh one",
    status=SourceStatus.DERIVED)
MOISTURE_SOURCE = SourceRef(
    citation="Moisture released by cooking, washing and drying inside the shelter, "
             "entered as a latent gain on top of what the occupants exhale; occupant "
             "respiration is already carried by the EnergyPlus People model",
    status=SourceStatus.ASSUMED)
KIVA_SOURCE = SourceRef(
    citation="EnergyPlus Foundation:Kiva two dimensional ground heat transfer under the "
             "real floor, with the soil properties, far field width, deep ground depth and "
             "mesh settings of the ZoneCoupledKivaSlab example shipped with EnergyPlus 24.2",
    status=SourceStatus.DERIVED,
    note=("Replaces the monthly ground temperature boundary, which the EnergyPlus "
          "documentation warns against for floor losses. The soil properties are the "
          "EnergyPlus defaults, not a Ladakh soil survey."))
LATENT_SOURCE = SourceRef(
    citation="Occupant latent heat and moisture release follow the EnergyPlus People "
             "model driven by the activity level schedule",
    status=SourceStatus.DERIVED)


@dataclass
class IdfBundle:
    text: str
    zone_names: tuple[str, ...]
    conditioned_zone_names: tuple[str, ...]
    ideal_loads_names: tuple[str, ...]
    surface_names: tuple[str, ...]
    timestep_per_hour: int
    assumptions: list[Assumption] = field(default_factory=list)
    warnings: list[EngineWarning] = field(default_factory=list)
    fresh_air: FreshAirSizing | None = None
    ventilation_design_l_s: float = 0.0
    infiltration_design_l_s: float = 0.0

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.text, encoding="utf-8")
        return target


def _idf_class(install: EnergyPlusInstall):
    try:
        from eppy.modeleditor import IDF
    except ImportError as error:
        raise ConfigurationError(
            "eppy is required to build EnergyPlus input files; install the engine "
            "dependencies") from error
    current = getattr(IDF, "iddname", None)
    if current is None:
        IDF.setiddname(str(install.idd))
    elif Path(current).resolve() != install.idd.resolve():
        IDF.setiddname(str(install.idd), testing=True)
    return IDF


_IDF_RESERVED = str.maketrans({",": " ", ";": " ", "!": " "})


def idf_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return " ".join(value.translate(_IDF_RESERVED).split())


def _new(idf, object_type: str, **fields: Any):
    obj = idf.newidfobject(object_type.upper())
    valid = set(obj.fieldnames)
    for name, value in fields.items():
        if value is None:
            continue
        if name not in valid:
            raise EngineError(
                f"EnergyPlus object {object_type} has no field {name!r} in this "
                f"version; available fields are {sorted(valid)}")
        setattr(obj, name, idf_text(value))
    return obj


def _field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return format(value, ".10g")
    return str(value).strip()


def serialise(idf) -> str:
    blocks: list[str] = []
    for key in idf.idfobjects:
        for obj in idf.idfobjects[key]:
            values = [_field_text(item) for item in obj.obj]
            if not values:
                continue
            while len(values) > 1 and values[-1] == "":
                values.pop()
            head = values[0].upper()
            if len(values) == 1:
                blocks.append(f"{head};")
                continue
            names = list(obj.fieldnames)
            lines = [f"{head},"]
            last = len(values) - 1
            for index in range(1, len(values)):
                terminator = ";" if index == last else ","
                label = (names[index].replace("_", " ") if index < len(names)
                         else f"Field {index}")
                lines.append(f"    {values[index]}{terminator}".ljust(34)
                             + f"!- {label}")
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _append_extensible(obj, values: Sequence[Any]) -> None:
    for value in values:
        obj.obj.append(idf_text(value))


def _schedule_type_limits(idf) -> None:
    _new(idf, "ScheduleTypeLimits", Name=FRACTION_LIMITS, Lower_Limit_Value=0.0,
         Upper_Limit_Value=1.0, Numeric_Type="Continuous", Unit_Type="Dimensionless")
    _new(idf, "ScheduleTypeLimits", Name=TEMPERATURE_LIMITS, Lower_Limit_Value=-100.0,
         Upper_Limit_Value=200.0, Numeric_Type="Continuous", Unit_Type="Temperature")
    _new(idf, "ScheduleTypeLimits", Name=ACTIVITY_LIMITS, Lower_Limit_Value=0.0,
         Upper_Limit_Value=2000.0, Numeric_Type="Continuous",
         Unit_Type="ActivityLevel")
    _new(idf, "ScheduleTypeLimits", Name=CONTROL_LIMITS, Lower_Limit_Value=0,
         Upper_Limit_Value=4, Numeric_Type="Discrete", Unit_Type="Control")
    _new(idf, "ScheduleTypeLimits", Name=PPM_LIMITS, Lower_Limit_Value=0.0,
         Upper_Limit_Value=100000.0, Numeric_Type="Continuous",
         Unit_Type="Dimensionless")


def _constant_schedule(idf, name: str, value: float, limits: str) -> str:
    _new(idf, "Schedule:Constant", Name=name, Schedule_Type_Limits_Name=limits,
         Hourly_Value=value)
    return name


def _compact_schedule(idf, name: str, limits: str,
                      blocks: Sequence[tuple[Sequence[str], Sequence[tuple[int, float]]]]
                      ) -> str:
    obj = _new(idf, "Schedule:Compact", Name=name, Schedule_Type_Limits_Name=limits)
    fields: list[Any] = ["Through: 12/31"]
    for day_types, steps in blocks:
        fields.append("For: " + " ".join(day_types))
        last = 0
        for until_hour, value in steps:
            if until_hour <= last and until_hour != 24:
                continue
            fields.append(f"Until: {until_hour:02d}:00")
            fields.append(value)
            last = until_hour
        if last < 24:
            fields.append("Until: 24:00")
            fields.append(steps[-1][1] if steps else 0.0)
    _append_extensible(obj, fields)
    return name


def _hour_profile(active_hours: Sequence[int], on_value: float,
                  off_value: float) -> list[tuple[int, float]]:
    active = set(int(hour) % 24 for hour in active_hours)
    steps: list[tuple[int, float]] = []
    current = on_value if 0 in active else off_value
    for hour in range(24):
        value = on_value if hour in active else off_value
        if value != current:
            steps.append((hour, current))
            current = value
    steps.append((24, current))
    return steps


def _all_days_schedule(idf, name: str, active_hours: Sequence[int],
                       on_value: float = 1.0, off_value: float = 0.0,
                       limits: str = FRACTION_LIMITS) -> str:
    return _compact_schedule(idf, name, limits,
                             [(("AllDays",), _hour_profile(active_hours, on_value,
                                                           off_value))])


def _compliance_days(compliance: float) -> tuple[tuple[str, ...], tuple[str, ...]]:
    closed_count = int(round(max(0.0, min(1.0, compliance)) * len(DAY_ORDER)))
    closed = DAY_ORDER[:closed_count]
    open_days = DAY_ORDER[closed_count:]
    return closed, open_days


def _shutter_schedule(idf, name: str, hours: Sequence[int],
                      compliance: float) -> str:
    if compliance >= 0.999:
        return _all_days_schedule(idf, name, hours)
    if compliance <= 0.001:
        return _constant_schedule(idf, name, 0.0, FRACTION_LIMITS)
    closed_days, open_days = _compliance_days(compliance)
    blocks: list[tuple[Sequence[str], Sequence[tuple[int, float]]]] = []
    if closed_days:
        blocks.append((closed_days, _hour_profile(hours, 1.0, 0.0)))
    if open_days:
        blocks.append((open_days, [(24, 0.0)]))
    blocks.append((("AllOtherDays",), [(24, 0.0)]))
    return _compact_schedule(idf, name, FRACTION_LIMITS, blocks)


def _add_materials(idf, constructions: ConstructionSet) -> None:
    for material in constructions.materials:
        if material.kind == "opaque":
            _new(idf, "Material", Name=material.name, **material.fields)
        elif material.kind == "nomass":
            _new(idf, "Material:NoMass", Name=material.name, **material.fields)
        elif material.kind == "simple_glazing":
            _new(idf, "WindowMaterial:SimpleGlazingSystem", Name=material.name,
                 **material.fields)
        elif material.kind == "shade":
            _new(idf, "WindowMaterial:Shade", Name=material.name, **material.fields)
        else:
            raise EngineError(f"unknown material kind {material.kind}")
    for construction in constructions.constructions:
        obj = _new(idf, "Construction", Name=construction.name,
                   Outside_Layer=construction.layers[0])
        for index, layer in enumerate(construction.layers[1:], start=2):
            attribute = f"Layer_{index}"
            if attribute not in obj.fieldnames:
                raise EngineError(
                    f"construction {construction.name} has more layers than this "
                    "EnergyPlus version accepts")
            setattr(obj, attribute, layer)
    for curve in constructions.phase_change:
        obj = _new(idf, "MaterialProperty:PhaseChange", Name=curve.material_name,
                   Temperature_Coefficient_for_Thermal_Conductivity=0.0)
        fields: list[Any] = []
        for temperature, enthalpy in curve.points:
            fields.extend((temperature, enthalpy))
        _append_extensible(obj, fields)


def _add_zone(idf, zone: ZoneModel) -> None:
    fields: dict[str, Any] = {
        "Name": zone.name,
        "Direction_of_Relative_North": 0.0,
        "X_Origin": 0.0, "Y_Origin": 0.0, "Z_Origin": 0.0,
        "Type": 1, "Multiplier": 1,
        "Ceiling_Height": zone.ceiling_height_m,
        "Volume": zone.volume_m3,
        "Floor_Area": zone.floor_area_m2,
        "Part_of_Total_Floor_Area": "Yes" if zone.conditioned else "No",
    }
    if zone.inside_convection_algorithm:
        fields["Zone_Inside_Convection_Algorithm"] = zone.inside_convection_algorithm
    _new(idf, "Zone", **fields)


def _edge_overlap(a: tuple[tuple[float, float], tuple[float, float]],
                  b: tuple[tuple[float, float], tuple[float, float]]) -> float:
    (ax, ay), (bx, by) = a
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5
    if length <= EDGE_TOLERANCE_M:
        return 0.0
    ux, uy = dx / length, dy / length
    projections = []
    for px, py in b:
        rx, ry = px - ax, py - ay
        if abs(rx * uy - ry * ux) > EDGE_TOLERANCE_M * 10:
            return 0.0
        projections.append(rx * ux + ry * uy)
    low, high = sorted(projections)
    return max(0.0, min(high, length) - max(low, 0.0))


def _edges(polygon: Polygon) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    points = [(float(x), float(y)) for x, y, _ in polygon.vertices]
    return [(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]


def exposed_perimeter_m(floor: Polygon, floors: Sequence[Polygon]) -> float:
    total = 0.0
    for edge in _edges(floor):
        (ax, ay), (bx, by) = edge
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        shared = sum(_edge_overlap(edge, other_edge)
                     for other in floors if other is not floor
                     for other_edge in _edges(other))
        total += max(0.0, length - min(shared, length))
    return total


def _add_surface(idf, polygon: Polygon, foundation: str | None = None) -> None:
    surface_type = {
        "wall": "Wall", "partition": "Wall", "roof": "Roof",
        "ceiling": "Ceiling", "floor": "Floor",
    }[polygon.kind.value]
    if polygon.kind.value == "roof" and polygon.outside_boundary != "Outdoors":
        surface_type = "Ceiling"
    outside = polygon.outside_boundary
    on_foundation = foundation is not None and outside == "Ground"
    obj = _new(
        idf, "BuildingSurface:Detailed",
        Name=polygon.name, Surface_Type=surface_type,
        Construction_Name=polygon.construction, Zone_Name=polygon.zone,
        Outside_Boundary_Condition="Foundation" if on_foundation else outside,
        Outside_Boundary_Condition_Object=(
            foundation if on_foundation
            else polygon.boundary_object if outside == "Surface" else ""),
        Sun_Exposure="SunExposed" if polygon.sun_exposed else "NoSun",
        Wind_Exposure="WindExposed" if polygon.wind_exposed else "NoWind",
        View_Factor_to_Ground="autocalculate",
        Number_of_Vertices=len(polygon.vertices))
    _set_vertices(obj, polygon.vertices)


def _add_window(idf, polygon: Polygon) -> None:
    obj = _new(
        idf, "FenestrationSurface:Detailed",
        Name=polygon.name, Surface_Type="Window",
        Construction_Name=polygon.construction,
        Building_Surface_Name=polygon.parent,
        Outside_Boundary_Condition_Object=(polygon.boundary_object
                                           if polygon.outside_boundary == "Surface"
                                           else ""),
        View_Factor_to_Ground="autocalculate",
        Frame_and_Divider_Name="", Multiplier=1,
        Number_of_Vertices=len(polygon.vertices))
    _set_vertices(obj, polygon.vertices)


def _set_vertices(obj, vertices: Sequence[tuple[float, float, float]]) -> None:
    for index, (x, y, z) in enumerate(vertices, start=1):
        for axis, value in (("Xcoordinate", x), ("Ycoordinate", y), ("Zcoordinate", z)):
            attribute = f"Vertex_{index}_{axis}"
            if attribute in obj.fieldnames:
                setattr(obj, attribute, round(float(value), 6))
            else:
                obj.obj.append(round(float(value), 6))


def _add_shading(idf, model: GeometryModel) -> None:
    for shade in model.shading:
        obj = _new(idf, "Shading:Site:Detailed", Name=shade.name,
                   Transmittance_Schedule_Name=shade.transmittance_schedule or "",
                   Number_of_Vertices=len(shade.vertices))
        _set_vertices(obj, shade.vertices)


ZONE_FIELD_ALIASES = ("Zone_or_ZoneList_or_Space_or_SpaceList_Name",
                      "Zone_or_ZoneList_Name", "Zone_or_Space_Name", "Zone_Name")


def _set_first(obj, candidates: Iterable[str], value: Any) -> str:
    valid = set(obj.fieldnames)
    for name in candidates:
        if name in valid:
            setattr(obj, name, idf_text(value))
            return name
    raise EngineError(
        f"none of {list(candidates)} is a field of {obj.key}; available fields are "
        f"{sorted(valid)}")


def _add_internal_mass(idf, design: DesignSpec, model: GeometryModel,
                       constructions: ConstructionSet,
                       assumptions: list[Assumption]) -> float:
    layout = layout_for(design.plan)
    area = layout.partition_area_m2
    if area <= 0.0 or not constructions.names.internal_mass:
        return 0.0
    mass = _new(idf, "InternalMass", Name="ROOM_PARTITIONS",
                Construction_Name=constructions.names.internal_mass,
                Surface_Area=round(area, 3))
    _set_first(mass, ZONE_FIELD_ALIASES, model.zone("ROOM").name)
    assumptions.append(Assumption(
        name="internal_partition_area", value=round(area, 2), unit="m2",
        source=INTERNAL_MASS_SOURCE, provenance=Provenance.DERIVED))
    return area


def _add_internal_gains(idf, design: DesignSpec, model: GeometryModel,
                        assumptions: list[Assumption],
                        fresh_air: FreshAirSizing | None) -> None:
    occupancy = design.occupancy
    room = model.zone("ROOM")
    if occupancy.unoccupied or occupancy.occupants == 0:
        return
    people = _new(idf, "People", Name="ROOM_PEOPLE",
                  Number_of_People_Schedule_Name=OCCUPANCY_SCHEDULE,
                  Number_of_People_Calculation_Method="People",
                  Number_of_People=occupancy.occupants,
                  Fraction_Radiant=0.3,
                  Activity_Level_Schedule_Name=ACTIVITY_SCHEDULE,
                  Carbon_Dioxide_Generation_Rate=(fresh_air.co2_m3_s_per_w
                                                  if fresh_air else None))
    _set_first(people, ZONE_FIELD_ALIASES, room.name)

    if occupancy.lighting_w > 0.0:
        lights = _new(idf, "Lights", Name="ROOM_LIGHTS",
                      Schedule_Name=LIGHTING_SCHEDULE,
                      Design_Level_Calculation_Method="LightingLevel",
                      Lighting_Level=occupancy.lighting_w,
                      Fraction_Radiant=0.42, Fraction_Visible=0.18)
        _set_first(lights, ZONE_FIELD_ALIASES, room.name)

    equipment_w = occupancy.equipment_w + occupancy.cooking_w
    if equipment_w > 0.0:
        equipment = _new(idf, "ElectricEquipment", Name="ROOM_EQUIPMENT",
                         Schedule_Name=EQUIPMENT_SCHEDULE,
                         Design_Level_Calculation_Method="EquipmentLevel",
                         Design_Level=equipment_w, Fraction_Radiant=0.3,
                         Fraction_Latent=0.0, Fraction_Lost=0.0)
        _set_first(equipment, ZONE_FIELD_ALIASES, room.name)
    assumptions.append(Assumption(
        name="occupant_latent_heat_model", value="EnergyPlus People activity level",
        source=LATENT_SOURCE, provenance=Provenance.DERIVED))


def _add_airflow(idf, design: DesignSpec, model: GeometryModel,
                 assumptions: list[Assumption],
                 fresh_air: FreshAirSizing | None) -> tuple[float, float]:
    ventilation = design.ventilation
    for zone in model.zones:
        rate = ventilation.infiltration_ach
        infiltration = _new(
            idf, "ZoneInfiltration:DesignFlowRate",
            Name=f"{zone.name}_INFILTRATION",
            Schedule_Name=INFILTRATION_SCHEDULE,
            Design_Flow_Rate_Calculation_Method="AirChanges/Hour",
            Air_Changes_per_Hour=rate,
            Constant_Term_Coefficient=1.0, Temperature_Term_Coefficient=0.0,
            Velocity_Term_Coefficient=0.0, Velocity_Squared_Term_Coefficient=0.0)
        _set_first(infiltration, ZONE_FIELD_ALIASES, zone.name)

    room = model.zone("ROOM")
    infiltration_l_s = ventilation.infiltration_ach * room.volume_m3 / 3.6
    floor_l_s = ventilation.minimum_fresh_air_ach * room.volume_m3 / 3.6
    people = 0 if design.occupancy.unoccupied else design.occupancy.occupants
    per_person = (ventilation.fresh_air_l_s_per_person
                  or (fresh_air.fresh_air_l_s_per_person if fresh_air else 0.0))
    required_l_s = max(floor_l_s, people * per_person)
    top_up_l_s = max(0.0, required_l_s - infiltration_l_s)
    if top_up_l_s > 0.0:
        fresh = _new(idf, "ZoneVentilation:DesignFlowRate", Name="ROOM_FRESH_AIR",
                     Schedule_Name=OCCUPANCY_SCHEDULE if people else FRESH_AIR_SCHEDULE,
                     Design_Flow_Rate_Calculation_Method="Flow/Zone",
                     Design_Flow_Rate=round(top_up_l_s / 1000.0, 6),
                     Ventilation_Type="Natural",
                     Constant_Term_Coefficient=1.0,
                     Temperature_Term_Coefficient=0.0,
                     Velocity_Term_Coefficient=0.0,
                     Velocity_Squared_Term_Coefficient=0.0)
        _set_first(fresh, ZONE_FIELD_ALIASES, room.name)
    if people and per_person > 0.0:
        chosen = ventilation.fresh_air_l_s_per_person is not None
        assumptions.append(Assumption(
            name="fresh_air_l_s_per_person", value=round(per_person, 2),
            unit="L/s per person",
            source=(SourceRef(citation="Fresh air per person set on the design",
                              status=SourceStatus.ASSUMED) if chosen else PERSILY_SOURCE),
            provenance=Provenance.ASSUMED if chosen else Provenance.DERIVED))
        assumptions.append(Assumption(
            name="fresh_air_top_up_l_s", value=round(top_up_l_s, 2), unit="L/s",
            source=SourceRef(
                citation=(f"{people} occupants at {per_person:.1f} L/s each need "
                          f"{people * per_person:.1f} L/s of outdoor air; leakage supplies "
                          f"{infiltration_l_s:.1f} L/s, so ventilation adds the rest while "
                          "the shelter is occupied. Air that leaks in already counts as "
                          "fresh, so sealing the shelter below this does not make it "
                          "warmer at night: the ventilation makes up the difference."),
                status=SourceStatus.DERIVED), provenance=Provenance.DERIVED))
    if floor_l_s > people * per_person and floor_l_s > 0.0:
        assumptions.append(Assumption(
            name="minimum_fresh_air_ach", value=ventilation.minimum_fresh_air_ach,
            unit="air changes per hour", source=SourceRef(
                citation="Minimum air change rate set on the design, which here asks for "
                         "more air than the occupants' CO2 does",
                status=SourceStatus.ASSUMED), provenance=Provenance.ASSUMED))
    if ventilation.night_ventilation and ventilation.night_ventilation_ach > 0.0:
        night = _new(idf, "ZoneVentilation:DesignFlowRate",
                     Name="ROOM_NIGHT_VENTILATION",
                     Schedule_Name=NIGHT_VENT_SCHEDULE,
                     Design_Flow_Rate_Calculation_Method="AirChanges/Hour",
                     Air_Changes_per_Hour=ventilation.night_ventilation_ach,
                     Ventilation_Type="Natural",
                     Constant_Term_Coefficient=1.0,
                     Temperature_Term_Coefficient=0.0,
                     Velocity_Term_Coefficient=0.0,
                     Velocity_Squared_Term_Coefficient=0.0)
        _set_first(night, ZONE_FIELD_ALIASES, room.name)

    params = design.arrangement_params
    partner = {Arrangement.SUNSPACE: "SUNSPACE",
               Arrangement.TROMBE_WALL: "TROMBE_GAP",
               Arrangement.VESTIBULE: "VESTIBULE"}.get(design.arrangement)
    if partner and params.coupling_ach > 0.0:
        volume = min(room.volume_m3, model.zone(partner).volume_m3)
        flow = params.coupling_ach * volume / 3600.0
        gated = design.arrangement in (Arrangement.SUNSPACE, Arrangement.TROMBE_WALL)
        delta = params.coupling_delta_t_k if gated else 0.0
        mixing = _new(idf, "ZoneCrossMixing", Name=f"ROOM_{partner}_COUPLING",
                      Schedule_Name=COUPLING_SCHEDULE,
                      Design_Flow_Rate_Calculation_Method="Flow/Zone",
                      Design_Flow_Rate=flow, Delta_Temperature=delta)
        _set_first(mixing, ZONE_FIELD_ALIASES, room.name)
        _set_first(mixing, ("Source_Zone_or_Space_Name", "Source_Zone_Name"), partner)
        assumptions.append(Assumption(
            name="interzone_coupling_ach", value=params.coupling_ach,
            unit="air changes per hour", source=params.coupling_source or COUPLING_SOURCE,
            provenance=Provenance.ASSUMED))
        if gated:
            assumptions.append(Assumption(
                name="interzone_coupling_gate_k", value=delta, unit="K",
                source=SourceRef(
                    citation=(f"Air moves between the room and the {partner.lower()} only "
                              f"while the {partner.lower()} is at least {delta:g} K warmer, "
                              "as a door or vent that is opened when it brings heat in and "
                              "shut when it would let heat out"),
                    status=SourceStatus.ASSUMED), provenance=Provenance.ASSUMED))
    return top_up_l_s, infiltration_l_s


def _add_foundation(idf, model: GeometryModel,
                    assumptions: list[Assumption]) -> str | None:
    floors = [polygon for polygon in model.opaque
              if polygon.kind.value == "floor" and polygon.outside_boundary == "Ground"]
    if not floors:
        return None
    _new(idf, "Foundation:Kiva:Settings",
         Soil_Conductivity=KIVA_SOIL["conductivity_w_mk"],
         Soil_Density=KIVA_SOIL["density_kg_m3"],
         Soil_Specific_Heat=KIVA_SOIL["specific_heat_j_kgk"],
         Ground_Solar_Absorptivity=0.9, Ground_Thermal_Absorptivity=0.9,
         Ground_Surface_Roughness=0.03, FarField_Width=KIVA_FAR_FIELD_M,
         DeepGround_Boundary_Condition="ZeroFlux",
         DeepGround_Depth=KIVA_DEEP_GROUND_M,
         Minimum_Cell_Dimension=KIVA_MIN_CELL_M,
         Maximum_Cell_Growth_Coefficient=KIVA_CELL_GROWTH,
         Simulation_Timestep="Hourly")
    _new(idf, "Foundation:Kiva", Name=KIVA_FOUNDATION,
         Wall_Height_Above_Grade=0.0, Wall_Depth_Below_Slab=0.0)
    exposed_total = 0.0
    for floor in floors:
        exposed = exposed_perimeter_m(floor, floors)
        exposed_total += exposed
        _new(idf, "SurfaceProperty:ExposedFoundationPerimeter", Surface_Name=floor.name,
             Exposed_Perimeter_Calculation_Method="TotalExposedPerimeter",
             Total_Exposed_Perimeter=round(max(exposed, 0.01), 4))
    assumptions.append(Assumption(
        name="ground_model", value="Foundation:Kiva", source=KIVA_SOURCE,
        provenance=Provenance.DERIVED))
    assumptions.append(Assumption(
        name="exposed_floor_perimeter_m", value=round(exposed_total, 2), unit="m",
        source=SourceRef(citation="Floor edges on the outside of the building, with every "
                                  "edge shared with a sunspace, gap or vestibule floor "
                                  "taken off", status=SourceStatus.DERIVED),
        provenance=Provenance.DERIVED))
    return KIVA_FOUNDATION


def _add_contaminant_balance(idf, design: DesignSpec, site: SiteSpec,
                             assumptions: list[Assumption]) -> FreshAirSizing | None:
    occupancy = design.occupancy
    if (occupancy.unoccupied or occupancy.occupants == 0
            or occupancy.metabolic_w_per_person <= 0.0):
        return None
    sizing = size_fresh_air(site.elevation_m, occupancy.metabolic_w_per_person,
                            design.ventilation.co2_limit_ppm)
    _constant_schedule(idf, OUTDOOR_CO2_SCHEDULE, round(sizing.outdoor_ppm, 2),
                       PPM_LIMITS)
    _new(idf, "ZoneAirContaminantBalance", Carbon_Dioxide_Concentration="Yes",
         Outdoor_Carbon_Dioxide_Schedule_Name=OUTDOOR_CO2_SCHEDULE)
    assumptions.extend((
        Assumption(name="co2_generation_m3_s_per_w",
                   value=float(f"{sizing.co2_m3_s_per_w:.4e}"), unit="m3/s per W",
                   source=PERSILY_SOURCE, provenance=Provenance.DERIVED),
        Assumption(name="site_pressure_kpa", value=round(sizing.site_pressure_kpa, 2),
                   unit="kPa", source=PRESSURE_SOURCE, provenance=Provenance.DERIVED),
        Assumption(name="outdoor_co2_ppm", value=sizing.outdoor_ppm, unit="ppm",
                   source=OUTDOOR_CO2_SOURCE, provenance=Provenance.LITERATURE),
        Assumption(name="co2_limit_ppm", value=sizing.limit_ppm, unit="ppm",
                   source=LIMIT_SOURCE, provenance=Provenance.ASSUMED)))
    return sizing


def _add_overhangs(idf, design: DesignSpec, model: GeometryModel) -> int:
    added = 0
    for polygon in model.windows:
        if polygon.facade is None or polygon.outside_boundary != "Outdoors":
            continue
        spec = design.windows.get(polygon.facade)
        if spec is None or spec.overhang_depth_m <= 0.0:
            continue
        _new(idf, "Shading:Overhang", Name=f"{polygon.name}_OVERHANG",
             Window_or_Door_Name=polygon.name,
             Height_above_Window_or_Door=spec.overhang_offset_m,
             Tilt_Angle_from_WindowDoor=90.0,
             Left_extension_from_WindowDoor_Width=0.0,
             Right_extension_from_WindowDoor_Width=0.0,
             Depth=spec.overhang_depth_m)
        added += 1
    return added


def _add_added_moisture(idf, design: DesignSpec, model: GeometryModel,
                        assumptions: list[Assumption]) -> None:
    occupancy = design.occupancy
    if occupancy.unoccupied or occupancy.additional_moisture_g_h <= 0.0:
        return
    watts = (occupancy.additional_moisture_g_h / 1000.0 / 3600.0
             * LATENT_HEAT_OF_VAPORISATION_J_KG)
    moisture = _new(idf, "OtherEquipment", Name="ROOM_ADDED_MOISTURE",
                    Fuel_Type="None", Schedule_Name=MOISTURE_SCHEDULE,
                    Design_Level_Calculation_Method="EquipmentLevel",
                    Design_Level=watts, Fraction_Latent=1.0,
                    Fraction_Radiant=0.0, Fraction_Lost=0.0)
    _set_first(moisture, ZONE_FIELD_ALIASES, model.zone("ROOM").name)
    assumptions.append(Assumption(
        name="additional_moisture_g_h", value=occupancy.additional_moisture_g_h,
        unit="g/h", source=MOISTURE_SOURCE, provenance=Provenance.ASSUMED))


def _add_window_shading(idf, design: DesignSpec, model: GeometryModel,
                        constructions: ConstructionSet,
                        options: RunOptions) -> None:
    if not constructions.shades:
        return
    index = constructions.construction_index()
    for facade_value, shade_material in constructions.shades.items():
        spec = constructions.shutter_specs[facade_value]
        compliance = options.shutter_compliance_override \
            if options.shutter_compliance_override is not None else spec.compliance
        schedule = _shutter_schedule(idf, f"{SHUTTER_SCHEDULE_PREFIX}{facade_value.upper()}",
                                     spec.closed_hours, compliance)
        facade = Facade(facade_value)
        base = constructions.names.glazing.get(facade)
        targets = [polygon.name for polygon in model.windows
                   if polygon.facade is facade
                   and polygon.outside_boundary == "Outdoors"
                   and polygon.construction == base]
        if not targets:
            continue
        shaded = f"{base}_SHADED"
        if shaded not in index:
            continue
        obj = _new(idf, "WindowShadingControl",
                   Name=f"SHADING_CONTROL_{facade_value.upper()}",
                   Shading_Type="InteriorShade",
                   Construction_with_Shading_Name=shaded,
                   Shading_Control_Type="OnIfScheduleAllows",
                   Schedule_Name=schedule,
                   Shading_Control_Is_Scheduled="Yes",
                   Glare_Control_Is_Active="No",
                   Type_of_Slat_Angle_Control_for_Blinds="FixedSlatAngle",
                   Multiple_Surface_Control_Type="Sequential")
        if "Zone_Name" in obj.fieldnames:
            obj.Zone_Name = model.by_name(targets[0]).zone
        for position, name in enumerate(targets, start=1):
            attribute = f"Fenestration_Surface_{position}_Name"
            if attribute in obj.fieldnames:
                setattr(obj, attribute, name)
            else:
                obj.obj.append(name)


def _add_ideal_loads(idf, model: GeometryModel, options: RunOptions) -> list[str]:
    names: list[str] = []
    for zone in model.zones:
        if not zone.conditioned:
            continue
        system = f"{zone.name}_IDEAL_LOADS"
        _new(idf, "ZoneHVAC:IdealLoadsAirSystem", Name=system,
             Zone_Supply_Air_Node_Name=f"{zone.name}_SUPPLY_INLET",
             Maximum_Heating_Supply_Air_Temperature=IDEAL_LOADS_MAX_SUPPLY_C,
             Minimum_Cooling_Supply_Air_Temperature=IDEAL_LOADS_MIN_SUPPLY_C,
             Heating_Limit="NoLimit", Cooling_Limit="NoLimit",
             Dehumidification_Control_Type="None",
             Humidification_Control_Type="None",
             Outdoor_Air_Economizer_Type="NoEconomizer",
             Heat_Recovery_Type="None")
        equipment_list = f"{zone.name}_EQUIPMENT"
        obj = _new(idf, "ZoneHVAC:EquipmentList", Name=equipment_list,
                   Load_Distribution_Scheme="SequentialLoad")
        obj.Zone_Equipment_1_Object_Type = "ZoneHVAC:IdealLoadsAirSystem"
        obj.Zone_Equipment_1_Name = system
        obj.Zone_Equipment_1_Cooling_Sequence = 1
        obj.Zone_Equipment_1_Heating_or_NoLoad_Sequence = 1
        _new(idf, "NodeList", Name=f"{zone.name}_SUPPLY_INLETS",
             Node_1_Name=f"{zone.name}_SUPPLY_INLET")
        _new(idf, "ZoneHVAC:EquipmentConnections", Zone_Name=zone.name,
             Zone_Conditioning_Equipment_List_Name=equipment_list,
             Zone_Air_Inlet_Node_or_NodeList_Name=f"{zone.name}_SUPPLY_INLETS",
             Zone_Air_Node_Name=f"{zone.name}_AIR_NODE",
             Zone_Return_Air_Node_or_NodeList_Name=f"{zone.name}_RETURN_OUTLET")
        _new(idf, "ThermostatSetpoint:DualSetpoint", Name=f"{zone.name}_SETPOINTS",
             Heating_Setpoint_Temperature_Schedule_Name=HEATING_SETPOINT_SCHEDULE,
             Cooling_Setpoint_Temperature_Schedule_Name=COOLING_SETPOINT_SCHEDULE)
        thermostat = _new(idf, "ZoneControl:Thermostat",
                          Name=f"{zone.name}_THERMOSTAT",
                          Control_Type_Schedule_Name=CONTROL_TYPE_SCHEDULE,
                          Control_1_Object_Type="ThermostatSetpoint:DualSetpoint",
                          Control_1_Name=f"{zone.name}_SETPOINTS")
        _set_first(thermostat, ZONE_FIELD_ALIASES, zone.name)
        names.append(system)
    return names


def _add_outputs(idf, model: GeometryModel, options: RunOptions,
                 conditioned: bool) -> None:
    for variable in SITE_VARIABLES:
        _new(idf, "Output:Variable", Key_Value="*", Variable_Name=variable,
             Reporting_Frequency="Hourly")
    for variable in ZONE_VARIABLES:
        _new(idf, "Output:Variable", Key_Value="*", Variable_Name=variable,
             Reporting_Frequency="Hourly")
    if options.surface_output:
        for variable in SURFACE_VARIABLES + WINDOW_VARIABLES:
            _new(idf, "Output:Variable", Key_Value="*", Variable_Name=variable,
                 Reporting_Frequency="Hourly")
    if conditioned:
        for variable in IDEAL_LOADS_VARIABLES:
            _new(idf, "Output:Variable", Key_Value="*", Variable_Name=variable,
                 Reporting_Frequency="Hourly")
    _new(idf, "Output:SQLite", Option_Type="Simple")
    _new(idf, "Output:VariableDictionary", Key_Field="IDF", Sort_Option="Unsorted")
    _new(idf, "Output:Diagnostics",
         Key_1="DisplayExtraWarnings", Key_2="DisplayUnusedSchedules")


def build_idf(design: DesignSpec, site: SiteSpec, model: GeometryModel,
              constructions: ConstructionSet, options: RunOptions,
              conditioned: bool = False,
              install: EnergyPlusInstall | None = None,
              force_finite_difference: bool = False) -> IdfBundle:
    install = install or find_energyplus()
    IDF = _idf_class(install)
    idf = IDF(io.StringIO(""))
    assumptions: list[Assumption] = list(constructions.assumptions)
    warnings: list[EngineWarning] = list(constructions.warnings) + list(model.warnings)

    finite_difference = (constructions.requires_conduction_finite_difference
                         or force_finite_difference)
    timestep = options.timestep_per_hour
    if finite_difference:
        timestep = max(timestep, CONDFD_TIMESTEP_PER_HOUR)
        if timestep != options.timestep_per_hour:
            warnings.append(EngineWarning(
                code="pcm_timestep_raised", severity=Severity.INFO, origin="engine",
                message=(f"The conduction finite difference solver is in use, so the "
                         f"timestep was raised from "
                         f"{options.timestep_per_hour} to {timestep} steps per hour. "
                         "This run will take noticeably longer.")))

    _new(idf, "Version", Version_Identifier=install.idf_version)
    _new(idf, "SimulationControl",
         Do_Zone_Sizing_Calculation="No", Do_System_Sizing_Calculation="No",
         Do_Plant_Sizing_Calculation="No",
         Run_Simulation_for_Sizing_Periods="No",
         Run_Simulation_for_Weather_File_Run_Periods="Yes")
    solar_distribution = options.solar_distribution
    if (design.arrangement in (Arrangement.SUNSPACE, Arrangement.TROMBE_WALL)
            and solar_distribution == "FullExterior"):
        solar_distribution = "FullInteriorAndExterior"
        warnings.append(EngineWarning(
            code="solar_distribution_raised", severity=Severity.INFO, origin="engine",
            message=(f"The {design.arrangement.value} arrangement needs interior solar "
                     "distribution, so FullInteriorAndExterior was used instead of "
                     "FullExterior. Under FullExterior EnergyPlus lands all transmitted "
                     "solar on the floor, which in a thin Trombe gap or a sunspace "
                     "drives the floor to physically impossible temperatures and starves "
                     "the mass wall of the gain the design depends on.")))
    _new(idf, "Building", Name=design.name[:100],
         North_Axis=0.0, Terrain=site.terrain,
         Loads_Convergence_Tolerance_Value=0.04,
         Temperature_Convergence_Tolerance_Value=0.4,
         Solar_Distribution=solar_distribution,
         Maximum_Number_of_Warmup_Days=25,
         Minimum_Number_of_Warmup_Days=DEFAULT_WARMUP_DAYS)
    _new(idf, "Timestep", Number_of_Timesteps_per_Hour=timestep)
    _new(idf, "GlobalGeometryRules",
         Starting_Vertex_Position="UpperLeftCorner",
         Vertex_Entry_Direction="Counterclockwise",
         Coordinate_System="World")
    _new(idf, "HeatBalanceAlgorithm",
         Algorithm="ConductionFiniteDifference" if finite_difference
         else "ConductionTransferFunction")
    _new(idf, "Site:Location", Name=site.name[:100],
         Latitude=site.latitude_deg, Longitude=site.longitude_deg,
         Time_Zone=site.time_zone_h, Elevation=site.elevation_m)

    kiva = options.ground_model == "kiva"
    if not kiva and site.ground is not None:
        ground = _new(idf, "Site:GroundTemperature:BuildingSurface")
        months = ("January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December")
        for month, value in zip(months, site.ground.monthly_c):
            setattr(ground, f"{month}_Ground_Temperature", value)
        assumptions.append(Assumption(
            name="monthly_ground_temperature_c", value=list(site.ground.monthly_c),
            unit="C", source=site.ground.source, provenance=Provenance.DERIVED))
    elif not kiva:
        warnings.append(EngineWarning(
            code="ground_temperature_default", severity=Severity.SEVERE,
            origin="engine",
            message=("No monthly ground temperatures were supplied, so EnergyPlus will "
                     "fall back on its temperate climate defaults. Ladakh ground "
                     "freezes seasonally and floor losses will be materially wrong. "
                     "Layer 0 must supply this before any reported result.")))

    period = options.period
    _new(idf, "RunPeriod", Name=period.label,
         Begin_Month=period.start.month, Begin_Day_of_Month=period.start.day,
         End_Month=period.end.month, End_Day_of_Month=period.end.day,
         Day_of_Week_for_Start_Day="Monday",
         Use_Weather_File_Holidays_and_Special_Days="Yes",
         Use_Weather_File_Daylight_Saving_Period="No",
         Apply_Weekend_Holiday_Rule="No",
         Use_Weather_File_Rain_Indicators="Yes",
         Use_Weather_File_Snow_Indicators="Yes")

    _schedule_type_limits(idf)
    _constant_schedule(idf, ALWAYS_ON, 1.0, FRACTION_LIMITS)
    _constant_schedule(idf, ALWAYS_OFF, 0.0, FRACTION_LIMITS)
    _constant_schedule(idf, INFILTRATION_SCHEDULE, 1.0, FRACTION_LIMITS)
    _constant_schedule(idf, FRESH_AIR_SCHEDULE, 1.0, FRACTION_LIMITS)
    _constant_schedule(idf, ACTIVITY_SCHEDULE,
                       design.occupancy.metabolic_w_per_person, ACTIVITY_LIMITS)
    occupancy = design.occupancy
    if occupancy.unoccupied:
        _constant_schedule(idf, OCCUPANCY_SCHEDULE, 0.0, FRACTION_LIMITS)
        _constant_schedule(idf, MOISTURE_SCHEDULE, 0.0, FRACTION_LIMITS)
        _constant_schedule(idf, EQUIPMENT_SCHEDULE, 0.0, FRACTION_LIMITS)
        _constant_schedule(idf, LIGHTING_SCHEDULE, 0.0, FRACTION_LIMITS)
    else:
        _all_days_schedule(idf, OCCUPANCY_SCHEDULE, occupancy.occupied_hours,
                           1.0, occupancy.daytime_occupancy_fraction)
        _all_days_schedule(idf, EQUIPMENT_SCHEDULE, occupancy.occupied_hours,
                           1.0, occupancy.daytime_occupancy_fraction)
        _all_days_schedule(idf, LIGHTING_SCHEDULE, occupancy.occupied_hours, 1.0, 0.05)
        _all_days_schedule(idf, MOISTURE_SCHEDULE, occupancy.occupied_hours,
                           1.0, occupancy.daytime_occupancy_fraction)
    if design.ventilation.night_ventilation:
        _all_days_schedule(idf, NIGHT_VENT_SCHEDULE,
                           design.ventilation.night_ventilation_hours)
    if design.arrangement is not Arrangement.SINGLE_ROOM:
        _all_days_schedule(idf, COUPLING_SCHEDULE,
                           design.arrangement_params.coupling_hours)
    if conditioned:
        heating_hours = () if occupancy.unoccupied else options.heating_hours
        _all_days_schedule(idf, HEATING_SETPOINT_SCHEDULE, heating_hours,
                           options.heating_setpoint_c, HEATING_OFF_C, TEMPERATURE_LIMITS)
        _constant_schedule(idf, COOLING_SETPOINT_SCHEDULE,
                           options.cooling_setpoint_c, TEMPERATURE_LIMITS)
        _constant_schedule(idf, CONTROL_TYPE_SCHEDULE, 4, CONTROL_LIMITS)
        assumptions.append(Assumption(
            name="heating_setpoint_c", value=options.heating_setpoint_c, unit="C",
            source=SETPOINT_SOURCE, provenance=Provenance.DERIVED))
        assumptions.append(Assumption(
            name="heating_window",
            value=(f"{options.heating_from_hour:02d}:00 to "
                   f"{options.heating_until_hour:02d}:00"
                   if heating_hours else "no heating, the shelter is unoccupied"),
            source=SETPOINT_SOURCE, provenance=Provenance.DERIVED))

    _add_materials(idf, constructions)
    for zone in model.zones:
        _add_zone(idf, zone)
    foundation = _add_foundation(idf, model, assumptions) if kiva else None
    for polygon in model.opaque:
        _add_surface(idf, polygon, foundation)
    for polygon in model.windows:
        _add_window(idf, polygon)
    if options.include_horizon_shading:
        _add_shading(idf, model)
    _add_internal_mass(idf, design, model, constructions, assumptions)
    fresh_air = _add_contaminant_balance(idf, design, site, assumptions)
    _add_internal_gains(idf, design, model, assumptions, fresh_air)
    _add_added_moisture(idf, design, model, assumptions)
    ventilation_l_s, infiltration_l_s = _add_airflow(idf, design, model, assumptions,
                                                     fresh_air)
    _add_window_shading(idf, design, model, constructions, options)
    overhangs = _add_overhangs(idf, design, model)
    if overhangs:
        assumptions.append(Assumption(
            name="window_overhangs", value=overhangs, unit="count",
            source=OVERHANG_SOURCE, provenance=Provenance.DERIVED))

    ideal_loads: list[str] = []
    if conditioned:
        ideal_loads = _add_ideal_loads(idf, model, options)
    _add_outputs(idf, model, options, conditioned)

    return IdfBundle(
        text=serialise(idf),
        zone_names=tuple(zone.name for zone in model.zones),
        conditioned_zone_names=tuple(zone.name for zone in model.zones
                                     if zone.conditioned),
        ideal_loads_names=tuple(ideal_loads),
        surface_names=tuple(polygon.name for polygon in model.polygons),
        timestep_per_hour=timestep,
        assumptions=assumptions,
        warnings=warnings,
        fresh_air=fresh_air,
        ventilation_design_l_s=ventilation_l_s,
        infiltration_design_l_s=infiltration_l_s)
