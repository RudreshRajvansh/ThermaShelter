from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from .errors import ResultsUnavailable
from .geometry import GeometryModel, ROOM, triangulate_fan
from .hashing import stable_hash
from .materials import MaterialLibrary
from .metrics import ZoneSeries
from .results import SimulationOutput
from .schema import (CFDBoundarySurface, CFDExport, CFDField, CFDSolverRecord,
                     DesignSpec, SourceRef, SourceStatus, SurfaceKind)

INSIDE_FACE = "Surface Inside Face Temperature"
SOLAR_GAIN_CANDIDATES = (
    "Zone Windows Total Transmitted Solar Radiation Energy",
    "Enclosure Windows Total Transmitted Solar Radiation Energy",
)
CONVECTIVE_GAIN = "Zone Total Internal Convective Heating Rate"
INFILTRATION_GAIN = "Zone Infiltration Sensible Heat Gain Energy"
INFILTRATION_LOSS = "Zone Infiltration Sensible Heat Loss Energy"
MIXING_GAIN = "Zone Mixing Sensible Heat Gain Energy"
MIXING_LOSS = "Zone Mixing Sensible Heat Loss Energy"
SECONDS_PER_HOUR = 3600.0
DEFAULT_EMISSIVITY = 0.90
DEFAULT_FIELD_DIMS = (48, 24, 16)
GAP_FACTOR = 1.5

BOUNDARY_SUFFIX = ".boundary.json"
FIELD_SUFFIX = ".field.json"

