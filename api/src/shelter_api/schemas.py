from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shelter_engine.schema import (Assumption, CFDExport, CFDField, ClimateObjective,
                                   DesignSpec, DesignVariable, EngineWarning,
                                   MapdlReport,
                                   OptimisationConstraints, ParetoResult,
                                   OccupancySpec, PlanSpec, RunOptions,
                                   RunPeriod, ShelterUse, SiteSpec, SourceRef)

DEFAULT_START = date(2019, 1, 1)
DEFAULT_END = date(2019, 1, 31)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Window(Body):
    start: date = DEFAULT_START
    end: date = DEFAULT_END

    @model_validator(mode="after")
    def _ordered(self) -> "Window":
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        if self.start.year != self.end.year:
            raise ValueError("the period must lie inside a single calendar year")
        return self

    def to_period(self, label: str = "run") -> RunPeriod:
        return RunPeriod(start=self.start, end=self.end, label=label)


class GeometryRequest(Body):
    design: DesignSpec
    site_id: str | None = None
    comfort_model_id: str | None = None


class LiveObservation(Body):
    air_temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    wind_speed_m_s: float | None = None
    surface_pressure_pa: float | None = None
    cloud_cover_pct: float | None = None


class LiveWeather(Body):
    site_id: str
    available: bool
    observation: LiveObservation | None = None
    observed_at: str | None = None
    fetched_at: str | None = None
    location: str | None = None
    provider_elevation_m: float | None = None
    elevation_mismatch: str | None = None
    detail: str | None = None
    provider: str
    note: str


class DesignSummary(Body):
    id: str
    name: str
    use: str
    arrangement: str
    floor_area_m2: float
    occupants: int
    is_reference: bool


class PostCalibration(Body):
    infiltration_ach: float = Field(gt=0.0, le=20.0)
    mass_multiplier: float = Field(default=1.0, gt=0.2, le=5.0)
    rmse_before_k: float | None = Field(default=None, ge=0.0)
    rmse_after_k: float | None = Field(default=None, ge=0.0)
    calibrated_on: date | None = None
    hours: int | None = Field(default=None, ge=1)


class Post(Body):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    name: str = Field(min_length=1, max_length=80)
    site_id: str = Field(min_length=1)
    latitude_deg: float = Field(ge=-90.0, le=90.0)
    longitude_deg: float = Field(ge=-180.0, le=180.0)
    design: DesignSpec
    soldiers: int = Field(ge=1, le=200)
    fuel: Literal["kerosene", "diesel", "lpg", "wood", "electricity"] = "kerosene"
    stock_units: float | None = Field(default=None, ge=0.0)
    calibration: PostCalibration | None = None


class PostSummary(Body):
    post: Post
    status: Literal["red", "amber", "green"] | None = None
    night_of: date | None = None
    issued_at: datetime | None = None


class DesignPackRequest(Body):
    design: DesignSpec
    site_id: str | None = None
    period: Window = Window()
    run_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class SimulateRequest(Body):
    design: DesignSpec
    site_id: str | None = None
    period: Window = Window()
    surface_period: Window | None = None
    timestep_per_hour: int = Field(default=6, ge=1, le=60)
    conditioned_run: bool = True
    surface_output: bool = True
    with_uncertainty: bool = True
    with_reference: bool = True
    reference: str | None = None
    comfort_model_id: str | None = None
    shutter_compliance_override: float | None = Field(default=None, ge=0.0, le=1.0)

    def to_options(self) -> RunOptions:
        return RunOptions(
            period=self.period.to_period(),
            surface_output_period=(self.surface_period.to_period("surface")
                                   if self.surface_period else None),
            timestep_per_hour=self.timestep_per_hour,
            conditioned_run=self.conditioned_run,
            surface_output=self.surface_output,
            shutter_compliance_override=self.shutter_compliance_override)


class SurfaceRequest(Body):
    design: DesignSpec
    site_id: str | None = None
    comfort_model_id: str | None = None
    period: Window = Window()
    surface_period: Window | None = None
    timestep_per_hour: int = Field(default=6, ge=1, le=60)

    def to_options(self) -> RunOptions:
        return RunOptions(
            period=self.period.to_period(),
            surface_output_period=(self.surface_period.to_period("surface")
                                   if self.surface_period else None),
            timestep_per_hour=self.timestep_per_hour,
            conditioned_run=False, surface_output=True)


