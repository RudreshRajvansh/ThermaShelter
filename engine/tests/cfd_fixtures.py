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


ROOM_W_M = 6.0
ROOM_D_M = 4.0
ROOM_H_M = 2.6
WINDOW = (2.0, 4.0, 0.9, 2.0)


def closed_room_export(rotation_deg: float = 0.0, offset: tuple[float, float] = (10.0, -5.0),
                       drop: str | None = None, bend: bool = False) -> CFDExport:
    angle = np.radians(rotation_deg)
    cos, sin = np.cos(angle), np.sin(angle)

    def place(corners):
        values = []
        for x, y, z in corners:
            values.extend((cos * x - sin * y + offset[0], sin * x + cos * y + offset[1], z))
        return tuple(values)

    w, d, h = ROOM_W_M, ROOM_D_M, ROOM_H_M
    x0, x1, z0, z1 = WINDOW
    rows = [
        ("ROOM_FLOOR", SurfaceKind.FLOOR, [(0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0)], 3.0),
        ("ROOM_ROOF", SurfaceKind.ROOF, [(0, d, h), (w, d, h), (w, 0, h), (0, 0, h)], 2.0),
        ("ROOM_WALL_SOUTH", SurfaceKind.WALL, [(0, 0, h), (0, 0, 0), (w, 0, 0), (w, 0, h)], 4.0),
        ("ROOM_WALL_NORTH", SurfaceKind.WALL, [(w, d, h), (w, d, 0), (0, d, 0), (0, d, h)], 3.5),
        ("ROOM_WALL_EAST", SurfaceKind.WALL, [(w, 0, h), (w, 0, 0), (w, d, 0), (w, d, h)], 3.6),
        ("ROOM_WALL_WEST", SurfaceKind.WALL,
         [(0, d, h), (0, d, 0), (0, 0, 0), (0.4 if bend else 0, 0, h)], 3.7),
        ("ROOM_WALL_SOUTH_WIN", SurfaceKind.WINDOW,
         [(x0, 0, z1), (x0, 0, z0), (x1, 0, z0), (x1, 0, z1)], -1.5),
    ]
    surfaces = []
    for name, kind, corners, temperature in rows:
        if name == drop:
            continue
        points = np.asarray(corners, dtype=float)
        area = float(np.linalg.norm(np.cross(points[1] - points[0], points[3] - points[0])))
        surfaces.append(CFDBoundarySurface(
            name=name, kind=kind, positions=place(corners), indices=(0, 1, 2, 0, 2, 3),
            area_m2=area, inside_face_temperature_c=temperature))
    return CFDExport(
        design_hash=DESIGN_HASH, zone="ROOM", label="coldest_night",
        timestamp="2019-01-12T06:00:00", zone_air_temperature_c=4.0,
        outdoor_air_temperature_c=-18.0, air_pressure_pa=67200.0,
        zone_volume_m3=w * d * h, surfaces=tuple(surfaces),
        internal_convective_gain_w=150.0, infiltration_sensible_w=-400.0,
        mixing_sensible_w=0.0)


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
