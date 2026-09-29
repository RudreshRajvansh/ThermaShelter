from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from shelter_engine.schema import SiteSpec

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


@dataclass
class Settings:
    designs_dir: Path | None = None
    data_dir: Path = field(default_factory=lambda: _path("SHELTER_DATA_DIR",
                                                         DEFAULT_DATA_DIR))
    database_path: Path | None = None
    cfd_dir: Path | None = None
    validation_dir: Path | None = None
    materials_path: Path | None = None
    sites_dir: Path | None = None
    default_site: str = field(
        default_factory=lambda: os.environ.get("SHELTER_DEFAULT_SITE", "leh"))
    comfort_model_id: str = field(
        default_factory=lambda: os.environ.get("SHELTER_COMFORT_MODEL",
                                               "drdo_tot_band"))
    fuel: str = field(default_factory=lambda: os.environ.get("SHELTER_FUEL", "kerosene"))
    allow_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            item.strip() for item in os.environ.get(
                "SHELTER_ALLOW_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173").split(",")
            if item.strip()))
    optimisation_workers: int = field(
        default_factory=lambda: int(os.environ.get("SHELTER_OPTIMISATION_WORKERS", "1")))

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.materials_path = _path("SHELTER_MATERIALS",
                                    self.materials_path
                                    or self.data_dir / "materials.db")
        self.sites_dir = _path("SHELTER_SITES", self.sites_dir
                               or self.data_dir / "sites")
        self.database_path = _path("SHELTER_DB", self.database_path
                                   or self.data_dir / "runs.db")
        self.cfd_dir = _path("SHELTER_CFD_DIR", self.cfd_dir
                             or self.data_dir / "cfd")
        self.designs_dir = Path(self.designs_dir
                                or self.data_dir / "designs")
        self.validation_dir = _path("SHELTER_VALIDATION_DIR",
                                    self.validation_dir
                                    or self.data_dir / "validation")

    def site_paths(self) -> dict[str, Path]:
        if not self.sites_dir.is_dir():
            return {}
        return {path.stem: path for path in sorted(self.sites_dir.glob("*.json"))}

    def load_sites(self) -> dict[str, SiteSpec]:
        sites: dict[str, SiteSpec] = {}
        for identifier, path in self.site_paths().items():
            payload = json.loads(path.read_text(encoding="utf-8"))
            site = SiteSpec.model_validate(payload)
            epw = Path(site.epw_path)
            if not epw.is_absolute():
                site = site.model_copy(update={
                    "epw_path": str((self.data_dir / epw).resolve())})
            sites[site.id or identifier] = site
        return sites


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
