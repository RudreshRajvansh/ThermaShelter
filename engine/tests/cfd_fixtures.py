from __future__ import annotations

import numpy as np

from shelter_engine.schema import (CFDBoundarySurface, CFDExport, CFDSolverRecord,
                                   SurfaceKind)

DESIGN_HASH = "0123456789abcdef0123456789abcdef"
WIDTH_M = 4.0
DEPTH_M = 3.0
HEIGHT_M = 2.5


def _rectangle(name: str, kind: SurfaceKind, z: float,
               temperature_c: float) -> CFDBoundarySurface:
    corners = [(0.0, 0.0, z), (WIDTH_M, 0.0, z), (WIDTH_M, DEPTH_M, z), (0.0, DEPTH_M, z)]
    return CFDBoundarySurface(
        name=name, kind=kind, positions=tuple(value for corner in corners
                                              for value in corner),
        indices=(0, 1, 2, 0, 2, 3), area_m2=WIDTH_M * DEPTH_M,
        inside_face_temperature_c=temperature_c)


def box_export(design_hash: str = DESIGN_HASH, label: str = "coldest_night",
               air_c: float = 5.0, flat: bool = False) -> CFDExport:
    surfaces = [_rectangle("ROOM_FLOOR", SurfaceKind.FLOOR, 0.0, 2.0)]
    surfaces.append(_rectangle("ROOM_ROOF", SurfaceKind.ROOF,
                               0.0 if flat else HEIGHT_M, 6.0))
    return CFDExport(
        design_hash=design_hash, zone="ROOM", label=label,
        timestamp="2019-01-12T06:00:00", zone_air_temperature_c=air_c,
        outdoor_air_temperature_c=-18.0, air_pressure_pa=67200.0,
        zone_volume_m3=WIDTH_M * DEPTH_M * HEIGHT_M, surfaces=tuple(surfaces),
        internal_convective_gain_w=400.0, infiltration_sensible_w=-900.0)


def solver_record(converged: bool = True) -> CFDSolverRecord:
    return CFDSolverRecord(
        solver="ANSYS Fluent", version="2026 R1", turbulence_model="k-omega SST",
        buoyancy_model="Boussinesq", radiation_model="none",
        operating_pressure_pa=67200.0, cell_count=480, iterations=400,
        converged=converged, residuals={"energy": 1.0e-7})


def cell_cloud(size_m: float = 0.25) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xs = np.arange(size_m / 2.0, WIDTH_M, size_m)
    ys = np.arange(size_m / 2.0, DEPTH_M, size_m)
    zs = np.arange(size_m / 2.0, HEIGHT_M, size_m)
    grid_z, grid_y, grid_x = np.meshgrid(zs, ys, xs, indexing="ij")
    centres = np.column_stack([grid_x.ravel(), grid_y.ravel(), grid_z.ravel()])
    temperature = 2.0 + 1.6 * centres[:, 2]
    speed = 0.05 * centres[:, 0]
    return centres, temperature, speed
