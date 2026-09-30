from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from fastapi import Query
from fastapi.responses import Response
from pydantic import ValidationError

from shelter_engine.cfd import (find_boundary_conditions, find_cfd_fields, label_of,
                                load_cfd_export, load_cfd_field)
from shelter_engine.errors import (EngineError, GeometryError, MaterialError,
                                   ResultsUnavailable, SimulationFailed,
                                   SimulationTimeout)
from shelter_engine.constructions import build_constructions
from shelter_engine.compliance import eco_niwas_sheet
from shelter_engine.costing import bill_of_quantities
from shelter_engine.design_pack import PackInputs, build_design_pack
from shelter_engine.evidence import read_manifest, simulation_evidence, zip_folder
from shelter_engine.geometry import build_geometry
from shelter_engine.hashing import stable_hash
from shelter_engine.mapdl import find_reports, load_report
from shelter_engine import __version__ as engine_version
from shelter_engine.schema import (BillOfQuantities, ClimateSummary, ComparisonTable,
                                   ComplianceSheet, GeometryPayload, RunOptions,
                                   SimulationResult, SunPath, SurfaceResults,
                                   TerrainMesh)

from ..deps import Context, get_context
from ..schemas import (CFDAbsent, CFDBoundarySet, CFDFieldSet, CompareRequest,
                       EndurancePayload, EnduranceRequest, GeometryRequest,
                       HorizonCase, HorizonValuePayload, LeftoverHeatPayload,
                       MapdlAbsent, MapdlSet, DesignPackRequest,
                       SimulateRequest, SurfaceRequest)

router = APIRouter()

DESIGN_HASH_PATTERN = r"^[0-9a-f]{32}$"
LOGGER = logging.getLogger(__name__)


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


@router.post("/bill-of-quantities", response_model=BillOfQuantities, tags=["design"])
def quantities(request: GeometryRequest,
               context: Context = Depends(get_context)) -> BillOfQuantities:
    site = context.site(request.site_id)
    if context.library is None:
        raise HTTPException(status_code=503, detail=(
            "The material database is not loaded, so there is nothing to weigh. Expected "
            f"it at {context.settings.materials_path}."))
    try:
        constructions = build_constructions(request.design, context.library)
        model = build_geometry(request.design, constructions.names,
                               design_hash=stable_hash(request.design), horizon=site.horizon)
        return bill_of_quantities(request.design, model, context.library)
    except Exception as error:
        raise _translate(error) from error


@router.post("/bye-law-sheet", response_model=ComplianceSheet, tags=["design"])
def bye_law_sheet(request: GeometryRequest,
                  context: Context = Depends(get_context)) -> ComplianceSheet:
    site = context.site(request.site_id)
    if context.library is None:
        raise HTTPException(status_code=503, detail=(
            "The material database is not loaded, so no U-value can be computed. Expected "
            f"it at {context.settings.materials_path}."))
    try:
        constructions = build_constructions(request.design, context.library)
        model = build_geometry(request.design, constructions.names,
                               design_hash=stable_hash(request.design), horizon=site.horizon)
        return eco_niwas_sheet(request.design, model, context.library, site)
    except Exception as error:
        raise _translate(error) from error


@router.post("/design-pack", tags=["design"],
             responses={200: {"content": {"application/zip": {}}}})
def design_pack(request: DesignPackRequest,
                context: Context = Depends(get_context)) -> Response:
    site = context.site(request.site_id)
    if context.library is None:
        raise HTTPException(status_code=503, detail=(
            "The material database is not loaded, so the pack cannot be built. Expected "
            f"it at {context.settings.materials_path}."))
    design = request.design
    fingerprint = stable_hash(design)
    notes: list[str] = []
    idf_text = None
    weather = None
    try:
        engine = context.engine(request.site_id)
        options = RunOptions(period=request.period.to_period(), conditioned_run=True,
                             surface_output=False)
        idf_text = engine.idf_text(design, options)
        weather = Path(site.epw_path)
    except HTTPException as error:
        notes.append(f"No EnergyPlus input file is included: {error.detail}")
    extra: dict[str, bytes] = {}
    for path in find_reports(context.settings.mapdl_dir, fingerprint):
        extra[f"ansys/mapdl/{path.name}"] = path.read_bytes()
    cfd_dir = Path(context.settings.cfd_dir)
    for path in find_boundary_conditions(cfd_dir, fingerprint) + find_cfd_fields(
            cfd_dir, fingerprint):
        extra[f"ansys/fluent/{path.name}"] = path.read_bytes()
    if not any(name.startswith("ansys/") for name in extra):
        notes.append("No ANSYS run is on record for this design, so the pack holds no "
                     "MAPDL or Fluent files.")
    if request.run_hash:
        stored = context.store.load_run(request.run_hash, with_surfaces=False)
        if stored is None or stored.design_fingerprint != fingerprint:
            notes.append("The simulation named in the request is not stored for this "
                         "design, so its result is not included.")
        else:
            extra["evidence/simulation_result.json"] = stored.model_dump_json(
                indent=2).encode("utf-8")
    try:
        model = build_geometry(design, build_constructions(design, context.library).names,
                               design_hash=fingerprint, horizon=site.horizon)
        payload = build_design_pack(PackInputs(
            design=design, site=site, model=model, library=context.library,
            idf_text=idf_text, weather_path=weather, extra_files=extra, notes=notes))
    except Exception as error:
        raise _translate(error) from error
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in design.name)
    return Response(content=payload, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}_design_pack.zip"'})


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
    try:
        simulation_evidence(Path(context.settings.evidence_dir), result,
                            engine.idf_text(request.design, request.to_options(),
                                            request.conditioned_run),
                            Path(engine.site.epw_path))
    except (OSError, EngineError) as error:
        LOGGER.warning("the evidence folder for run %s was not written: %s",
                       result.design_hash, error)
    return result


