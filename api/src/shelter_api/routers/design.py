from __future__ import annotations

from pathlib import Path

from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query

from shelter_engine.cfd import find_cfd_field, load_cfd_export
from shelter_engine.errors import (EngineError, GeometryError, MaterialError,
                                   SimulationFailed, SimulationTimeout)
from shelter_engine.hashing import stable_hash
from shelter_engine.schema import (CFDExport, ClimateSummary, ComparisonTable,
                                   GeometryPayload, SimulationResult, SunPath,
                                   SurfaceResults, TerrainMesh)

from ..deps import Context, get_context
from ..schemas import (CFDAbsent, CompareRequest, GeometryRequest,
                       SimulateRequest, SurfaceRequest)

router = APIRouter()


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
    engine = context.engine(request.site_id)
    try:
        return engine.geometry(request.design)
    except Exception as error:
        raise _translate(error) from error


@router.post("/simulate", response_model=SimulationResult, tags=["design"])
def simulate(request: SimulateRequest,
             context: Context = Depends(get_context)) -> SimulationResult:
    engine = context.engine(request.site_id)
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
    engine = context.engine(request.site_id)
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
    engine = context.engine(request.site_id)
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
            context: Context = Depends(get_context)) -> TerrainMesh:
    if inner_radius_m >= outer_radius_m:
        raise HTTPException(
            status_code=422,
            detail="inner_radius_m must be smaller than outer_radius_m")
    from shelter_engine.solar import terrain_mesh
    return terrain_mesh(context.site(site_id), inner_radius_m, outer_radius_m)


@router.post("/cfd", response_model=CFDExport | CFDAbsent, tags=["cfd"])
def cfd_for_design(request: GeometryRequest,
                   context: Context = Depends(get_context)):
    design_hash = stable_hash(request.design)
    return _cfd_payload(context, design_hash)


@router.get("/cfd/{design_hash}", response_model=CFDExport | CFDAbsent, tags=["cfd"])
def cfd(design_hash: str, context: Context = Depends(get_context)):
    return _cfd_payload(context, design_hash)


def _cfd_payload(context: Context, design_hash: str):
    directory = Path(context.settings.cfd_dir)
    matches = find_cfd_field(directory, design_hash)
    if not matches:
        return CFDAbsent(
            design_hash=design_hash,
            reason=("Detailed analysis has not been run for this design. An air "
                    "temperature or velocity field can only come from a fluid dynamics "
                    "solver, which runs offline in Layer 1 for the recommended design "
                    "only. The main engine computes one well mixed air temperature per "
                    "room and no velocity at all, so there is nothing here to draw."))
    return load_cfd_export(matches[0])