class CompareRequest(Body):
    designs: list[DesignSpec] = Field(min_length=1, max_length=8)
    site_id: str | None = None
    period: Window = Window()
    conditioned_run: bool = True
    reference: str | None = None
    comfort_model_id: str | None = None

    def to_options(self) -> RunOptions:
        return RunOptions(period=self.period.to_period(),
                          conditioned_run=self.conditioned_run,
                          surface_output=False)


class LeverRequest(Body):
    design: DesignSpec
    site_id: str | None = None


class LeverPayload(Body):
    variable: DesignVariable
    label: str
    group: str


class OptimiseRequest(Body):
    base_design: DesignSpec
    variables: list[DesignVariable] = Field(min_length=1, max_length=24)
    constraints: OptimisationConstraints = OptimisationConstraints()
    objectives: list[str] = Field(default_factory=lambda: [
        "night_degree_hours_below_kh", "heating_litres", "imported_tonnes"])
    population_size: int = Field(default=24, ge=8, le=200)
    generations: int = Field(default=10, ge=1, le=200)
    seed: int = 1
    workers: int | None = Field(default=None, ge=1, le=32)
    robust_top: int = Field(default=5, ge=0, le=20)
    robust_keep: int = Field(default=3, ge=1, le=20)
    site_id: str | None = None
    period: Window = Window()

    def to_options(self) -> RunOptions:
        return RunOptions(period=self.period.to_period(), conditioned_run=False,
                          surface_output=False)


class ProjectRequest(Body):
    id: str = Field(min_length=1, max_length=80,
                    pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=160)
    site_id: str | None = None
    design: DesignSpec
    period: Window = Window()
    notes: str | None = None


class SiteSummary(Body):
    id: str
    name: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    climate_objective: ClimateObjective
    headline_metric: str
    has_horizon: bool
    has_ground_temperatures: bool
    weather_file: str


class MetaResponse(Body):
    engine_version: str
    energyplus_version: str | None
    energyplus_available: bool
    api_version: str
    default_site_id: str | None
    sites: list[SiteSummary]
    reference_designs: list[str]
    default_reference: str
    comfort_models: list[str]
    comfort_model_id: str
    fuel: str | None
    materials: int
    glazing_systems: int
    phase_change_materials: int
    stored_runs: int
    warnings: list[EngineWarning]
    provenance_gaps: list[str]


class HealthResponse(Body):
    status: Literal["ok", "degraded"]
    ready: bool
    detail: str
    warnings: list[EngineWarning]


class OptimisationJob(Body):
    job_id: str
    site_id: str
    status: Literal["running", "done", "failed"]
    created_at: str
    finished_at: str | None = None
    evaluated: int = 0
    message: str | None = None
    result: ParetoResult | None = None


class CFDAbsent(Body):
    available: Literal[False] = False
    design_hash: str
    reason: str
    boundary_labels: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)


class MapdlAbsent(Body):
    available: Literal[False] = False
    design_hash: str
    reason: str
    rejected: list[str] = Field(default_factory=list)


class MapdlSet(Body):
    available: Literal[True] = True
    design_hash: str
    reports: list[MapdlReport]
    rejected: list[str] = Field(default_factory=list)


class CFDFieldSet(Body):
    available: Literal[True] = True
    design_hash: str
    fields: list[CFDField] = Field(min_length=1)
    boundary_labels: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)


class CFDBoundarySet(Body):
    design_hash: str
    exports: list[CFDExport] = Field(min_length=1)
    rejected: list[str] = Field(default_factory=list)


class PlaceResult(Body):
    name: str
    region: str = ""
    country: str = ""
    country_code: str | None = None
    latitude_deg: float
    longitude_deg: float
    elevation_m: float | None = None


class ResolveSiteRequest(Body):
    latitude_deg: float = Field(ge=-90.0, le=90.0)
    longitude_deg: float = Field(ge=-180.0, le=180.0)
    name: str | None = Field(default=None, max_length=120)


class SiteProvenance(Body):
    elevation_source: str
    horizon_source: str
    horizon_directions: int
    horizon_max_distance_km: float
    weather_provider: str
    weather_dataset: str
    weather_model: str | None = None
    weather_year: int
    grid_latitude_deg: float | None = None
    grid_longitude_deg: float | None = None
    grid_distance_km: float | None = None
    time_zone: str | None = None
    utc_offset_h: float
    heating_degree_days: float
    cooling_degree_days: float
    annual_mean_c: float
    coldest_month_mean_c: float
    warmest_month_mean_c: float
    place_name_source: str | None = None
    country_code: str | None = None


