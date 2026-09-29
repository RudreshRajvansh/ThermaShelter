from __future__ import annotations

from datetime import date as date_type
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
                       GeometryRequest, SimulateRequest, SurfaceRequest)

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
