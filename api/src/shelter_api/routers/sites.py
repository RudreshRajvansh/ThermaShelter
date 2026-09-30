from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from shelter_engine.errors import WeatherError
from shelter_engine.schema import EngineWarning, Severity, SiteSpec, SitingMap
from shelter_engine.weather import load_epw_cached

from .. import sites as site_builder
from ..deps import Context, get_context
from ..schemas import (DesignNightPayload, PlaceResult, RefereePayload,
                       ResolveSiteRequest, SiteDetail, SiteProvenance)
from .catalog import summarise_site

router = APIRouter()

MAX_SUBSTITUTE_KM = 250.0


def _absolute(context: Context, site: SiteSpec) -> SiteSpec:
    path = Path(site.epw_path)
    if path.is_absolute():
        return site
    return site.model_copy(update={
        "epw_path": str((Path(context.settings.data_dir) / path).resolve())})


def _detail(site: SiteSpec, provenance: dict | None, notes: tuple[str, ...] | list[str],
            created: bool) -> SiteDetail:
    try:
        warnings = list(load_epw_cached(site.epw_path, site).warnings)
    except WeatherError as error:
        raise HTTPException(
            status_code=502,
            detail=f"The weather file for {site.name} could not be read: {error}") from error
    return SiteDetail(
        site=site, summary=summarise_site(site),
        provenance=SiteProvenance.model_validate(provenance) if provenance else None,
        notes=list(notes), warnings=warnings, created=created)


@router.get("/places", response_model=list[PlaceResult], tags=["site"])
def places(query: str = Query(min_length=2, max_length=80),
           count: int = Query(default=8, ge=1, le=20)) -> list[PlaceResult]:
    try:
        found = site_builder.search_places(query.strip(), count)
    except site_builder.SiteUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return [PlaceResult.model_validate(item) for item in found]


@router.post("/sites/resolve", response_model=SiteDetail, tags=["site"])
def resolve_site(request: ResolveSiteRequest,
                 context: Context = Depends(get_context)) -> SiteDetail:
    try:
        built = site_builder.build_site(request.latitude_deg, request.longitude_deg,
                                        request.name, Path(context.settings.data_dir))
    except site_builder.SiteUnavailable as error:
        return _nearest_built(context, request, str(error))
    except (ValueError, WeatherError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    site = _absolute(context, built.site)
    context.add_site(site)
    return _detail(site, built.provenance, built.notes, built.created)


def _nearest_built(context: Context, request: ResolveSiteRequest,
                   reason: str) -> SiteDetail:
    from shelter_engine.site_data import great_circle_km

    candidates = list(context.sites.values())
    if not candidates:
        raise HTTPException(
            status_code=503,
            detail=(f"{reason} No site has been built here before either, so there is "
                    "nothing to fall back on."))

    def distance(site: SiteSpec) -> float:
        return great_circle_km(request.latitude_deg, request.longitude_deg,
                               site.latitude_deg, site.longitude_deg)

    nearest = min(candidates, key=distance)
    away = distance(nearest)
    if away > MAX_SUBSTITUTE_KM:
        raise HTTPException(
            status_code=503,
            detail=(f"{reason} The nearest site already built on this machine is "
                    f"{nearest.name}, {away:.0f} km away, too far to stand in for this "
                    "point."))
    site = _absolute(context, nearest)
    provenance, notes = site_builder.load_provenance(Path(context.settings.data_dir),
                                                     nearest.id)
    detail = _detail(site, provenance, notes, False)
    return detail.model_copy(update={
        "requested_latitude_deg": round(request.latitude_deg, 5),
        "requested_longitude_deg": round(request.longitude_deg, 5),
        "substituted_km": round(away, 1),
        "warnings": [EngineWarning(
            code="site_substituted",
            severity=Severity.SEVERE,
            origin="weather",
            message=(
                f"The point you clicked could not be built: {reason} "
                f"Everything below describes {nearest.name} instead, "
                f"{away:.0f} km away at {nearest.elevation_m:.0f} m, because that site "
                f"is already on this machine. Its weather, terrain horizon and ground "
                f"belong to that place, not to the point you clicked, so no result on "
                f"this site is a result for your point."),
        ), *detail.warnings],
    })


@router.get("/sites/{site_id}", response_model=SiteDetail, tags=["site"])
def site_detail(site_id: str, context: Context = Depends(get_context)) -> SiteDetail:
    site = context.site(site_id)
    provenance, notes = site_builder.load_provenance(Path(context.settings.data_dir), site.id)
    if not notes and site.source is not None:
        notes = tuple(part for part in (site.source.citation, site.source.note) if part)
    return _detail(site, provenance, notes, False)


@router.get("/sites/{site_id}/design-night", response_model=DesignNightPayload,
            tags=["site"])
def design_night(site_id: str,
                 context: Context = Depends(get_context)) -> DesignNightPayload:
    site = context.site(site_id)
    try:
        payload = site_builder.build_design_night(site, Path(context.settings.data_dir))
    except site_builder.SiteUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail=(f"The design night for {site.name} is not on this machine and could "
                    f"not be built: {error}")) from error
    return DesignNightPayload.model_validate(payload)


@router.get("/sites/{site_id}/siting-map", response_model=SitingMap, tags=["site"])
def siting(site_id: str,
           half_width_m: float = Query(default=1500.0, ge=250.0, le=4000.0),
           step_m: float = Query(default=250.0, ge=50.0, le=1000.0),
           context: Context = Depends(get_context)) -> SitingMap:
    site = context.site(site_id)
    try:
        return site_builder.build_siting_map(site, Path(context.settings.data_dir),
                                             half_width_m, step_m)
    except site_builder.SiteUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail=(f"The siting map for {site.name} could not be built: {error}")) \
            from error


@router.get("/sites/{site_id}/referee", response_model=RefereePayload, tags=["site"])
def weather_referee(site_id: str,
                    context: Context = Depends(get_context)) -> RefereePayload:
    site = _absolute(context, context.site(site_id))
    try:
        payload = site_builder.build_referee(site, Path(context.settings.data_dir))
    except WeatherError as error:
        raise HTTPException(status_code=422,
                            detail=f"The weather file could not be checked: {error}") \
            from error
    return RefereePayload.model_validate(payload)