@router.get("/evidence/{run_hash}", tags=["design"])
def evidence(run_hash: str = PathParam(pattern=DESIGN_HASH_PATTERN),
             context: Context = Depends(get_context)) -> dict:
    folder = Path(context.settings.evidence_dir) / run_hash
    try:
        return read_manifest(folder)
    except EngineError as error:
        raise HTTPException(status_code=404, detail=(
            "No evidence folder is on record for this run. One is written whenever a "
            "simulation is stored, and by scripts/run_mapdl.py for ANSYS runs.")) from error


@router.get("/evidence/{run_hash}/zip", tags=["design"],
            responses={200: {"content": {"application/zip": {}}}})
def evidence_zip(run_hash: str = PathParam(pattern=DESIGN_HASH_PATTERN),
                 context: Context = Depends(get_context)) -> Response:
    folder = Path(context.settings.evidence_dir) / run_hash
    try:
        payload = zip_folder(folder)
    except EngineError as error:
        raise HTTPException(status_code=404, detail=(
            "No evidence folder is on record for this run.")) from error
    return Response(content=payload, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="evidence_{run_hash}.zip"'})


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


@router.post("/mapdl", response_model=MapdlSet | MapdlAbsent, tags=["ansys"])
def mapdl_for_design(request: GeometryRequest,
                     context: Context = Depends(get_context)) -> MapdlSet | MapdlAbsent:
    return _mapdl_payload(context, stable_hash(request.design), request.site_id)


@router.get("/mapdl/{design_hash}", response_model=MapdlSet | MapdlAbsent, tags=["ansys"])
def mapdl(design_hash: str = PathParam(pattern=DESIGN_HASH_PATTERN),
          site_id: str | None = None,
          context: Context = Depends(get_context)) -> MapdlSet | MapdlAbsent:
    return _mapdl_payload(context, design_hash, site_id)


def _mapdl_payload(context: Context, design_hash: str,
                   site_id: str | None) -> MapdlSet | MapdlAbsent:
    site = context.site(site_id)
    reports = []
    rejected = []
    for path in find_reports(context.settings.mapdl_dir, design_hash):
        try:
            report = load_report(path)
        except (ValidationError, ValueError, EngineError) as error:
            rejected.append(f"{path.name} could not be read: {str(error)[:200]}")
            continue
        if report.design_hash != design_hash:
            rejected.append(f"{path.name} belongs to design {report.design_hash}")
            continue
        if report.site_id != site.id:
            rejected.append(f"{path.name} was solved for {report.site_id}, not {site.id}")
            continue
        if report.engine_version != engine_version:
            rejected.append(
                f"{path.name} compares MAPDL with EnergyPlus as engine "
                f"{report.engine_version} modelled it; this is engine {engine_version}, "
                "so the comparison has to be run again")
            continue
        reports.append(report)
    if reports:
        return MapdlSet(design_hash=design_hash, reports=reports, rejected=rejected)
    return MapdlAbsent(
        design_hash=design_hash, rejected=rejected,
        reason=("ANSYS Mechanical APDL has not been run on this design. It solves the walls, "
                "roof, floor and the ground under them as a 3D transient finite element "
                "model on the loads EnergyPlus computed, and runs offline with "
                "scripts/run_mapdl.py. Until it has, the envelope rests on EnergyPlus "
                "alone and there is no independent check to show."))


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