class SiteDetail(Body):
    site: SiteSpec
    summary: SiteSummary
    provenance: SiteProvenance | None = None
    notes: list[str] = Field(default_factory=list)
    warnings: list[EngineWarning] = Field(default_factory=list)
    created: bool = False
    requested_latitude_deg: float | None = None
    requested_longitude_deg: float | None = None
    substituted_km: float | None = None


class EnduranceRequest(Body):
    design: DesignSpec
    site_id: str | None = None
    floor_c: float = Field(default=5.0, ge=-10.0, le=20.0)
    cutoff_hour: int = Field(default=18, ge=0, le=23)
    extra_days: int = Field(default=2, ge=0, le=10)
    fuel: Literal["kerosene", "diesel"] = "kerosene"


class LeftoverHeatPayload(Body):
    night_heating_kwh: float
    night_peak_kw: float | None = None
    litres: float | None = None
    him_tapak_hours_low: float | None = None
    him_tapak_hours_high: float | None = None
    him_tapak_fuel_input_kw: list[float] | None = None
    him_tapak_source: SourceRef
    solar_storage_kwh: float
    solar_system_source: SourceRef
    notes: list[str] = Field(default_factory=list)


class EndurancePayload(Body):
    site_id: str
    night_of: str
    design_night_minimum_c: float
    return_level_c: float
    return_period_years: float
    cutoff: str
    floor_c: float
    floor_source: SourceRef
    reached_floor: bool
    hours_above_floor: float
    hours_simulated_after_cutoff: float
    operative_at_cutoff_c: float
    minimum_after_cutoff_c: float
    minimum_at: str
    night_heating_kwh: float
    night_fuel_units: float | None = None
    fuel: str | None = None
    fuel_unit_name: str | None = None
    weather_source: str
    leftover_heat: LeftoverHeatPayload



class HorizonCase(Body):
    minimum_overnight_operative_c: float | None = None
    hours_below_band: float | None = None
    degree_hours_below_kh: float | None = None
    heating_kwh: float | None = None
    fuel_units: float | None = None
    fuel_unit_name: str | None = None
    windows_solar_gain_kwh: float | None = None


class HorizonValuePayload(Body):
    site_id: str
    with_horizon: HorizonCase
    flat: HorizonCase
    overnight_minimum_difference_k: float | None = None
    heating_difference_kwh: float | None = None
    fuel_difference_units: float | None = None
    max_blocked_altitude_deg: float
    horizon_source: SourceRef


class WinterMinimumPayload(Body):
    winter: int
    night_of: str
    minimum_c: float
    observed_c: float | None = None


class DesignNightPayload(Body):
    site_id: str
    night_of: str
    minimum_c: float
    return_level_c: float
    return_period_years: float
    gumbel_location_c: float
    gumbel_scale_k: float
    winters: list[WinterMinimumPayload]
    source: str
    source_model: str
    source_note: str
    station: str | None = None
    station_distance_km: float | None = None
    epw_path: str
    run_start: str
    run_end: str
    notes: list[str] = Field(default_factory=list)


class MonthCheckPayload(Body):
    month: int = Field(ge=1, le=12)
    file: float
    reference: float
    difference: float
    ratio: float | None = None


class RefereePayload(Body):
    site_id: str
    weather_file: str
    station: str | None = None
    station_distance_km: float | None = None
    night_minimum: list[MonthCheckPayload] = Field(default_factory=list)
    day_maximum: list[MonthCheckPayload] = Field(default_factory=list)
    hours_below_record_low: int = 0
    coldest_hour_c: float | None = None
    record_low_c: float | None = None
    record_low_date: str | None = None
    solar: list[MonthCheckPayload] = Field(default_factory=list)
    annual_solar_file_kwh_m2: float | None = None
    annual_solar_reference_kwh_m2: float | None = None
    solar_source: str | None = None
    solar_factors: dict[int, float] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ValidationEvidence(Body):
    standard_140: dict[str, Any] | None = None
    thermal_bridge: dict[str, Any] | None = None
    calibration: dict[str, Any] | None = None
    available: bool = False
    detail: str = ""
    assumptions: list[Assumption] = Field(default_factory=list)


class ShelterUseSummary(BaseModel):
    use: ShelterUse
    label: str
    description: str
    area_per_person_m2: float
    default_occupants: int
    default_floor_area_m2: float
    ceiling_height_m: float
    aspect_ratio: float
    room_names: tuple[str, ...]
    plan: PlanSpec
    occupancy: OccupancySpec
    source: SourceRef
