from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from shelter_engine import __version__ as engine_version
from shelter_engine.config import energyplus_available, find_energyplus
from shelter_engine.schema import (ClimateObjective, ComfortModel, DesignSpec,
                                   FixedComfortTarget, FuelModel, GlazingSystem,
                                   Material, PhaseChangeMaterial, SiteSpec,
                                   SourceStatus)

from ..deps import Context, get_context
from ..live import fetch_live_weather
from shelter_engine.programme import PROGRAMMES, occupancy_for, plan_for

from ..schemas import (DesignSummary, HealthResponse, LiveWeather,
                       MetaResponse,
                       ShelterUseSummary,
                       SiteSummary, ValidationEvidence)

router = APIRouter()

API_VERSION = "0.1.0"


def _headline(objective: ClimateObjective) -> str:
    if objective is ClimateObjective.COOLING_DOMINATED:
        return "maximum_daytime_operative_c"
    return "minimum_overnight_operative_c"


def summarise_site(site: SiteSpec) -> SiteSummary:
    return SiteSummary(
        id=site.id, name=site.name, latitude_deg=site.latitude_deg,
        longitude_deg=site.longitude_deg, elevation_m=site.elevation_m,
        climate_objective=site.climate_objective,
        headline_metric=_headline(site.climate_objective),
        has_horizon=site.horizon is not None,
        has_ground_temperatures=site.ground is not None,
        weather_file=Path(site.epw_path).name)


@router.get("/health", response_model=HealthResponse, tags=["meta"])
def health(context: Context = Depends(get_context)) -> HealthResponse:
    blocking = [item for item in context.startup_warnings
                if item.severity.value in ("severe", "fatal")]
    if context.ready and not blocking:
        return HealthResponse(status="ok", ready=True,
                              detail="Engine, weather and materials are loaded.",
                              warnings=context.startup_warnings)
    return HealthResponse(
        status="degraded", ready=context.ready,
        detail=("The API is up but cannot simulate. Each warning below names what is "
                "missing and who owns it."),
        warnings=context.startup_warnings)


@router.get("/meta", response_model=MetaResponse, tags=["meta"])
def meta(context: Context = Depends(get_context)) -> MetaResponse:
    library = context.library
    sites = [summarise_site(site) for site in context.sites.values()]

    gaps: list[str] = []
    if library is not None and library.unsourced():
        gaps.append(
            f"{len(library.unsourced())} material or glazing records do not carry a "
            "verified source")
    uncited = context.references.uncited()
    if uncited:
        gaps.append(f"{len(uncited)} reference designs are uncited: {', '.join(uncited)}")
    for model in context.catalogue.adaptive:
        if model.id == context.settings.comfort_model_id \
                and model.source.status is not SourceStatus.VERIFIED:
            gaps.append(
                f"the comfort model {model.id} is marked {model.source.status.value}; "
                "its coefficients must be checked against the paper")
    for fuel in context.catalogue.fuels:
        if fuel.fuel == context.settings.fuel and fuel.price_inr_per_unit <= 0.0:
            gaps.append("the fuel price is zero, so every rupee figure is zero by "
                        "construction")
    for site in context.sites.values():
        if site.ground is None:
            gaps.append(f"site {site.id} has no monthly ground temperatures")
        if site.horizon is None:
            gaps.append(f"site {site.id} has no terrain horizon profile")

    return MetaResponse(
        engine_version=engine_version,
        energyplus_version=(find_energyplus().version_string
                            if energyplus_available() else None),
        energyplus_available=energyplus_available(),
        api_version=API_VERSION,
        default_site_id=(context.settings.default_site
                         if context.settings.default_site in context.sites
                         else next(iter(context.sites), None)),
        sites=sites,
        reference_designs=list(context.references.names()),
        default_reference=context.references.default_name,
        comfort_models=[model.id for model in context.catalogue.adaptive],
        comfort_model_id=context.settings.comfort_model_id,
        fuel=context.settings.fuel,
        materials=len(library.materials) if library else 0,
        glazing_systems=len(library.glazing_systems) if library else 0,
        phase_change_materials=len(library.phase_change_materials) if library else 0,
        stored_runs=context.store.count_runs(),
        warnings=context.startup_warnings,
        provenance_gaps=gaps)


def _library(context: Context):
    if context.library is None:
        raise HTTPException(
            status_code=503,
            detail=("The material database is not loaded. Layer 0 owns "
                    f"{context.settings.materials_path}."))
    return context.library


@router.get("/materials", response_model=list[Material], tags=["catalogue"])
def materials(context: Context = Depends(get_context)) -> list[Material]:
    return list(_library(context).materials)


@router.get("/glazing", response_model=list[GlazingSystem], tags=["catalogue"])
def glazing(context: Context = Depends(get_context)) -> list[GlazingSystem]:
    return list(_library(context).glazing_systems)


