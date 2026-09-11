from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from shelter_engine.errors import WeatherError
from shelter_engine.schema import SiteSpec
from shelter_engine.weather import load_epw_cached

from .. import sites as site_builder
from ..deps import Context, get_context
from ..schemas import PlaceResult, ResolveSiteRequest, SiteDetail, SiteProvenance
from .catalog import summarise_site

router = APIRouter()


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
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (ValueError, WeatherError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    site = _absolute(context, built.site)
    context.add_site(site)
    return _detail(site, built.provenance, built.notes, built.created)


@router.get("/sites/{site_id}", response_model=SiteDetail, tags=["site"])
def site_detail(site_id: str, context: Context = Depends(get_context)) -> SiteDetail:
    site = context.site(site_id)
    provenance, notes = site_builder.load_provenance(Path(context.settings.data_dir), site.id)
    if not notes and site.source is not None:
        notes = tuple(part for part in (site.source.citation, site.source.note) if part)
    return _detail(site, provenance, notes, False)
