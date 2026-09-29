from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from fastapi import Query
from pydantic import ValidationError

from shelter_engine.cfd import (find_boundary_conditions, find_cfd_fields, label_of,
                                load_cfd_export, load_cfd_field)
from shelter_engine.errors import (EngineError, GeometryError, MaterialError,
                                   ResultsUnavailable, SimulationFailed,
                                   SimulationTimeout)
from shelter_engine.hashing import stable_hash
from shelter_engine.schema import (ClimateSummary, ComparisonTable, GeometryPayload,
                                   SimulationResult, SunPath, SurfaceResults,
                                   TerrainMesh)

from ..deps import Context, get_context
from ..schemas import (CFDAbsent, CFDBoundarySet, CFDFieldSet, CompareRequest,
                       EndurancePayload, EnduranceRequest, GeometryRequest,
                       HorizonCase, HorizonValuePayload, LeftoverHeatPayload,
                       SimulateRequest, SurfaceRequest)

router = APIRouter()

DESIGN_HASH_PATTERN = r"^[0-9a-f]{32}$"


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, (GeometryError, MaterialError)):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, SimulationTimeout):
        return HTTPException(status_code=504, detail=str(error))
    if isinstance(error, SimulationFailed):
        return HTTPException(status_code=502, detail=str(error))
    if isinstance(error, EngineError):
        return HTTPException(status_code=422, detail=str(error))
    raise error


@router.post("/endurance", response_model=EndurancePayload, tags=["design"])
def endurance(request: EnduranceRequest,
              context: Context = Depends(get_context)) -> EndurancePayload:
    from shelter_engine.endurance import FLOOR_SOURCE
    from shelter_engine.leftover_heat import (HIM_TAPAK_SOURCE, SOLAR_SYSTEM_SOURCE,
                                              leftover_heat)
    from shelter_engine.schema import RunPeriod

    from .. import sites as site_builder

    site = context.site(request.site_id)
    try:
        night = site_builder.build_design_night(site, Path(context.settings.data_dir))
    except site_builder.SiteUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail=f"No design night is available for {site.name}: {error}") from error
    night_of = date_type.fromisoformat(night["night_of"])
    start = date_type.fromisoformat(night["run_start"])
    end = min(date_type(night_of.year, 12, 31),
              date_type.fromisoformat(night["run_end"])
              + timedelta(days=request.extra_days))
    engine = context.design_night_engine(request.site_id, request.fuel, night)
    try:
        result = engine.endurance(request.design,
                                  night_of, RunPeriod(start=start, end=end,
                                                      label="design night"),
                                  floor_c=request.floor_c,
                                  cutoff_hour=request.cutoff_hour)
    except Exception as error:
        raise _translate(error) from error
    spare = leftover_heat(result.night_heating_kwh, result.night_peak_w, engine.fuel)
    return EndurancePayload(
        site_id=site.id, night_of=night["night_of"],
        design_night_minimum_c=night["minimum_c"],
        return_level_c=night["return_level_c"],
        return_period_years=night["return_period_years"],
        cutoff=result.cutoff.isoformat(), floor_c=result.floor_c,
        floor_source=FLOOR_SOURCE, reached_floor=result.reached_floor,
        hours_above_floor=round(result.hours_above_floor, 1),
        hours_simulated_after_cutoff=round(result.hours_simulated_after_cutoff, 1),
        operative_at_cutoff_c=round(result.operative_at_cutoff_c, 2),
        minimum_after_cutoff_c=round(result.minimum_after_cutoff_c, 2),
        minimum_at=result.minimum_at.isoformat(),
        night_heating_kwh=round(result.night_heating_kwh, 2),
        night_fuel_units=(round(result.night_fuel_units, 2)
                          if result.night_fuel_units is not None else None),
        fuel=result.fuel, fuel_unit_name=result.fuel_unit_name,
        weather_source=night["source"],
        leftover_heat=LeftoverHeatPayload(
            night_heating_kwh=round(spare.night_heating_kwh, 2),
            night_peak_kw=(round(spare.night_peak_w / 1000.0, 2)
                           if spare.night_peak_w is not None else None),
            litres=round(spare.litres, 2) if spare.litres is not None else None,
            him_tapak_hours_low=(round(spare.him_tapak_hours_low, 1)
                                 if spare.him_tapak_hours_low is not None else None),
            him_tapak_hours_high=(round(spare.him_tapak_hours_high, 1)
                                  if spare.him_tapak_hours_high is not None else None),
            him_tapak_fuel_input_kw=([round(value, 2) for value in spare.him_tapak_input_kw]
                                     if spare.him_tapak_input_kw else None),
            him_tapak_source=HIM_TAPAK_SOURCE,
            solar_storage_kwh=round(spare.solar_storage_kwh, 2),
            solar_system_source=SOLAR_SYSTEM_SOURCE, notes=list(spare.notes)))


