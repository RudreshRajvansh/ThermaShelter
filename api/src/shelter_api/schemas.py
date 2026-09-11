from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shelter_engine.schema import (Assumption, ClimateObjective, DesignSpec,
                                   DesignVariable, EngineWarning,
                                   OptimisationConstraints, ParetoResult,
                                   OccupancySpec, PlanSpec, RunOptions,
                                   RunPeriod, ShelterUse, SourceRef,
                                   ThermalFieldPayload)

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


class ThermalFieldRequest(Body):
    design: DesignSpec
    site_id: str | None = None
    comfort_model_id: str | None = None
    hour_index: int = Field(default=0, ge=0)
    zone: str = "ROOM"
    resolution: tuple[int, int, int] = (32, 32, 16)
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


class OptimiseRequest(Body):
    base_design: DesignSpec
    variables: list[DesignVariable] = Field(min_length=1, max_length=24)
    constraints: OptimisationConstraints = OptimisationConstraints()
    objectives: list[str] = Field(default_factory=lambda: [
        "degree_hours_total_kh", "total_cost_inr", "worst_night_margin_k"])
    population_size: int = Field(default=24, ge=8, le=200)
    generations: int = Field(default=10, ge=1, le=200)
    seed: int = 1
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