NOTES = (
    "Boundary conditions come from an EnergyPlus whole building run at the stated hour. "
    "Surface temperatures are inside face values; the zone air temperature is a single "
    "well mixed value and is provided as an initial condition, not as a target.",
    "The internal convective gain, the infiltration sensible heat flow and the mixing "
    "sensible heat flow are the rates EnergyPlus reported for that hour. Applied as a "
    "volumetric source they close the same air heat balance EnergyPlus solved.",
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


def _net_rate_w(output: SimulationOutput, gain: str, loss: str, zone: str,
                moment: datetime) -> float | None:
    gained = _value_at(output.series(gain, zone, required=False), moment)
    lost = _value_at(output.series(loss, zone, required=False), moment)
    if gained is None and lost is None:
        return None
    return ((gained or 0.0) - (lost or 0.0)) / SECONDS_PER_HOUR


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

    convective = _value_at(output.series(CONVECTIVE_GAIN, zone, required=False), moment)
    if convective is None:
        notes.append("EnergyPlus reported no internal convective gain for this zone, so "
                     "none is carried; the fluid dynamics run must not invent one.")

    pressure = output.series("Site Outdoor Air Barometric Pressure", required=False)
    return CFDExport(
        design_hash=design_hash, zone=zone, label=label,
        timestamp=pd.Timestamp(moment).isoformat(),
        zone_air_temperature_c=_value_at(series.air_c, moment) or 0.0,
        zone_relative_humidity_percent=_value_at(series.relative_humidity_pct, moment),
        outdoor_air_temperature_c=_value_at(series.outdoor_c, moment),
        air_pressure_pa=_value_at(pressure, moment),
        zone_volume_m3=model.zone(zone).volume_m3,
        surfaces=tuple(surfaces),
        internal_convective_gain_w=convective,
        infiltration_sensible_w=_net_rate_w(output, INFILTRATION_GAIN,
                                            INFILTRATION_LOSS, zone, moment),
        mixing_sensible_w=_net_rate_w(output, MIXING_GAIN, MIXING_LOSS, zone, moment),
        notes=tuple(notes),
        source_run=str(output.sql_path))


def export_bounds(export: CFDExport) -> tuple[tuple[float, float, float],
                                               tuple[float, float, float]]:
    points = [surface.positions for surface in export.surfaces if surface.positions]
    if not points:
        raise ResultsUnavailable("the boundary conditions carry no surface geometry")
    array = np.concatenate([np.asarray(item, dtype=float).reshape(-1, 3)
                            for item in points])
    low = array.min(axis=0)
    high = array.max(axis=0)
    if np.any(high - low <= 0.0):
        raise ResultsUnavailable("the boundary surfaces do not enclose a volume")
    return (tuple(float(v) for v in low), tuple(float(v) for v in high))


def sample_to_grid(centres: Sequence[Sequence[float]] | np.ndarray,
                   temperature_c: Sequence[float] | np.ndarray,
                   bounds_min: Sequence[float], bounds_max: Sequence[float],
                   dims: Sequence[int],
                   speed_m_s: Sequence[float] | np.ndarray | None = None,
                   max_gap_m: float | None = None
                   ) -> tuple[tuple[float | None, ...], tuple[float | None, ...] | None]:
    from scipy.spatial import cKDTree

    points = np.asarray(centres, dtype=float)
    temperature = np.asarray(temperature_c, dtype=float).ravel()
    if points.ndim != 2 or points.shape[1] != 3:
        raise ResultsUnavailable("cell centres must be an N by 3 array of coordinates")
    if len(points) != len(temperature):
        raise ResultsUnavailable(
            f"{len(points)} cell centres were given with {len(temperature)} temperatures")
    speed = None
    if speed_m_s is not None:
        speed = np.asarray(speed_m_s, dtype=float).ravel()
        if len(speed) != len(points):
            raise ResultsUnavailable(
                f"{len(points)} cell centres were given with {len(speed)} speeds")
    grid = tuple(int(value) for value in dims)
    if len(grid) != 3 or min(grid) < 2:
        raise ResultsUnavailable("the sampling grid needs at least two points per axis")
    low = np.asarray(bounds_min, dtype=float)
    high = np.asarray(bounds_max, dtype=float)
    if low.shape != (3,) or high.shape != (3,) or np.any(high <= low) \
            or not (np.isfinite(low).all() and np.isfinite(high).all()):
        raise ResultsUnavailable("the sampling bounds must be finite with positive extent")

    keep = np.isfinite(points).all(axis=1) & np.isfinite(temperature)
    if speed is not None:
        keep &= np.isfinite(speed)
    points = points[keep]
    temperature = temperature[keep]
    if speed is not None:
        speed = speed[keep]
    if len(points) == 0:
        raise ResultsUnavailable("the solver returned no finite cell values")

    tree = cKDTree(points)
    if max_gap_m is None:
        if len(points) > 1:
            spacing, _ = tree.query(points, k=2)
            typical = float(np.median(spacing[:, 1]))
        else:
            typical = float(np.max(high - low))
        diagonal = float(np.max((high - low) / (np.asarray(grid) - 1)))
        max_gap_m = GAP_FACTOR * max(typical, 1e-9) + 0.5 * diagonal
    if not math.isfinite(max_gap_m) or max_gap_m <= 0.0:
        raise ResultsUnavailable("the maximum sampling gap must be positive")

    axes = [np.linspace(low[axis], high[axis], grid[axis]) for axis in range(3)]
    grid_z, grid_y, grid_x = np.meshgrid(axes[2], axes[1], axes[0], indexing="ij")
    targets = np.column_stack([grid_x.ravel(), grid_y.ravel(), grid_z.ravel()])
    distance, index = tree.query(targets, k=1)
    inside = distance <= max_gap_m

    temperatures = tuple(float(temperature[i]) if ok else None
                         for i, ok in zip(index, inside))
    speeds = None
    if speed is not None:
        speeds = tuple(float(speed[i]) if ok else None for i, ok in zip(index, inside))
    return temperatures, speeds


def build_cfd_field(export: CFDExport, centres: Sequence[Sequence[float]] | np.ndarray,
                    temperature_c: Sequence[float] | np.ndarray,
                    solver: CFDSolverRecord,
                    speed_m_s: Sequence[float] | np.ndarray | None = None,
                    dims: Sequence[int] = DEFAULT_FIELD_DIMS) -> CFDField:
    bounds_min, bounds_max = export_bounds(export)
    temperatures, speeds = sample_to_grid(centres, temperature_c, bounds_min,
                                          bounds_max, dims, speed_m_s)
    finite = [value for value in temperatures if value is not None]
    if not finite:
        raise ResultsUnavailable(
            "no grid point fell inside the solved air volume; the solver cells do not "
            "overlap the exported boundary geometry")
    moving = [value for value in speeds if value is not None] if speeds else []
    notes = [
        "Every grid point carries the value of the nearest solver cell. Grid points "
        "farther from any cell than the local cell spacing lie outside the air volume "
        "and are left empty rather than filled.",
        f"The surfaces were held at the inside face temperatures EnergyPlus reported for "
        f"{export.label.replace('_', ' ')} at {export.timestamp}.",
    ]
    if not solver.converged:
        notes.insert(0, "The solver did not meet its convergence criteria. Treat this "
                        "field as unconverged and do not quote numbers from it.")
    return CFDField(
        design_hash=export.design_hash, zone=export.zone, label=export.label,
        timestamp=export.timestamp, boundary_hash=stable_hash(export),
        dims=tuple(int(value) for value in dims),
        bounds_min=bounds_min, bounds_max=bounds_max,
        temperature_c=temperatures, speed_m_s=speeds,
        min_c=min(finite), max_c=max(finite), mean_c=float(np.mean(finite)),
        max_speed_m_s=max(moving) if moving else None,
        energyplus_zone_air_c=export.zone_air_temperature_c,
        solver=solver, notes=tuple(notes),
        source=SourceRef(
            citation=(f"{solver.solver} {solver.version}, {solver.turbulence_model}, "
                      f"{solver.cell_count:,} cells, run on the boundary conditions "
                      f"EnergyPlus exported for {export.label.replace('_', ' ')}"),
            status=SourceStatus.DERIVED))


def _write(payload, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload.model_dump(mode="json"), indent=2),
                    encoding="utf-8")
    return path