@router.post("/horizon-value", response_model=HorizonValuePayload, tags=["design"])
def horizon_value(request: SimulateRequest,
                  context: Context = Depends(get_context)) -> HorizonValuePayload:
    engine = context.engine(request.site_id, request.comfort_model_id)
    if engine.site.horizon is None:
        raise HTTPException(
            status_code=422,
            detail=(f"{engine.site.name} has no horizon profile, so there is nothing to "
                    "compare a flat skyline against."))
    options = request.to_options().model_copy(update={"surface_output": False})
    runs = {}
    try:
        for label, shaded in (("with_horizon", True), ("flat", False)):
            result = engine.simulate(
                request.design, options.model_copy(update={"include_horizon_shading":
                                                           shaded}),
                with_uncertainty=False, with_reference=False)
            runs[label] = HorizonCase(
                minimum_overnight_operative_c=result.comfort.minimum_overnight_operative_c,
                hours_below_band=result.comfort.hours_below_band,
                degree_hours_below_kh=result.comfort.degree_hours_below_kh,
                heating_kwh=result.energy.heating_demand_kwh,
                fuel_units=result.energy.fuel_units,
                fuel_unit_name=result.energy.fuel_unit_name,
                windows_solar_gain_kwh=result.breakdown.windows_solar_gain_kwh)
    except Exception as error:
        raise _translate(error) from error
    real, flat = runs["with_horizon"], runs["flat"]

    def gap(a: float | None, b: float | None) -> float | None:
        return round(a - b, 3) if a is not None and b is not None else None

    return HorizonValuePayload(
        site_id=engine.site.id, with_horizon=real, flat=flat,
        overnight_minimum_difference_k=gap(real.minimum_overnight_operative_c,
                                           flat.minimum_overnight_operative_c),
        heating_difference_kwh=gap(real.heating_kwh, flat.heating_kwh),
        fuel_difference_units=gap(real.fuel_units, flat.fuel_units),
        max_blocked_altitude_deg=engine.site.horizon.max_blocked_deg,
        horizon_source=engine.site.horizon.source)


@router.post("/geometry", response_model=GeometryPayload, tags=["design"])
def geometry(request: GeometryRequest,
             context: Context = Depends(get_context)) -> GeometryPayload:
    engine = context.engine(request.site_id, request.comfort_model_id)
    try:
        return engine.geometry(request.design)
    except Exception as error:
        raise _translate(error) from error


@router.post("/simulate", response_model=SimulationResult, tags=["design"])
def simulate(request: SimulateRequest,
             context: Context = Depends(get_context)) -> SimulationResult:
    engine = context.engine(request.site_id, request.comfort_model_id)
    try:
        result = engine.simulate(request.design, request.to_options(),
                                 reference=request.reference,
                                 with_uncertainty=request.with_uncertainty,
                                 with_reference=request.with_reference)
    except Exception as error:
        raise _translate(error) from error
    context.store.save_run(result)
    return result


