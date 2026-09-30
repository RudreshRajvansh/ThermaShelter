from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shelter_engine.errors import (ConfigurationError, EngineError, GeometryError,
                                   MaterialError, ResultsUnavailable,
                                   SimulationFailed, SimulationTimeout, WeatherError)

from .config import get_settings
from .deps import get_context
from .operate import Scheduler
from .routers import catalog, design, operate, search, sites

DESCRIPTION = """
HTTP surface over the passive shelter simulation engine.

Physics happens in the engine, which is a Python package with no idea the web exists.
This layer translates requests into engine calls and nothing else. If a temperature is
ever computed here or in the browser, something has gone wrong in the design.

Every response that carries numbers also carries the assumptions those numbers rest on,
the warnings the simulation produced, and the difference against a named reference
design. Read the warnings and show them.
"""

logger = logging.getLogger("shelter_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    context = get_context()
    for warning in context.startup_warnings:
        logger.warning("[%s] %s", warning.code, warning.message)
    if context.ready:
        logger.info("engine ready for sites: %s", sorted(context.sites))
    scheduler = None
    settings = get_settings()
    if settings.operate_at:
        scheduler = Scheduler(settings.operate_at,
                              lambda: operate.issue_all(context, settings.operate_lang))
        scheduler.start()
        logger.info("Commander Cards will be issued daily at %s", settings.operate_at)
    yield
    if scheduler is not None:
        scheduler.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        lifespan=lifespan,
        title="Passive shelter design API",
        description=DESCRIPTION,
        version="0.1.0",
        openapi_tags=[
            {"name": "meta", "description": "What is loaded and what is missing."},
            {"name": "catalogue",
             "description": "Materials, glazing, reference designs and comfort models."},
            {"name": "design",
             "description": "Geometry, simulation, per surface results and comparison."},
            {"name": "search", "description": "Multi objective design search."},
            {"name": "cfd",
             "description": "Pre computed fluid dynamics fields, absent for most designs."},
            {"name": "site",
             "description": "Solar geometry and terrain skyline for a location."},
            {"name": "validation", "description": "Evidence committed by Layer 1."},
            {"name": "history", "description": "Stored runs."},
            {"name": "projects", "description": "Saved projects."},
            {"name": "operate",
             "description": "Posts, tonight's forecast run and the Commander Card."},
        ])

    if settings.allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allow_origins),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"])

    app.include_router(catalog.router, prefix="/api")
    app.include_router(design.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(sites.router, prefix="/api")
    app.include_router(operate.router, prefix="/api")

    @app.exception_handler(SimulationTimeout)
    async def _timeout(request: Request, error: SimulationTimeout) -> JSONResponse:
        return JSONResponse(status_code=504, content={"detail": str(error)})

    @app.exception_handler(SimulationFailed)
    async def _failed(request: Request, error: SimulationFailed) -> JSONResponse:
        return JSONResponse(status_code=502, content={
            "detail": str(error), "severe": error.severe, "fatal": error.fatal})

    @app.exception_handler(ConfigurationError)
    async def _configuration(request: Request,
                             error: ConfigurationError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error)})

    @app.exception_handler(WeatherError)
    async def _weather(request: Request, error: WeatherError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error)})

    @app.exception_handler(ResultsUnavailable)
    async def _results(request: Request, error: ResultsUnavailable) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(error)})

    @app.exception_handler(GeometryError)
    async def _geometry(request: Request, error: GeometryError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(MaterialError)
    async def _material(request: Request, error: MaterialError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(EngineError)
    async def _engine(request: Request, error: EngineError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    return app


app = create_app()
