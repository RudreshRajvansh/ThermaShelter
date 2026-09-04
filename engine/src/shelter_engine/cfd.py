from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .errors import ResultsUnavailable
from .geometry import GeometryModel, ROOM, triangulate_fan
from .materials import MaterialLibrary
from .metrics import ZoneSeries
from .results import SimulationOutput
from .schema import (CFDBoundarySurface, CFDExport, DesignSpec, SurfaceKind)

INSIDE_FACE = "Surface Inside Face Temperature"
SOLAR_GAIN_CANDIDATES = (
    "Zone Windows Total Transmitted Solar Radiation Energy",
    "Enclosure Windows Total Transmitted Solar Radiation Energy",
)
DEFAULT_EMISSIVITY = 0.90

NOTES = (
    "Boundary conditions come from an EnergyPlus whole building run at the stated hour. "
    "Surface temperatures are inside face values; the zone air temperature is a single "
    "well mixed value and is provided as an initial condition, not as a target.",
    "EnergyPlus computes no air temperature field and no air velocity inside the room. "
    "Anything of that kind must come from this fluid dynamics run and must be labelled "
    "with the hour and the solver on screen.",
    "Coordinates are in metres in the same world frame as the display mesh, x east, "
    "y north, z up, with the building rotated to its real orientation.",
)


def select_hours(output: SimulationOutput, series: ZoneSeries,
                 zone: str = ROOM) -> dict[str, datetime]:
    hours: dict[str, datetime] = {}
    operative = series.operative_c.dropna()
    if operative.empty:
        raise ResultsUnavailable("no operative temperature available to pick CFD hours")
    hours["coldest_night"] = operative.idxmin().to_pydatetime()

    found = output.first_available(SOLAR_GAIN_CANDIDATES, key=zone)
    solar = found[1] if found is not None else None
    if solar is not None and not solar.dropna().empty:
        hourly = solar.resample("1h").mean().dropna()
        if not hourly.empty:
            hours["peak_solar"] = hourly.idxmax().to_pydatetime()
    if "peak_solar" not in hours:
        afternoon = operative[operative.index.hour.isin(range(13, 18))]
        pool = afternoon if not afternoon.empty else operative
        hours["peak_solar"] = pool.idxmax().to_pydatetime()
    return hours


def _surface_temperature(frame: pd.DataFrame, name: str,
                         moment: datetime) -> float | None:
    if frame.empty or name not in frame.columns:
        return None
    column = frame[name].dropna()
    if column.empty:
        return None
    position = column.index.get_indexer([pd.Timestamp(moment)], method="nearest")[0]
    if position < 0:
        return None
    return float(column.iloc[position])


def _value_at(series: pd.Series | None, moment: datetime) -> float | None:
    if series is None:
        return None
    clean = series.dropna()
    if clean.empty:
        return None
    position = clean.index.get_indexer([pd.Timestamp(moment)], method="nearest")[0]
    if position < 0:
        return None
    return float(clean.iloc[position])


def build_cfd_export(design: DesignSpec, model: GeometryModel,
                     output: SimulationOutput, series: ZoneSeries,
                     moment: datetime, label: str, design_hash: str,
                     library: MaterialLibrary | None = None,
                     zone: str = ROOM) -> CFDExport:
    inside = output.wide(INSIDE_FACE)
    if inside.empty:
        raise ResultsUnavailable(
            "surface inside face temperatures were not reported, so no fluid dynamics "
            "boundary condition can be exported; enable surface output on the run")
    inside = inside.resample("1h").mean()

    surfaces: list[CFDBoundarySurface] = []
    missing: list[str] = []
    for polygon in model.polygons:
        if polygon.zone != zone:
            continue
        temperature = _surface_temperature(inside, polygon.name, moment)
        if temperature is None:
            missing.append(polygon.name)
            continue
        positions: list[float] = []
        for vertex in polygon.vertices:
            positions.extend(float(value) for value in vertex)
        emissivity = DEFAULT_EMISSIVITY
        if library is not None and polygon.kind is not SurfaceKind.WINDOW:
            construction = {"wall": design.wall, "partition": design.partition
                            or design.wall, "roof": design.roof,
                            "ceiling": design.roof,
                            "floor": design.floor}.get(polygon.kind.value)
            if construction is not None:
                layers = library.layers(construction)
                if layers:
                    emissivity = layers[-1].material.thermal_absorptance
        surfaces.append(CFDBoundarySurface(
            name=polygon.name, kind=polygon.kind, facade=polygon.facade,
            positions=tuple(positions), indices=triangulate_fan(len(polygon.vertices)),
            area_m2=polygon.area_m2, inside_face_temperature_c=temperature,
            emissivity=emissivity))

    if not surfaces:
        raise ResultsUnavailable(
            f"no surface of zone {zone} had a reported inside face temperature at "
            f"{moment.isoformat()}")

    notes = list(NOTES)
    if missing:
        notes.append(
            f"{len(missing)} surfaces had no reported inside face temperature and were "
            f"omitted: {', '.join(sorted(missing)[:8])}")

    pressure = output.series("Site Outdoor Air Barometric Pressure", required=False)
    return CFDExport(
        design_hash=design_hash, zone=zone, label=label,
        timestamp=pd.Timestamp(moment).isoformat(),
        zone_air_temperature_c=_value_at(series.air_c, moment) or 0.0,
        zone_relative_humidity_percent=_value_at(series.relative_humidity_pct, moment),
        outdoor_air_temperature_c=_value_at(series.outdoor_c, moment),
        air_pressure_pa=_value_at(pressure, moment),
        zone_volume_m3=model.zone(zone).volume_m3,
        surfaces=tuple(surfaces), notes=tuple(notes),
        source_run=str(output.sql_path))


def write_cfd_export(export: CFDExport, directory: str | Path) -> Path:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{export.design_hash}_{export.label}.json"
    path.write_text(json.dumps(export.model_dump(mode="json"), indent=2),
                    encoding="utf-8")
    return path


def load_cfd_export(path: str | Path) -> CFDExport:
    source = Path(path)
    if not source.is_file():
        raise ResultsUnavailable(f"no fluid dynamics export at {source}")
    return CFDExport.model_validate(json.loads(source.read_text(encoding="utf-8")))


def find_cfd_field(directory: str | Path, design_hash: str) -> tuple[Path, ...]:
    target = Path(directory)
    if not target.is_dir():
        return ()
    return tuple(sorted(target.glob(f"{design_hash}_*.json")))