@router.post("/surface-results", response_model=SurfaceResults, tags=["design"])
def surface_results(request: SurfaceRequest,
                    context: Context = Depends(get_context)) -> SurfaceResults:
    engine = context.engine(request.site_id, request.comfort_model_id)
    options = request.to_options()
    try:
        result = engine.surface_results(request.design, options)
    except Exception as error:
        raise _translate(error) from error
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=("The simulation reported no per surface results for this period. "
                    "Widen the surface period or check that surface output is enabled."))
    return result


@router.post("/compare", response_model=ComparisonTable, tags=["design"])
def compare(request: CompareRequest,
            context: Context = Depends(get_context)) -> ComparisonTable:
    engine = context.engine(request.site_id, request.comfort_model_id)
    try:
        table = engine.compare(request.designs, request.to_options(),
                               reference=request.reference)
    except Exception as error:
        raise _translate(error) from error
    return table


@router.get("/runs", tags=["history"])
def list_runs(site_id: str | None = None, limit: int = Query(default=100, ge=1, le=1000),
              context: Context = Depends(get_context)) -> list[dict]:
    return [item.__dict__ for item in context.store.list_runs(site_id, limit)]


@router.get("/runs/{design_hash}", response_model=SimulationResult, tags=["history"])
def get_run(design_hash: str,
            context: Context = Depends(get_context)) -> SimulationResult:
    result = context.store.load_run(design_hash)
    if result is None:
        raise HTTPException(status_code=404,
                            detail=f"No stored run with hash {design_hash}")
    return result


@router.get("/runs/{design_hash}/surfaces", response_model=SurfaceResults,
            tags=["history"])
def get_run_surfaces(design_hash: str,
                     context: Context = Depends(get_context)) -> SurfaceResults:
    surfaces = context.store.load_surfaces(design_hash)
    if surfaces is None:
        raise HTTPException(
            status_code=404,
            detail=(f"Run {design_hash} has no stored per surface results. Re-run it "
                    "with surface output enabled."))
    return surfaces


@router.get("/climate", response_model=ClimateSummary, tags=["site"])
def climate(site_id: str | None = None,
            window_days: int = Query(default=7, ge=1, le=31),
            context: Context = Depends(get_context)) -> ClimateSummary:
    try:
        return context.engine(site_id).climate(window_days)
    except Exception as error:
        raise _translate(error) from error


@router.get("/sun-path", response_model=SunPath, tags=["site"])
def sun_path(site_id: str | None = None, day: str | None = None,
             step_minutes: int = Query(default=20, ge=1, le=120),
             context: Context = Depends(get_context)) -> SunPath:
    engine = context.engine(site_id)
    try:
        target = date_type.fromisoformat(day) if day else date_type(2019, 1, 15)
    except ValueError as error:
        raise HTTPException(status_code=422,
                            detail=f"day must be an ISO date: {error}") from error
    try:
        return engine.sun_path(target, step_minutes)
    except Exception as error:
        raise _translate(error) from error


@router.get("/terrain", response_model=TerrainMesh, tags=["site"])
def terrain(site_id: str | None = None,
            inner_radius_m: float = Query(default=45.0, gt=0.0, le=5000.0),
            outer_radius_m: float = Query(default=320.0, gt=0.0, le=20000.0),
            skyline_only: bool = Query(
                default=False,
                description="return the ring built from the horizon table instead "
                            "of the measured elevation model"),
            context: Context = Depends(get_context)) -> TerrainMesh:
    if inner_radius_m >= outer_radius_m:
        raise HTTPException(
            status_code=422,
            detail="inner_radius_m must be smaller than outer_radius_m")
    from shelter_engine.solar import terrain_mesh
    from shelter_engine.terrain import load_terrain_obj
    site = context.site(site_id)
    if not skyline_only:
        directory = Path(context.settings.data_dir) / "terrain"
        measured = load_terrain_obj(directory / f"{site.id}.obj", site)
        if measured is not None:
            return measured
    return terrain_mesh(site, inner_radius_m, outer_radius_m)