@router.get("/phase-change-materials", response_model=list[PhaseChangeMaterial],
            tags=["catalogue"])
def phase_change(context: Context = Depends(get_context)) -> list[PhaseChangeMaterial]:
    return list(_library(context).phase_change_materials)


@router.get("/references", response_model=list[DesignSpec], tags=["catalogue"])
def references(context: Context = Depends(get_context)) -> list[DesignSpec]:
    return list(context.references.designs)


@router.get("/references/{name}", response_model=DesignSpec, tags=["catalogue"])
def reference(name: str, context: Context = Depends(get_context)) -> DesignSpec:
    try:
        return context.references.get(name)
    except Exception as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _design_files(context: Context) -> dict[str, Path]:
    directory = Path(context.settings.designs_dir)
    if not directory.is_dir():
        return {}
    return {path.stem: path for path in sorted(directory.glob("*.json"))}


def _load_design(path: Path) -> DesignSpec:
    return DesignSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


@router.get("/designs", response_model=list[DesignSummary], tags=["catalogue"])
def designs(context: Context = Depends(get_context)) -> list[DesignSummary]:
    reference_names = {design.name for design in context.references.designs}
    summaries: list[DesignSummary] = []
    for identifier, path in _design_files(context).items():
        try:
            design = _load_design(path)
        except (OSError, ValueError):
            continue
        summaries.append(DesignSummary(
            id=identifier,
            name=design.name,
            use=design.plan.use.value,
            arrangement=design.arrangement.value,
            floor_area_m2=design.plan.floor_area_m2,
            occupants=design.occupancy.occupants,
            is_reference=design.name in reference_names,
        ))
    return summaries


@router.get("/designs/{identifier}", response_model=DesignSpec, tags=["catalogue"])
def design(identifier: str, context: Context = Depends(get_context)) -> DesignSpec:
    path = _design_files(context).get(identifier)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=(f"There is no stored design called {identifier!r}. "
                    f"The library holds {sorted(_design_files(context))}."))
    try:
        return _load_design(path)
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=500,
            detail=f"The stored design {identifier!r} could not be read: {error}",
        ) from error


@router.get("/live-weather", response_model=LiveWeather, tags=["catalogue"])
def live_weather(site_id: str | None = None,
                 context: Context = Depends(get_context)) -> LiveWeather:
    site = context.site(site_id)
    return fetch_live_weather(site.id, site.name, site.latitude_deg,
                              site.longitude_deg, site.elevation_m)


@router.get("/comfort-models", response_model=list[ComfortModel], tags=["catalogue"])
def comfort_models(context: Context = Depends(get_context)) -> list[ComfortModel]:
    return list(context.catalogue.adaptive)


@router.get("/comfort-targets", response_model=list[FixedComfortTarget],
            tags=["catalogue"])
def comfort_targets(context: Context = Depends(get_context)) -> list[FixedComfortTarget]:
    return list(context.catalogue.fixed)


@router.get("/shelter-uses", response_model=list[ShelterUseSummary],
            tags=["catalogue"])
def shelter_uses() -> list[ShelterUseSummary]:
    return [ShelterUseSummary(
        use=spec.use, label=spec.label, description=spec.description,
        area_per_person_m2=spec.area_per_person_m2,
        default_occupants=spec.occupants,
        default_floor_area_m2=spec.floor_area_m2,
        ceiling_height_m=spec.ceiling_height_m, aspect_ratio=spec.aspect_ratio,
        room_names=tuple(room.name for room in spec.rooms),
        plan=plan_for(spec.use), occupancy=occupancy_for(spec.use),
        source=spec.source) for spec in PROGRAMMES.values()]


@router.get("/fuels", response_model=list[FuelModel], tags=["catalogue"])
def fuels(context: Context = Depends(get_context)) -> list[FuelModel]:
    return list(context.catalogue.fuels)


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


@router.get("/validation", response_model=ValidationEvidence, tags=["validation"])
def validation(context: Context = Depends(get_context)) -> ValidationEvidence:
    directory = Path(context.settings.validation_dir)
    standard = _read_json(directory / "standard_140.json")
    bridge = _read_json(directory / "thermal_bridge.json")
    calibration = _read_json(directory / "calibration.json")
    available = any(item is not None for item in (standard, bridge, calibration))
    detail = ("Validation evidence produced offline by Layer 1 and committed as files."
              if available else
              "No validation evidence has been committed yet. Layer 1 owns the "
              "ASHRAE Standard 140 runs, the ANSYS thermal bridge calculation and the "
              f"calibration comparison; they belong in {directory}. This endpoint "
              "reports their absence rather than inventing a result.")
    return ValidationEvidence(standard_140=standard, thermal_bridge=bridge,
                              calibration=calibration, available=available,
                              detail=detail)