def write_cfd_export(export: CFDExport, directory: str | Path) -> Path:
    return _write(export, Path(directory)
                  / f"{export.design_hash}_{export.label}{BOUNDARY_SUFFIX}")


def write_cfd_field(field: CFDField, directory: str | Path) -> Path:
    return _write(field, Path(directory)
                  / f"{field.design_hash}_{field.label}{FIELD_SUFFIX}")


def load_cfd_export(path: str | Path) -> CFDExport:
    source = Path(path)
    if not source.is_file():
        raise ResultsUnavailable(f"no fluid dynamics boundary conditions at {source}")
    return CFDExport.model_validate(json.loads(source.read_text(encoding="utf-8")))


def load_cfd_field(path: str | Path) -> CFDField:
    source = Path(path)
    if not source.is_file():
        raise ResultsUnavailable(f"no solved fluid dynamics field at {source}")
    return CFDField.model_validate(json.loads(source.read_text(encoding="utf-8")))


def _find(directory: str | Path, design_hash: str, suffix: str) -> tuple[Path, ...]:
    target = Path(directory)
    if not design_hash or not design_hash.isalnum() or not target.is_dir():
        return ()
    prefix = f"{design_hash}_"
    return tuple(sorted(path for path in target.iterdir()
                        if path.is_file() and path.name.startswith(prefix)
                        and path.name.endswith(suffix)
                        and len(path.name) > len(prefix) + len(suffix)))


def find_boundary_conditions(directory: str | Path,
                             design_hash: str) -> tuple[Path, ...]:
    return _find(directory, design_hash, BOUNDARY_SUFFIX)


def find_cfd_fields(directory: str | Path, design_hash: str) -> tuple[Path, ...]:
    return _find(directory, design_hash, FIELD_SUFFIX)


def label_of(path: str | Path, design_hash: str) -> str:
    name = Path(path).name
    for suffix in (FIELD_SUFFIX, BOUNDARY_SUFFIX):
        if name.endswith(suffix):
            return name[len(design_hash) + 1:-len(suffix)]
    raise ResultsUnavailable(f"{name} is not a fluid dynamics file")