@router.post("/cfd", response_model=CFDFieldSet | CFDAbsent, tags=["cfd"])
def cfd_for_design(request: GeometryRequest,
                   context: Context = Depends(get_context)):
    return _cfd_payload(context, stable_hash(request.design))


@router.get("/cfd/{design_hash}", response_model=CFDFieldSet | CFDAbsent, tags=["cfd"])
def cfd(design_hash: str = PathParam(pattern=DESIGN_HASH_PATTERN),
        context: Context = Depends(get_context)):
    return _cfd_payload(context, design_hash)


@router.get("/cfd/{design_hash}/boundary-conditions", response_model=CFDBoundarySet,
            tags=["cfd"])
def cfd_boundary_conditions(design_hash: str = PathParam(pattern=DESIGN_HASH_PATTERN),
                            context: Context = Depends(get_context)) -> CFDBoundarySet:
    exports, rejected = _load_boundaries(Path(context.settings.cfd_dir), design_hash)
    if not exports:
        raise HTTPException(
            status_code=404,
            detail=(f"No fluid dynamics boundary conditions are exported for design "
                    f"{design_hash}." + (f" Unreadable: {'; '.join(rejected)}"
                                         if rejected else "")))
    return CFDBoundarySet(design_hash=design_hash, exports=list(exports.values()),
                          rejected=rejected)


def _load_boundaries(directory: Path, design_hash: str):
    exports = {}
    rejected: list[str] = []
    for path in find_boundary_conditions(directory, design_hash):
        try:
            export = load_cfd_export(path)
        except (ValidationError, ValueError, ResultsUnavailable) as error:
            rejected.append(f"{path.name} could not be read: {str(error)[:200]}")
            continue
        if export.design_hash != design_hash:
            rejected.append(f"{path.name} belongs to design {export.design_hash}")
            continue
        exports[label_of(path, design_hash)] = export
    return exports, rejected


def _cfd_payload(context: Context, design_hash: str):
    directory = Path(context.settings.cfd_dir)
    boundaries, rejected = _load_boundaries(directory, design_hash)
    fields = []
    for path in find_cfd_fields(directory, design_hash):
        try:
            field = load_cfd_field(path)
        except (ValidationError, ValueError, ResultsUnavailable) as error:
            rejected.append(f"{path.name} could not be read: {str(error)[:200]}")
            continue
        if field.design_hash != design_hash:
            rejected.append(f"{path.name} belongs to design {field.design_hash}")
            continue
        boundary = boundaries.get(field.label)
        if boundary is not None and stable_hash(boundary) != field.boundary_hash:
            rejected.append(
                f"{path.name} was solved on boundary conditions that have since changed; "
                "the field no longer describes this design and has to be solved again")
            continue
        if boundary is None:
            rejected.append(
                f"{path.name} has no matching boundary conditions, so the hour and the "
                "surface temperatures it was solved against cannot be confirmed")
            continue
        fields.append(field)
    labels = sorted(boundaries)
    if fields:
        return CFDFieldSet(design_hash=design_hash, fields=fields,
                           boundary_labels=labels, rejected=rejected)
    if labels:
        reason = (f"Boundary conditions for {', '.join(item.replace('_', ' ') for item in labels)} "
                  "are exported from EnergyPlus, but ANSYS Fluent has not been run on them "
                  "for this design yet. Until it has, there is no air temperature or "
                  "velocity field to draw; the engine computes one well mixed air "
                  "temperature per room and no velocity at all.")
    else:
        reason = ("Detailed analysis has not been run for this design. An air temperature "
                  "or velocity field can only come from a fluid dynamics solver, which "
                  "runs offline in Layer 1 for the recommended design only. The main "
                  "engine computes one well mixed air temperature per room and no "
                  "velocity at all, so there is nothing here to draw.")
    return CFDAbsent(design_hash=design_hash, reason=reason, boundary_labels=labels,
                     rejected=rejected)
