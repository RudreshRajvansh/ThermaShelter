from __future__ import annotations

import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from shelter_engine.baseline import ReferenceCatalogue, load_references
from shelter_engine.comfort import ComfortCatalogue, load_catalogue
from shelter_engine.config import energyplus_available
from shelter_engine.engine import Engine
from shelter_engine.errors import EngineError, MaterialError, WeatherError
from shelter_engine.materials import MaterialLibrary
from shelter_engine.schema import EngineWarning, Severity, SiteSpec

from .config import Settings, get_settings
from .store import Store


@dataclass
class Context:
    settings: Settings
    store: Store
    sites: dict[str, SiteSpec]
    library: MaterialLibrary | None
    references: ReferenceCatalogue
    catalogue: ComfortCatalogue
    startup_warnings: list[EngineWarning] = field(default_factory=list)
    _engines: dict[tuple[str, str], Engine] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def ready(self) -> bool:
        return bool(self.sites) and self.library is not None and energyplus_available()

    def default_site_id(self) -> str:
        if self.settings.default_site in self.sites:
            return self.settings.default_site
        if self.sites:
            return next(iter(self.sites))
        raise HTTPException(
            status_code=503,
            detail=("No site records are loaded. Layer 0 supplies these as JSON files "
                    f"in {self.settings.sites_dir}. Run scripts/build_data.py to "
                    "generate development inputs."))

    def site(self, site_id: str | None) -> SiteSpec:
        identifier = site_id or self.default_site_id()
        try:
            return self.sites[identifier]
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=(f"Unknown site {identifier!r}. Loaded sites: "
                        f"{sorted(self.sites)}")) from None

    def engine(self, site_id: str | None = None,
               comfort_model_id: str | None = None) -> Engine:
        site = self.site(site_id)
        if self.library is None:
            raise HTTPException(
                status_code=503,
                detail=("The material database is not loaded, so nothing can be "
                        f"simulated. Expected it at {self.settings.materials_path}."))
        if not energyplus_available():
            raise HTTPException(
                status_code=503,
                detail=("EnergyPlus was not found. Set ENERGYPLUS_DIR or put "
                        "energyplus on PATH. Physics happens in EnergyPlus and the "
                        "API will not fabricate a result without it."))
        model_id = comfort_model_id or self.settings.comfort_model_id
        if self.catalogue is not None:
            known = {model.id for model in self.catalogue.adaptive}
            if known and model_id not in known:
                raise HTTPException(
                    status_code=422,
                    detail=(f"There is no comfort model called {model_id!r}. "
                            f"The catalogue holds {', '.join(sorted(known))}."))
        key = (site.id, model_id)
        with self._lock:
            existing = self._engines.get(key)
            if existing is not None:
                return existing
            try:
                engine = Engine(site, self.library,
                                comfort_model_id=model_id,
                                fuel=self.settings.fuel,
                                references=self.references,
                                catalogue=self.catalogue,
                                cfd_directory=self.settings.cfd_dir)
            except (EngineError, WeatherError) as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self._engines[key] = engine
            return engine

    def add_site(self, site: SiteSpec) -> None:
        with self._lock:
            self.sites[site.id] = site
            for key in [key for key in self._engines if key[0] == site.id]:
                del self._engines[key]


def _warning(code: str, message: str,
             severity: Severity = Severity.SEVERE) -> EngineWarning:
    return EngineWarning(code=code, severity=severity, message=message,
                         origin="engine")


@lru_cache(maxsize=1)
def get_context() -> Context:
    return build_context(get_settings())


def build_context(settings: Settings) -> Context:
    warnings: list[EngineWarning] = []

    library: MaterialLibrary | None = None
    materials_path = Path(settings.materials_path)
    if materials_path.is_file():
        try:
            library = MaterialLibrary.load(materials_path)
        except MaterialError as error:
            warnings.append(_warning("materials_unreadable", str(error)))
    else:
        warnings.append(_warning(
            "materials_missing",
            f"No material database at {materials_path}. Layer 0 owns this file; "
            "run scripts/build_data.py to generate development inputs."))

    sites: dict[str, SiteSpec] = {}
    try:
        sites = settings.load_sites()
    except Exception as error:
        warnings.append(_warning("sites_unreadable",
                                 f"Could not read site records: {error}"))
    if not sites:
        warnings.append(_warning(
            "sites_missing",
            f"No site records in {settings.sites_dir}."))

    if not energyplus_available():
        warnings.append(_warning(
            "energyplus_missing",
            "EnergyPlus was not found. Set ENERGYPLUS_DIR or put energyplus on PATH.",
            Severity.FATAL))

    return Context(settings=settings, store=Store(settings.database_path),
                   sites=sites, library=library, references=load_references(),
                   catalogue=load_catalogue(), startup_warnings=warnings)
