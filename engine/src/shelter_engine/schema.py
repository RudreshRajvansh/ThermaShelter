from __future__ import annotations

import math
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MIN_WINDOW_DIMENSION_M = 0.30
MIN_JAMB_MARGIN_M = 0.15
MIN_HEAD_MARGIN_M = 0.15
MIN_ZONE_DIMENSION_M = 0.02


class Arrangement(str, Enum):
    SINGLE_ROOM = "single_room"
    SUNSPACE = "sunspace"
    TROMBE_WALL = "trombe_wall"
    VESTIBULE = "vestibule"


class ShelterUse(str, Enum):
    BARRACK = "barrack"
    MEDICAL_POST = "medical_post"
    COMMAND_POST = "command_post"
    KITCHEN_MESS = "kitchen_mess"
    WORKSHOP = "workshop"
    STORE = "store"
    RELIEF_SHELTER = "relief_shelter"


class Facade(str, Enum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"

    @property
    def nominal_azimuth_deg(self) -> float:
        return {"north": 0.0, "east": 90.0, "south": 180.0, "west": 270.0}[self.value]


class SurfaceKind(str, Enum):
    WALL = "wall"
    ROOF = "roof"
    FLOOR = "floor"
    CEILING = "ceiling"
    WINDOW = "window"
    PARTITION = "partition"
    SHADING = "shading"


class ClimateObjective(str, Enum):
    HEATING_DOMINATED = "heating_dominated"
    COOLING_DOMINATED = "cooling_dominated"
    COMPOSITE = "composite"


class SourceStatus(str, Enum):
    VERIFIED = "verified"
    PENDING = "pending"
    DERIVED = "derived"
    ASSUMED = "assumed"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    SEVERE = "severe"
    FATAL = "fatal"


class Provenance(str, Enum):
    SIMULATED = "simulated"
    SURROGATE = "surrogate"
    CFD = "cfd"
    MEASURED = "measured"
    DERIVED = "derived"
    LITERATURE = "literature"
    ASSUMED = "assumed"


Fraction = Annotated[float, Field(ge=0.0, le=1.0)]


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class Mutable(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SourceRef(Frozen):
    citation: str = Field(min_length=1)
    status: SourceStatus = SourceStatus.PENDING
    url: str | None = None
    note: str | None = None


class Assumption(Frozen):
    name: str
    value: Any
    unit: str | None = None
    source: SourceRef
    provenance: Provenance = Provenance.DERIVED


class EngineWarning(Frozen):
    code: str
    severity: Severity
    message: str
    origin: Literal["engine", "energyplus", "weather", "materials", "geometry",
                    "optimiser"] = "engine"
    count: int = Field(default=1, ge=1)


class MaterialAppearance(Frozen):
    colour: str = Field(pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
    finish: Literal["rough", "smooth", "matte", "glossy", "textured",
                    "transparent"] = "rough"


class UncertainRange(Frozen):
    low: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> "UncertainRange":
        if self.high < self.low:
            raise ValueError("high must be >= low")
        return self

    def brackets(self, value: float) -> bool:
        return self.low <= value <= self.high

    @property
    def is_degenerate(self) -> bool:
        return self.high == self.low


class Material(Frozen):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    conductivity_w_mk: float = Field(gt=0.0, le=500.0)
    density_kg_m3: float = Field(gt=0.0, le=25000.0)
    specific_heat_j_kgk: float = Field(gt=0.0, le=10000.0)
    thermal_absorptance: float = Field(default=0.90, gt=0.0, le=1.0)
    solar_absorptance: Fraction = 0.70
    visible_absorptance: Fraction = 0.70
    roughness: Literal["VeryRough", "Rough", "MediumRough", "MediumSmooth",
                       "Smooth", "VerySmooth"] = "MediumRough"
    cost_inr_m3: float = Field(ge=0.0)
    locally_available: bool = True
    transport_multiplier: float = Field(default=1.0, ge=1.0, le=10.0)
    mason_skill: Literal["unskilled", "semiskilled", "skilled",
                         "specialist"] = "semiskilled"
    appearance: MaterialAppearance
    source: SourceRef
    conductivity_range: UncertainRange | None = None
    density_range: UncertainRange | None = None

    @model_validator(mode="after")
    def _ranges_bracket_nominal(self) -> "Material":
        if self.conductivity_range and not self.conductivity_range.brackets(
                self.conductivity_w_mk):
            raise ValueError(f"conductivity_range must bracket nominal for {self.id}")
        if self.density_range and not self.density_range.brackets(self.density_kg_m3):
            raise ValueError(f"density_range must bracket nominal for {self.id}")
        return self

    @property
    def delivered_cost_inr_m3(self) -> float:
        multiplier = 1.0 if self.locally_available else self.transport_multiplier
        return self.cost_inr_m3 * multiplier

    @property
    def volumetric_heat_capacity_j_m3k(self) -> float:
        return self.density_kg_m3 * self.specific_heat_j_kgk


class PhaseChangeMaterial(Frozen):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    melt_temperature_c: float = Field(ge=-50.0, le=150.0)
    latent_heat_j_kg: float = Field(gt=0.0, le=1_000_000.0)
    conductivity_w_mk: float = Field(gt=0.0, le=50.0)
    density_kg_m3: float = Field(gt=0.0, le=25000.0)
    specific_heat_j_kgk: float = Field(gt=0.0, le=10000.0)
    melt_band_k: float = Field(default=2.0, gt=0.0, le=15.0)
    cost_inr_m3: float = Field(ge=0.0)
    cycle_capacity_retention: Fraction = 1.0
    appearance: MaterialAppearance
    source: SourceRef

    @property
    def effective_latent_heat_j_kg(self) -> float:
        return self.latent_heat_j_kg * self.cycle_capacity_retention


class GlazingSystem(Frozen):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    u_value_w_m2k: float = Field(gt=0.05, le=10.0)
    shgc: float = Field(gt=0.0, le=1.0)
    visible_transmittance: float = Field(gt=0.0, le=1.0)
    layers: int = Field(default=1, ge=1, le=4)
    frame_fraction: Fraction = 0.15
    cost_inr_m2: float = Field(ge=0.0)
    locally_available: bool = True
    appearance: MaterialAppearance
    source: SourceRef


class ShutterSpec(Frozen):
    present: bool = False
    thermal_resistance_m2k_w: float = Field(default=0.35, gt=0.0, le=5.0)
    closed_from_hour: int = Field(default=17, ge=0, le=23)
    closed_until_hour: int = Field(default=8, ge=0, le=23)
    compliance: Fraction = 1.0
    cost_inr_m2: float = Field(default=0.0, ge=0.0)
    source: SourceRef | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> "ShutterSpec":
        if self.present and self.closed_from_hour == self.closed_until_hour:
            raise ValueError("shutter closed period is empty")
        return self

    @property
    def closed_hours(self) -> tuple[int, ...]:
        if not self.present:
            return ()
        if self.closed_from_hour > self.closed_until_hour:
            return (tuple(range(self.closed_from_hour, 24))
                    + tuple(range(0, self.closed_until_hour)))
        return tuple(range(self.closed_from_hour, self.closed_until_hour))


class WindowSpec(Frozen):
    wwr: float = Field(ge=0.0, le=0.90)
    glazing_id: str = Field(min_length=1)
    sill_height_m: float = Field(default=0.90, ge=0.0, le=3.0)
    shutter: ShutterSpec = ShutterSpec()
    overhang_depth_m: float = Field(default=0.0, ge=0.0, le=3.0)
    overhang_offset_m: float = Field(default=0.10, ge=0.0, le=1.0)


class LayerSpec(Frozen):
    material_id: str = Field(min_length=1)
    thickness_m: float = Field(gt=0.0001, le=2.0)


class ConstructionSpec(Frozen):
    name: str = Field(min_length=1)
    layers: tuple[LayerSpec, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _unique_enough(self) -> "ConstructionSpec":
        if self.thickness_m > 3.0:
            raise ValueError(f"construction {self.name} exceeds 3 m total thickness")
        return self

    @property
    def thickness_m(self) -> float:
        return sum(layer.thickness_m for layer in self.layers)


class PCMSpec(Frozen):
    present: bool = False
    pcm_id: str | None = None
    thickness_m: float = Field(default=0.02, gt=0.0, le=0.10)
    applied_to: Literal["mass_wall_inner", "floor_inner",
                        "ceiling_inner"] = "mass_wall_inner"

    @model_validator(mode="after")
    def _needs_id(self) -> "PCMSpec":
        if self.present and not self.pcm_id:
            raise ValueError("pcm_id is required when pcm.present is true")
        return self


class OccupancySpec(Frozen):
    occupants: int = Field(default=9, ge=0, le=200)
    metabolic_w_per_person: float = Field(default=110.0, ge=0.0, le=500.0)
    equipment_w: float = Field(default=150.0, ge=0.0, le=20000.0)
    lighting_w: float = Field(default=100.0, ge=0.0, le=20000.0)
    cooking_w: float = Field(default=0.0, ge=0.0, le=20000.0)
    occupied_from_hour: int = Field(default=17, ge=0, le=23)
    occupied_until_hour: int = Field(default=8, ge=0, le=23)
    daytime_occupancy_fraction: Fraction = 0.20
    additional_moisture_g_h: float = Field(default=0.0, ge=0.0, le=20000.0)
    unoccupied: bool = False
    source: SourceRef | None = None

    @property
    def peak_sensible_w(self) -> float:
        if self.unoccupied:
            return 0.0
        return (self.occupants * self.metabolic_w_per_person
                + self.equipment_w + self.lighting_w + self.cooking_w)

    @property
    def occupied_hours(self) -> tuple[int, ...]:
        if self.unoccupied:
            return ()
        if self.occupied_from_hour == self.occupied_until_hour:
            return tuple(range(24))
        if self.occupied_from_hour > self.occupied_until_hour:
            return (tuple(range(self.occupied_from_hour, 24))
                    + tuple(range(0, self.occupied_until_hour)))
        return tuple(range(self.occupied_from_hour, self.occupied_until_hour))


class VentilationSpec(Frozen):
    infiltration_ach: float = Field(default=0.60, ge=0.0, le=15.0)
    minimum_fresh_air_ach: float = Field(default=0.35, ge=0.0, le=15.0)
    night_ventilation: bool = False
    night_ventilation_ach: float = Field(default=0.0, ge=0.0, le=20.0)
    night_ventilation_from_hour: int = Field(default=21, ge=0, le=23)
    night_ventilation_until_hour: int = Field(default=6, ge=0, le=23)
    infiltration_range: UncertainRange | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "VentilationSpec":
        if self.night_ventilation and self.night_ventilation_ach <= 0.0:
            raise ValueError("night_ventilation_ach must be > 0 when enabled")
        if self.night_ventilation and (
                self.night_ventilation_from_hour == self.night_ventilation_until_hour):
            raise ValueError("night ventilation period is empty")
        if self.infiltration_range and not self.infiltration_range.brackets(
                self.infiltration_ach):
            raise ValueError("infiltration_range must bracket infiltration_ach")
        return self

    @property
    def effective_air_change_ach(self) -> float:
        return max(self.infiltration_ach, self.minimum_fresh_air_ach)

    @property
    def night_ventilation_hours(self) -> tuple[int, ...]:
        if not self.night_ventilation:
            return ()
        start, end = self.night_ventilation_from_hour, self.night_ventilation_until_hour
        if start > end:
            return tuple(range(start, 24)) + tuple(range(0, end))
        return tuple(range(start, end))


class PlanSpec(Frozen):
    use: ShelterUse = ShelterUse.BARRACK
    floor_area_m2: float = Field(default=40.0, ge=4.0, le=2000.0)
    aspect_ratio: float = Field(default=1.60, ge=0.25, le=6.0)
    ceiling_height_m: float = Field(default=2.70, ge=1.8, le=6.0)
    azimuth_deg: float = 180.0

    @field_validator("azimuth_deg")
    @classmethod
    def _wrap(cls, value: float) -> float:
        return float(value) % 360.0

    @property
    def width_m(self) -> float:
        return math.sqrt(self.floor_area_m2 * self.aspect_ratio)

    @property
    def depth_m(self) -> float:
        return math.sqrt(self.floor_area_m2 / self.aspect_ratio)

    @property
    def volume_m3(self) -> float:
        return self.floor_area_m2 * self.ceiling_height_m

    @property
    def envelope_area_m2(self) -> float:
        return (2.0 * self.floor_area_m2
                + 2.0 * self.ceiling_height_m * (self.width_m + self.depth_m))

    @property
    def surface_to_volume(self) -> float:
        return self.envelope_area_m2 / self.volume_m3

    def facade_width_m(self, facade: Facade) -> float:
        return (self.width_m if facade in (Facade.NORTH, Facade.SOUTH)
                else self.depth_m)


class ArrangementParams(Frozen):
    sunspace_depth_m: float = Field(default=1.80, ge=0.6, le=6.0)
    sunspace_glazed_roof: bool = True
    sunspace_glazing_id: str | None = None
    trombe_gap_m: float = Field(default=0.08, ge=0.02, le=0.50)
    trombe_glazing_id: str | None = None
    trombe_width_fraction: float = Field(default=0.70, gt=0.0, le=1.0)
    vestibule_depth_m: float = Field(default=1.20, ge=0.6, le=3.0)
    vestibule_facade: Facade = Facade.NORTH
    coupling_ach: float = Field(default=2.0, ge=0.0, le=30.0)
    coupling_from_hour: int = Field(default=10, ge=0, le=23)
    coupling_until_hour: int = Field(default=17, ge=0, le=23)
    coupling_source: SourceRef | None = None

    @model_validator(mode="after")
    def _coupling_window(self) -> "ArrangementParams":
        if self.coupling_ach > 0.0 and self.coupling_from_hour == self.coupling_until_hour:
            raise ValueError("zone coupling period is empty")
        return self

    @property
    def coupling_hours(self) -> tuple[int, ...]:
        if self.coupling_ach <= 0.0:
            return ()
        start, end = self.coupling_from_hour, self.coupling_until_hour
        if start > end:
            return tuple(range(start, 24)) + tuple(range(0, end))
        return tuple(range(start, end))


class DesignSpec(Frozen):
    name: str = Field(default="candidate", min_length=1, max_length=120)
    description: str | None = None
    arrangement: Arrangement = Arrangement.SINGLE_ROOM
    plan: PlanSpec = PlanSpec()
    wall: ConstructionSpec
    roof: ConstructionSpec
    floor: ConstructionSpec
    partition: ConstructionSpec | None = None
    windows: dict[Facade, WindowSpec] = Field(default_factory=dict)
    ventilation: VentilationSpec = VentilationSpec()
    occupancy: OccupancySpec = OccupancySpec()
    pcm: PCMSpec = PCMSpec()
    arrangement_params: ArrangementParams = ArrangementParams()
    is_reference: bool = False
    citation: SourceRef | None = None
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _coherent(self) -> "DesignSpec":
        needs_partition = self.arrangement in (
            Arrangement.SUNSPACE, Arrangement.TROMBE_WALL, Arrangement.VESTIBULE)
        if needs_partition and self.partition is None:
            raise ValueError(
                f"arrangement {self.arrangement.value} requires a partition construction")
        if self.arrangement == Arrangement.TROMBE_WALL:
            south = self.windows.get(Facade.SOUTH)
            if south is not None and south.wwr > 0.0:
                raise ValueError(
                    "trombe_wall glazes the south facade itself; drop the south "
                    "window or set its wwr to 0")
        if self.arrangement == Arrangement.SUNSPACE:
            south = self.windows.get(Facade.SOUTH)
            if south is None or south.wwr <= 0.0:
                raise ValueError(
                    "sunspace arrangement requires a south window linking room to sunspace")
        if self.arrangement == Arrangement.VESTIBULE:
            facade = self.arrangement_params.vestibule_facade
            window = self.windows.get(facade)
            if window is not None and window.wwr > 0.0:
                raise ValueError(
                    f"vestibule occupies the {facade.value} facade; that facade "
                    "cannot also carry a window")
        for facade, window in self.windows.items():
            if window.wwr <= 0.0:
                continue
            height = self.plan.ceiling_height_m
            width = self.plan.facade_width_m(facade)
            usable_height = height - window.sill_height_m - MIN_HEAD_MARGIN_M
            usable_width = width - 2.0 * MIN_JAMB_MARGIN_M
            if usable_height < MIN_WINDOW_DIMENSION_M:
                raise ValueError(
                    f"{facade.value} sill {window.sill_height_m:.2f} m leaves no "
                    f"glazing room in a {height:.2f} m wall")
            if usable_width < MIN_WINDOW_DIMENSION_M:
                raise ValueError(
                    f"{facade.value} wall is {width:.2f} m wide, too narrow for a window")
            required = window.wwr * width * height
            if required > usable_width * usable_height:
                limit = usable_width * usable_height / (width * height)
                raise ValueError(
                    f"{facade.value} wwr {window.wwr:.3f} does not fit above a "
                    f"{window.sill_height_m:.2f} m sill; maximum is {limit:.3f}")
        return self

    @property
    def total_wall_thickness_m(self) -> float:
        return self.wall.thickness_m

    @property
    def glazed_facades(self) -> tuple[Facade, ...]:
        return tuple(f for f, w in self.windows.items() if w.wwr > 0.0)

    def variant(self, **updates: Any) -> "DesignSpec":
        return self.model_copy(update=updates, deep=True)


class HorizonProfile(Frozen):
    azimuths_deg: tuple[float, ...] = Field(min_length=2, max_length=72)
    blocked_altitude_deg: tuple[float, ...] = Field(min_length=2, max_length=72)
    source: SourceRef

    @model_validator(mode="after")
    def _valid(self) -> "HorizonProfile":
        if len(self.azimuths_deg) != len(self.blocked_altitude_deg):
            raise ValueError("azimuths_deg and blocked_altitude_deg lengths differ")
        if any(not 0.0 <= a < 360.0 for a in self.azimuths_deg):
            raise ValueError("azimuths_deg must lie in [0, 360)")
        if any(not 0.0 <= h < 90.0 for h in self.blocked_altitude_deg):
            raise ValueError("blocked_altitude_deg must lie in [0, 90)")
        if len(set(self.azimuths_deg)) != len(self.azimuths_deg):
            raise ValueError("azimuths_deg must be unique")
        return self

    @property
    def max_blocked_deg(self) -> float:
        return max(self.blocked_altitude_deg)


class GroundTemperatures(Frozen):
    monthly_c: tuple[float, ...] = Field(min_length=12, max_length=12)
    depth_m: float = Field(default=0.5, gt=0.0, le=10.0)
    source: SourceRef

    @field_validator("monthly_c")
    @classmethod
    def _plausible(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not -60.0 <= v <= 60.0 for v in value):
            raise ValueError("monthly ground temperatures must lie in [-60, 60] C")
        return value


class SiteSpec(Frozen):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    latitude_deg: float = Field(ge=-90.0, le=90.0)
    longitude_deg: float = Field(ge=-180.0, le=180.0)
    elevation_m: float = Field(ge=-500.0, le=9000.0)
    time_zone_h: float = Field(ge=-12.0, le=14.0)
    epw_path: str = Field(min_length=1)
    climate_objective: ClimateObjective = ClimateObjective.HEATING_DOMINATED
    terrain: Literal["Country", "Suburbs", "City", "Ocean", "Urban"] = "Country"
    horizon: HorizonProfile | None = None
    ground: GroundTemperatures | None = None
    source: SourceRef | None = None


class RunPeriod(Frozen):
    start: date
    end: date
    label: str = "run"

    @model_validator(mode="after")
    def _ordered(self) -> "RunPeriod":
        if self.end < self.start:
            raise ValueError("run period end must be on or after start")
        if self.start.year != self.end.year:
            raise ValueError("run period must lie inside a single calendar year")
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, moment: datetime | date) -> bool:
        day = moment.date() if isinstance(moment, datetime) else moment
        return self.start <= day <= self.end


class ComfortModel(Frozen):
    id: str
    name: str
    intercept_c: float
    slope: float
    band_half_width_k: float = Field(gt=0.0, le=10.0)
    running_mean_alpha: float = Field(default=0.80, gt=0.0, lt=1.0)
    outdoor_applicability_c: UncertainRange
    absolute_lower_bound_c: float | None = None
    absolute_upper_bound_c: float | None = None
    source: SourceRef

    def neutral_temperature(self, outdoor_running_mean_c: float) -> float:
        clamped = min(max(outdoor_running_mean_c, self.outdoor_applicability_c.low),
                      self.outdoor_applicability_c.high)
        return self.intercept_c + self.slope * clamped

    def bounds(self, outdoor_running_mean_c: float) -> tuple[float, float]:
        neutral = self.neutral_temperature(outdoor_running_mean_c)
        lower = neutral - self.band_half_width_k
        upper = neutral + self.band_half_width_k
        if self.absolute_lower_bound_c is not None:
            lower = max(lower, self.absolute_lower_bound_c)
        if self.absolute_upper_bound_c is not None:
            upper = min(upper, self.absolute_upper_bound_c)
        if upper <= lower:
            raise ValueError(f"comfort model {self.id} produced an empty band")
        return lower, upper


class FixedComfortTarget(Frozen):
    id: str
    name: str
    lower_bound_c: float
    upper_bound_c: float | None = None
    from_hour: int = Field(default=18, ge=0, le=23)
    until_hour: int = Field(default=6, ge=0, le=23)
    source: SourceRef

    @property
    def hours(self) -> tuple[int, ...]:
        if self.from_hour == self.until_hour:
            return tuple(range(24))
        if self.from_hour > self.until_hour:
            return tuple(range(self.from_hour, 24)) + tuple(range(0, self.until_hour))
        return tuple(range(self.from_hour, self.until_hour))


class FuelModel(Frozen):
    fuel: Literal["diesel", "kerosene", "lpg", "electricity", "wood"] = "diesel"
    unit: str = "litre"
    lower_heating_value_kwh_per_unit: float = Field(gt=0.0)
    appliance_efficiency: float = Field(default=0.75, gt=0.0, le=1.0)
    price_inr_per_unit: float = Field(ge=0.0)
    source: SourceRef

    def units_for(self, delivered_kwh: float) -> float:
        if delivered_kwh <= 0.0:
            return 0.0
        return delivered_kwh / (self.lower_heating_value_kwh_per_unit
                                * self.appliance_efficiency)

    def cost_inr_for(self, delivered_kwh: float) -> float:
        return self.units_for(delivered_kwh) * self.price_inr_per_unit


class RunOptions(Frozen):
    period: RunPeriod
    timestep_per_hour: int = Field(default=6, ge=1, le=60)
    conditioned_run: bool = True
    heating_setpoint_c: float = Field(default=20.0, ge=0.0, le=35.0)
    cooling_setpoint_c: float = Field(default=29.0, ge=5.0, le=45.0)
    surface_output: bool = True
    surface_output_period: RunPeriod | None = None
    include_horizon_shading: bool = True
    shutter_compliance_override: float | None = Field(default=None, ge=0.0, le=1.0)
    solar_distribution: Literal[
        "FullExterior", "FullInteriorAndExterior",
        "FullExteriorWithReflections",
        "FullInteriorAndExteriorWithReflections"] = "FullExterior"

    @field_validator("timestep_per_hour")
    @classmethod
    def _divides_hour(cls, value: int) -> int:
        if 60 % value != 0:
            raise ValueError("timestep_per_hour must divide 60")
        return value

    @model_validator(mode="after")
    def _checks(self) -> "RunOptions":
        if self.cooling_setpoint_c <= self.heating_setpoint_c:
            raise ValueError("cooling_setpoint_c must exceed heating_setpoint_c")
        window = self.surface_output_period
        if window is not None and not (
                window.start >= self.period.start and window.end <= self.period.end):
            raise ValueError("surface_output_period must lie inside period")
        return self

    @property
    def effective_surface_period(self) -> RunPeriod:
        return self.surface_output_period or self.period


class SunPosition(Frozen):
    hour: float = Field(ge=0.0, le=24.0)
    altitude_deg: float = Field(ge=-90.0, le=90.0)
    azimuth_deg: float = Field(ge=0.0, lt=360.0)
    horizon_altitude_deg: float = Field(default=0.0, ge=0.0, lt=90.0)
    above_horizon: bool = False
    visible: bool = False


class SolarNoon(Frozen):
    hour: float
    altitude_deg: float
    azimuth_deg: float


class SunPath(Frozen):
    site_id: str
    date: str
    latitude_deg: float
    longitude_deg: float
    declination_deg: float
    equation_of_time_minutes: float
    positions: tuple[SunPosition, ...]
    astronomical_sunrise_hour: float | None = None
    astronomical_sunset_hour: float | None = None
    visible_sunrise_hour: float | None = None
    visible_sunset_hour: float | None = None
    noon: SolarNoon
    has_horizon: bool = False
    daylight_hours: float = 0.0
    visible_daylight_hours: float = 0.0
    daylight_lost_to_terrain_h: float = 0.0
    source: SourceRef
    provenance: Provenance = Provenance.DERIVED


class TerrainMesh(Frozen):
    site_id: str
    positions: tuple[float, ...]
    indices: tuple[int, ...]
    inner_radius_m: float
    outer_radius_m: float
    ridge_max_m: float
    derived_from_horizon: bool
    source: SourceRef
    note: str = ""
    elevation_min_m: float | None = None
    elevation_max_m: float | None = None
    site_elevation_m: float | None = None
    sample_spacing_m: float | None = None
    vertex_count: int = 0


class ClimateMonth(Frozen):
    month: int = Field(ge=1, le=12)
    mean_dry_bulb_c: float
    min_dry_bulb_c: float
    max_dry_bulb_c: float
    mean_daily_swing_k: float
    global_horizontal_kwh_m2: float
    direct_normal_kwh_m2: float
    mean_wind_m_s: float
    mean_relative_humidity_pct: float
    heating_degree_days: float
    cooling_degree_days: float
    ground_temperature_c: float | None = None


class ClimateWindow(Frozen):
    label: str
    timestamps: tuple[str, ...]
    dry_bulb_c: tuple[float, ...]
    relative_humidity_pct: tuple[float, ...]
    global_horizontal_w_m2: tuple[float, ...]
    direct_normal_w_m2: tuple[float, ...]
    diffuse_horizontal_w_m2: tuple[float, ...]
    wind_speed_m_s: tuple[float, ...]


class ClimateSummary(Frozen):
    site_id: str
    name: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    time_zone_h: float
    climate_objective: ClimateObjective
    weather_file: str
    weather_station: str
    weather_source: str
    hours: int
    annual_mean_c: float
    annual_min_c: float
    annual_max_c: float
    heating_degree_days: float
    cooling_degree_days: float
    annual_global_horizontal_kwh_m2: float
    annual_direct_normal_kwh_m2: float
    mean_wind_m_s: float
    design_day: str
    design_day_min_c: float
    design_day_max_c: float
    mean_station_pressure_pa: float
    expected_station_pressure_pa: float
    air_density_kg_m3: float
    sea_level_air_density_kg_m3: float
    months: tuple[ClimateMonth, ...]
    design_day_window: ClimateWindow
    sun_path: SunPath
    has_horizon: bool
    has_ground_temperatures: bool
    horizon_azimuths_deg: tuple[float, ...] = ()
    horizon_blocked_deg: tuple[float, ...] = ()
    warnings: tuple[EngineWarning, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    source: SourceRef


class ThermalBridge(Frozen):
    id: str = Field(min_length=1)
    junction: str = Field(min_length=1)
    psi_w_mk: float = Field(ge=0.0, le=5.0)
    length_m: float = Field(default=0.0, ge=0.0)
    length_from: Literal["explicit", "wall_floor_perimeter", "wall_roof_perimeter",
                         "wall_corners", "window_perimeter"] = "explicit"
    source: SourceRef
    provenance: Provenance = Provenance.CFD

    @model_validator(mode="after")
    def _length_present(self) -> "ThermalBridge":
        if self.length_from == "explicit" and self.length_m <= 0.0:
            raise ValueError(
                f"thermal bridge {self.id} needs a positive length_m or a "
                "length_from rule that derives it from the geometry")
        return self


class SurfaceInfo(Frozen):
    name: str
    zone: str
    kind: SurfaceKind
    facade: Facade | None = None
    area_m2: float = Field(ge=0.0)
    azimuth_deg: float = Field(ge=0.0, lt=360.0)
    tilt_deg: float = Field(ge=0.0, le=180.0)
    construction: str
    outside_boundary: str
    parent: str | None = None


class MeshSurface(Frozen):
    name: str
    zone: str
    kind: SurfaceKind
    facade: Facade | None = None
    positions: tuple[float, ...]
    indices: tuple[int, ...]
    normal: tuple[float, float, float]
    colour: str
    finish: str = "rough"
    area_m2: float = Field(ge=0.0)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    parent: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "MeshSurface":
        if len(self.positions) % 3 != 0:
            raise ValueError("positions must be a flat list of xyz triples")
        vertex_count = len(self.positions) // 3
        if vertex_count < 3:
            raise ValueError("a mesh surface needs at least three vertices")
        if len(self.indices) % 3 != 0:
            raise ValueError("indices must describe triangles")
        if self.indices and max(self.indices) >= vertex_count:
            raise ValueError("index out of range for positions")
        return self


class RoomPayload(Frozen):
    name: str
    x0: float
    y0: float
    x1: float
    y1: float
    area_m2: float = Field(ge=0.0)
    heated: bool = True


class PartitionPayload(Frozen):
    x0: float
    y0: float
    x1: float
    y1: float
    height_m: float = Field(gt=0.0)
    between: tuple[str, str]


class LayoutPayload(Frozen):
    use: ShelterUse
    label: str
    description: str
    rooms: tuple[RoomPayload, ...]
    partitions: tuple[PartitionPayload, ...]
    partition_area_m2: float = Field(ge=0.0)
    thermal_zoning: str
    notes: tuple[str, ...] = ()
    source: SourceRef


class GeometryPayload(Frozen):
    design_hash: str
    arrangement: Arrangement
    azimuth_deg: float
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    surfaces: tuple[MeshSurface, ...]
    zone_volumes_m3: dict[str, float]
    layout: LayoutPayload | None = None
    warnings: tuple[EngineWarning, ...] = ()

    @property
    def surface_names(self) -> tuple[str, ...]:
        return tuple(surface.name for surface in self.surfaces)


class HourlySeries(Frozen):
    name: str
    unit: str
    timestamps: tuple[str, ...]
    values: tuple[float | None, ...]
    provenance: Provenance = Provenance.SIMULATED

    @model_validator(mode="after")
    def _aligned(self) -> "HourlySeries":
        if len(self.timestamps) != len(self.values):
            raise ValueError(f"series {self.name} has mismatched timestamps and values")
        return self


class SurfaceSeries(Frozen):
    surface: str
    zone: str
    kind: SurfaceKind
    inside_face_temperature_c: tuple[float | None, ...] = ()
    outside_face_temperature_c: tuple[float | None, ...] = ()
    conduction_w: tuple[float | None, ...] = ()
    incident_solar_w_m2: tuple[float | None, ...] = ()


class SurfaceResults(Frozen):
    timestamps: tuple[str, ...]
    period: RunPeriod
    surfaces: tuple[SurfaceSeries, ...]

    @model_validator(mode="after")
    def _aligned(self) -> "SurfaceResults":
        span = len(self.timestamps)
        for surface in self.surfaces:
            for field_name in ("inside_face_temperature_c", "outside_face_temperature_c",
                               "conduction_w", "incident_solar_w_m2"):
                values = getattr(surface, field_name)
                if values and len(values) != span:
                    raise ValueError(
                        f"surface {surface.surface} field {field_name} has "
                        f"{len(values)} values against {span} timestamps")
        return self


class ComfortMetrics(Frozen):
    comfort_model_id: str
    minimum_overnight_operative_c: float | None = None
    minimum_overnight_month: int | None = None
    minimum_operative_c: float | None = None
    maximum_operative_c: float | None = None
    maximum_daytime_operative_c: float | None = None
    minimum_on_design_day_c: float | None = None
    design_day_date: str | None = None
    hours_below_band: float = 0.0
    hours_above_band: float = 0.0
    hours_outside_band: float = 0.0
    degree_hours_below_kh: float = 0.0
    degree_hours_above_kh: float = 0.0
    degree_hours_total_kh: float = 0.0
    occupied_hours_outside_band: float = 0.0
    fixed_target_hours_below: float | None = None
    fixed_target_id: str | None = None
    worst_night_margin_k: float | None = None
    mean_operative_c: float | None = None
    mean_daily_swing_k: float | None = None
    decrement_factor: float | None = None
    time_lag_h: float | None = None


class HeatBalanceBreakdown(Frozen):
    walls_kwh: float = 0.0
    roof_kwh: float = 0.0
    floor_kwh: float = 0.0
    windows_conduction_kwh: float = 0.0
    windows_solar_gain_kwh: float = 0.0
    infiltration_kwh: float = 0.0
    ventilation_kwh: float = 0.0
    interzone_kwh: float = 0.0
    internal_gains_kwh: float = 0.0
    thermal_bridge_kwh: float = 0.0
    thermal_bridge_provenance: Provenance = Provenance.DERIVED

    @property
    def total_loss_kwh(self) -> float:
        return sum(max(0.0, -value) for value in (
            self.walls_kwh, self.roof_kwh, self.floor_kwh,
            self.windows_conduction_kwh, self.infiltration_kwh,
            self.ventilation_kwh, self.interzone_kwh)) + max(0.0, self.thermal_bridge_kwh)

    def loss_fractions(self) -> dict[str, float]:
        total = self.total_loss_kwh
        if total <= 0.0:
            return {}
        parts = {
            "walls": max(0.0, -self.walls_kwh),
            "roof": max(0.0, -self.roof_kwh),
            "floor": max(0.0, -self.floor_kwh),
            "windows_conduction": max(0.0, -self.windows_conduction_kwh),
            "infiltration": max(0.0, -self.infiltration_kwh),
            "ventilation": max(0.0, -self.ventilation_kwh),
            "interzone": max(0.0, -self.interzone_kwh),
            "thermal_bridge": max(0.0, self.thermal_bridge_kwh),
        }
        return {key: value / total for key, value in parts.items() if value > 0.0}


class CondensationMetrics(Frozen):
    hours: float = 0.0
    worst_surface: str | None = None
    worst_margin_k: float | None = None
    minimum_inner_glazing_c: float | None = None
    peak_indoor_rh_percent: float | None = None


class EnergyMetrics(Frozen):
    heating_demand_kwh: float | None = None
    cooling_demand_kwh: float | None = None
    peak_heating_w: float | None = None
    fuel: str | None = None
    fuel_units: float | None = None
    fuel_unit_name: str | None = None
    fuel_cost_inr: float | None = None
    provenance: Provenance = Provenance.SIMULATED


class CostBreakdown(Frozen):
    envelope_inr: float = 0.0
    glazing_inr: float = 0.0
    shutters_inr: float = 0.0
    pcm_inr: float = 0.0
    total_inr: float = 0.0
    max_mason_skill: str = "unskilled"
    non_local_material_fraction: float = 0.0
    assumptions: tuple[Assumption, ...] = ()


class BuildabilityMetrics(Frozen):
    wall_thickness_m: float
    surface_to_volume: float
    seismic_flag: bool = False
    seismic_note: str | None = None
    minimum_ventilation_met: bool = True
    heaviest_wall_kg_m2: float = 0.0


class UncertaintyContribution(Frozen):
    parameter: str
    low_value: float
    high_value: float
    effect_on_metric: float
    share: float = Field(ge=0.0, le=1.0)


class UncertaintyBand(Frozen):
    metric: str
    unit: str
    nominal: float
    low: float
    high: float
    dominant_parameter: str | None = None
    contributions: tuple[UncertaintyContribution, ...] = ()
    runs: int = 0

    @model_validator(mode="after")
    def _ordered(self) -> "UncertaintyBand":
        if self.high < self.low:
            raise ValueError("uncertainty band high must be >= low")
        return self


class Recommendation(Frozen):
    code: str
    title: str
    detail: str
    priority: Literal["high", "medium", "low"]
    triggered_by: dict[str, float]
    expected_effect: str | None = None
    provenance: Provenance = Provenance.DERIVED


class ReferenceComparison(Frozen):
    reference_name: str
    reference_hash: str
    citation: SourceRef | None = None
    delta_minimum_overnight_k: float | None = None
    delta_degree_hours_kh: float | None = None
    delta_heating_demand_kwh: float | None = None
    fuel_saved_units: float | None = None
    fuel_unit_name: str | None = None
    rupees_saved_per_year: float | None = None
    delta_cost_inr: float | None = None


class SimulationResult(Frozen):
    design_hash: str
    design_fingerprint: str = ""
    design: DesignSpec
    site_id: str
    period: RunPeriod
    climate_objective: ClimateObjective
    headline_metric: str
    comfort: ComfortMetrics
    energy: EnergyMetrics
    breakdown: HeatBalanceBreakdown
    condensation: CondensationMetrics
    cost: CostBreakdown
    buildability: BuildabilityMetrics
    hourly: tuple[HourlySeries, ...] = ()
    surfaces: SurfaceResults | None = None
    reference: ReferenceComparison | None = None
    uncertainty: tuple[UncertaintyBand, ...] = ()
    recommendations: tuple[Recommendation, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    warnings: tuple[EngineWarning, ...] = ()
    engine_version: str = ""
    energyplus_version: str = ""
    runtime_s: float = 0.0
    provenance: Provenance = Provenance.SIMULATED
    cached: bool = False

    @property
    def has_blocking_warnings(self) -> bool:
        return any(w.severity in (Severity.SEVERE, Severity.FATAL) for w in self.warnings)


class ComparisonRow(Frozen):
    design_hash: str
    name: str
    is_reference: bool = False
    minimum_overnight_operative_c: float | None = None
    minimum_overnight_band: tuple[float, float] | None = None
    hours_below_band: float | None = None
    degree_hours_total_kh: float | None = None
    heating_demand_kwh: float | None = None
    fuel_units: float | None = None
    total_cost_inr: float | None = None
    wall_thickness_m: float | None = None
    locally_available: bool | None = None
    mason_skill: str | None = None
    condensation_hours: float | None = None
    delta_vs_reference: dict[str, float] = Field(default_factory=dict)


class ComparisonTable(Frozen):
    reference_hash: str
    headline_metric: str
    climate_objective: ClimateObjective
    rows: tuple[ComparisonRow, ...]
    warnings: tuple[EngineWarning, ...] = ()


class DesignVariable(Frozen):
    name: str
    kind: Literal["real", "integer", "choice", "binary"]
    low: float | None = None
    high: float | None = None
    options: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _valid(self) -> "DesignVariable":
        if self.kind in ("real", "integer"):
            if self.low is None or self.high is None:
                raise ValueError(f"variable {self.name} needs low and high")
            if self.high <= self.low:
                raise ValueError(f"variable {self.name} has high <= low")
        if self.kind == "choice":
            if not self.options or len(self.options) < 2:
                raise ValueError(f"choice variable {self.name} needs at least two options")
            if len(set(self.options)) != len(self.options):
                raise ValueError(f"choice variable {self.name} has duplicate options")
        return self


class OptimisationConstraints(Frozen):
    minimum_fresh_air_ach: float = Field(default=0.35, ge=0.0, le=10.0)
    max_wall_thickness_m: float = Field(default=0.60, gt=0.0, le=2.0)
    max_condensation_hours: float = Field(default=50.0, ge=0.0)
    max_total_cost_inr: float | None = Field(default=None, ge=0.0)
    min_worst_night_margin_k: float | None = None
    seismic_wall_thickness_flag_m: float = Field(default=0.45, gt=0.0, le=2.0)
    source: SourceRef | None = None


class OptimisationSpec(Frozen):
    base_design: DesignSpec
    variables: tuple[DesignVariable, ...] = Field(min_length=1, max_length=24)
    constraints: OptimisationConstraints = OptimisationConstraints()
    population_size: int = Field(default=40, ge=8, le=400)
    generations: int = Field(default=25, ge=1, le=500)
    seed: int = 1
    objectives: tuple[str, ...] = ("degree_hours_total_kh", "total_cost_inr",
                                   "worst_night_margin_k")

    @model_validator(mode="after")
    def _names_unique(self) -> "OptimisationSpec":
        names = [variable.name for variable in self.variables]
        if len(set(names)) != len(names):
            raise ValueError("optimisation variable names must be unique")
        if len(set(self.objectives)) != len(self.objectives):
            raise ValueError("objectives must be unique")
        if not self.objectives:
            raise ValueError("at least one objective is required")
        return self

    @property
    def evaluations(self) -> int:
        return self.population_size * (self.generations + 1)


class ParetoPoint(Frozen):
    design_hash: str
    design: DesignSpec
    objectives: dict[str, float]
    constraint_violation: float = 0.0
    feasible: bool = True
    metrics: dict[str, float] = Field(default_factory=dict)
    flags: tuple[str, ...] = ()


class ParetoResult(Frozen):
    spec_hash: str
    site_id: str
    front: tuple[ParetoPoint, ...]
    evaluated: int
    feasible_evaluated: int
    failed: int = 0
    objectives: tuple[str, ...] = ()
    runtime_s: float = 0.0
    warnings: tuple[EngineWarning, ...] = ()
    knee_hash: str | None = None


class CFDBoundarySurface(Frozen):
    name: str
    kind: SurfaceKind
    facade: Facade | None = None
    positions: tuple[float, ...]
    indices: tuple[int, ...]
    area_m2: float
    inside_face_temperature_c: float
    emissivity: float = 0.9


class CFDExport(Frozen):
    design_hash: str
    zone: str
    label: str
    timestamp: str
    zone_air_temperature_c: float
    zone_relative_humidity_percent: float | None = None
    outdoor_air_temperature_c: float | None = None
    air_pressure_pa: float | None = None
    zone_volume_m3: float
    surfaces: tuple[CFDBoundarySurface, ...]
    units: str = "SI"
    notes: tuple[str, ...] = ()
    source_run: str | None = None
