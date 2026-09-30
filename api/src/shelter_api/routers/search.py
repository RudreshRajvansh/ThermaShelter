from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from shelter_engine.schema import OptimisationSpec

from ..deps import Context, get_context
from ..schemas import (LeverPayload, LeverRequest, OptimisationJob, OptimiseRequest,
                       ProjectRequest)

router = APIRouter()

_running = threading.Semaphore(2)
MAX_DEFAULT_WORKERS = 8


MEMORY_PER_WORKER = 700 * 1024 * 1024
CGROUP = Path("/sys/fs/cgroup")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def container_cpus() -> float | None:
    quota = _read(CGROUP / "cpu.max")
    if quota:
        limit, _, period = quota.partition(" ")
        if limit != "max" and period:
            return int(limit) / int(period)
    old_quota = _read(CGROUP / "cpu" / "cpu.cfs_quota_us")
    old_period = _read(CGROUP / "cpu" / "cpu.cfs_period_us")
    if old_quota and old_period and int(old_quota) > 0:
        return int(old_quota) / int(old_period)
    return None


def container_memory() -> int | None:
    for path in (CGROUP / "memory.max", CGROUP / "memory" / "memory.limit_in_bytes"):
        value = _read(path)
        if value and value != "max" and int(value) < 1 << 50:
            return int(value)
    return None


def default_workers() -> int:
    cpus = container_cpus() or float(os.cpu_count() or 2)
    workers = min(MAX_DEFAULT_WORKERS, int(cpus) - 1)
    memory = container_memory()
    if memory is not None:
        workers = min(workers, memory // MEMORY_PER_WORKER - 1)
    return max(1, workers)


def _run_optimisation(context: Context, job_id: str, site_id: str,
                      spec: OptimisationSpec, options) -> None:
    acquired = _running.acquire(blocking=True, timeout=1800)
    if not acquired:
        context.store.finish_optimisation(
            job_id, None,
            message=("The optimisation queue was busy for thirty minutes and this job "
                     "was dropped. Optimisation is a batch job; run it before the "
                     "demonstration and store the front."))
        return
    try:
        engine = context.engine(site_id)

        def publish(stage) -> None:
            context.store.publish_optimisation_stage(
                job_id, stage.model_dump(mode="json"), stage.evaluated)

        result = engine.optimise(spec, options, on_stage=publish)
        context.store.finish_optimisation(
            job_id, result.model_dump(mode="json"),
            message=None, evaluated=result.evaluated)
    except Exception as error:
        context.store.finish_optimisation(
            job_id, None, message=f"{type(error).__name__}: {error}")
    finally:
        _running.release()


@router.post("/search-levers", response_model=list[LeverPayload], tags=["search"])
def levers(payload: LeverRequest,
           context: Context = Depends(get_context)) -> list[LeverPayload]:
    from shelter_engine.optimize import search_levers
    from shelter_engine.schema import ClimateObjective

    site = context.site(payload.site_id)
    if context.library is None:
        raise HTTPException(status_code=503, detail="The material database is not loaded.")
    cold = site.climate_objective is not ClimateObjective.COOLING_DOMINATED
    return [LeverPayload(variable=lever.variable, label=lever.label, group=lever.group)
            for lever in search_levers(payload.design, context.library, cold)]


@router.post("/optimise", response_model=OptimisationJob, tags=["search"])
def start_optimisation(payload: OptimiseRequest, background: BackgroundTasks,
                       context: Context = Depends(get_context)) -> OptimisationJob:
    site = context.site(payload.site_id)
    context.engine(payload.site_id)
    try:
        spec = OptimisationSpec(
            base_design=payload.base_design,
            variables=tuple(payload.variables),
            constraints=payload.constraints,
            objectives=tuple(payload.objectives),
            population_size=payload.population_size,
            generations=payload.generations,
            seed=payload.seed,
            workers=payload.workers or default_workers(),
            robust_top=payload.robust_top, robust_keep=payload.robust_keep)
    except Exception as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    job_id = uuid.uuid4().hex[:16]
    context.store.create_optimisation(job_id, site.id, spec.model_dump(mode="json"))
    background.add_task(_run_optimisation, context, job_id, site.id, spec,
                        payload.to_options())
    stored = context.store.load_optimisation(job_id)
    return OptimisationJob.model_validate(stored)


@router.get("/optimise/{job_id}", response_model=OptimisationJob, tags=["search"])
def optimisation_status(job_id: str,
                        context: Context = Depends(get_context)) -> OptimisationJob:
    stored = context.store.load_optimisation(job_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"No optimisation job {job_id}")
    return OptimisationJob.model_validate(stored)


@router.get("/optimise", tags=["search"])
def list_optimisations(limit: int = Query(default=50, ge=1, le=500),
                       context: Context = Depends(get_context)) -> list[dict]:
    return context.store.list_optimisations(limit)


@router.get("/projects", tags=["projects"])
def list_projects(context: Context = Depends(get_context)) -> list[dict]:
    return context.store.list_projects()


@router.put("/projects/{identifier}", tags=["projects"])
def save_project(identifier: str, request: ProjectRequest,
                 context: Context = Depends(get_context)) -> dict:
    if identifier != request.id:
        raise HTTPException(
            status_code=422,
            detail=f"path id {identifier!r} does not match body id {request.id!r}")
    site = context.site(request.site_id)
    context.store.save_project(request.id, request.name, site.id,
                               request.model_dump(mode="json"))
    stored = context.store.load_project(request.id)
    if stored is None:
        raise HTTPException(status_code=500, detail="the project failed to save")
    return stored


@router.get("/projects/{identifier}", tags=["projects"])
def get_project(identifier: str, context: Context = Depends(get_context)) -> dict:
    stored = context.store.load_project(identifier)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"No project {identifier}")
    return stored


@router.delete("/projects/{identifier}", tags=["projects"])
def delete_project(identifier: str,
                   context: Context = Depends(get_context)) -> dict:
    if not context.store.delete_project(identifier):
        raise HTTPException(status_code=404, detail=f"No project {identifier}")
    return {"deleted": identifier}
