from __future__ import annotations

import math
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from . import __version__
from .errors import ConfigurationError, EngineError
from .schema import (MapdlAgreement, MapdlCheck, MapdlConvergence, MapdlFace, MapdlLevel,
                     MapdlReport, SourceRef, SourceStatus)

GROUPS = ("south", "north", "east", "west", "roof", "floor")
OUTER_GROUPS = ("south", "north", "east", "west", "roof", "ground")
STUDENT_NODE_LIMIT = 128_000
STEFAN_BOLTZMANN = 5.670374419e-8
AIR_CP_J_KGK = 1005.0
DRY_AIR_R = 287.05
STANDARD_AIR_K = 293.15
SKY_VIEW = {"south": 0.5, "north": 0.5, "east": 0.5, "west": 0.5, "roof": 1.0,
            "ground": 1.0}
INSIDE_FILM = {"south": 1 / 0.13, "north": 1 / 0.13, "east": 1 / 0.13, "west": 1 / 0.13,
               "roof": 1 / 0.10, "floor": 1 / 0.17}
LINK_BITS = 10
LEAD_SUBSTEP_S = 6 * 3600
LINK_BANKS = (("V", "outdoor_air_w_k", 20), ("G", "window_ua_w_k", 40))
MESH_GROWTH = 1.35
LEVELS = {
    name: {"span": 0.60 / MESH_GROWTH ** index, "layer": 0.08 / MESH_GROWTH ** index,
           "soil_first": 0.20 / MESH_GROWTH ** index,
           "soil_largest": 1.20 / MESH_GROWTH ** index}
    for index, name in enumerate(("coarse", "medium", "fine"))
}
GCI_SAFETY = 1.25
GCI_SOURCE = SourceRef(
    citation=("Celik et al., Procedure for estimation and reporting of uncertainty due to "
              "discretization in CFD applications, J. Fluids Eng. 130 (2008) 078001"),
    status=SourceStatus.VERIFIED,
    note="Grid convergence index on three meshes with unequal refinement ratios.")

FILM_SOURCE = SourceRef(
    citation=("Surface resistances of ISO 6946: inside 0.13 m2K/W for walls, 0.10 for "
              "heat flowing up through the roof and 0.17 down through the floor; outside "
              "convection 4 + 4v W/m2K with v the wind speed, and linearised long wave "
              "radiation 4 sigma epsilon T3 to the sky"),
    status=SourceStatus.DERIVED,
    note=("The inside film joins every surface to one air node, the combined convective "
          "and radiative model ISO 6946 uses, rather than EnergyPlus's separate surface "
          "to surface radiation. It is one of the differences the agreement check "
          "measures."))
SOLAIR_SOURCE = SourceRef(
    citation=("Sun and sky on the outside faces applied as a sol-air temperature: outdoor "
              "air plus absorbed sun over the outside film, less the long wave loss to a "
              "sky below air temperature, weighted by each face's view of the sky"),
    status=SourceStatus.DERIVED)


@dataclass(frozen=True)
class Layer:
    material_id: str
    thickness_m: float
    conductivity_w_mk: float
    density_kg_m3: float
    specific_heat_j_kgk: float


@dataclass(frozen=True)
class Opening:
    facade: str
    along_from_m: float
    along_to_m: float
    z_from_m: float
    z_to_m: float
    area_m2: float
    u_value_w_m2k: float


@dataclass(frozen=True)
class MapdlCase:
    label: str
    design_hash: str
    width_m: float
    depth_m: float
    height_m: float
    wall: tuple[Layer, ...]
    roof: tuple[Layer, ...]
    floor: tuple[Layer, ...]
    soil: Layer
    soil_margin_m: float
    soil_depth_m: float
    deep_ground_c: float
    air_capacitance_j_k: float
    openings: tuple[Opening, ...]
    timestamps: tuple[str, ...]
    outdoor_c: tuple[float, ...]
    solair_c: Mapping[str, tuple[float, ...]]
    outside_film_w_m2k: Mapping[str, tuple[float, ...]]
    internal_gain_w: tuple[float, ...]
    transmitted_solar_w: tuple[float, ...]
    outdoor_air_w_k: tuple[float, ...]
    window_ua_w_k: tuple[float, ...]
    energyplus_air_c: tuple[float, ...]
    energyplus_face_w: Mapping[str, tuple[float, ...]]
    energyplus_operative_c: tuple[float, ...] = ()
    lead_hours: int = 0
    notes: tuple[str, ...] = ()

    @property
    def compared_hours(self) -> int:
        return self.hours - self.lead_hours

    def compared(self, values: Sequence[float]) -> np.ndarray:
        return np.asarray(values, dtype=float)[self.lead_hours:]

    @property
    def hours(self) -> int:
        return len(self.timestamps)

    def validate(self) -> None:
        if min(self.width_m, self.depth_m, self.height_m) <= 0.0:
            raise EngineError("the room needs a positive width, depth and height")
        for name, layers in (("wall", self.wall), ("roof", self.roof),
                             ("floor", self.floor)):
            if not layers:
                raise EngineError(f"the {name} has no layers to model")
            for layer in layers:
                if layer.thickness_m <= 0.0 or layer.conductivity_w_mk <= 0.0 \
                        or layer.density_kg_m3 <= 0.0 or layer.specific_heat_j_kgk <= 0.0:
                    raise EngineError(f"{name} layer {layer.material_id} has a non "
                                      "physical property for a solid model")
        series = [self.outdoor_c, self.internal_gain_w, self.transmitted_solar_w,
                  self.outdoor_air_w_k, self.window_ua_w_k, self.energyplus_air_c]
        series += list(self.solair_c.values()) + list(self.outside_film_w_m2k.values())
        for values in series:
            if len(values) != self.hours:
                raise EngineError("every hourly series must match the timestamps")
            if not np.isfinite(np.asarray(values, dtype=float)).all():
                raise EngineError("an hourly series holds a missing value")
        for name in ("outdoor_air_w_k", "window_ua_w_k", "transmitted_solar_w"):
            if min(getattr(self, name), default=0.0) < 0.0:
                raise EngineError(f"{name} cannot be negative")
        if not 0 <= self.lead_hours <= self.hours - 2:
            raise EngineError("the lead in must leave at least two hours to compare")
        if self.hours < 2:
            raise EngineError("the MAPDL run needs at least two hours of loads")


def thickness(layers: Sequence[Layer]) -> float:
    return float(sum(layer.thickness_m for layer in layers))


def layered_axis(start: float, layers: Sequence[Layer], direction: int,
                 step: float) -> list[float]:
    points = [start]
    position = start
    for layer in layers:
        count = max(1, math.ceil(layer.thickness_m / step - 1e-9))
        for index in range(1, count + 1):
            points.append(position + direction * layer.thickness_m * index / count)
        position += direction * layer.thickness_m
    return points


def uniform(start: float, end: float, step: float) -> list[float]:
    count = max(1, math.ceil((end - start) / step - 1e-9))
    return [start + (end - start) * index / count for index in range(count + 1)]


def graded(start: float, end: float, first: float, largest: float,
           growth: float = 1.4) -> list[float]:
    span = end - start
    if span <= 0.0:
        return [start]
    sizes = []
    size = first
    while sum(sizes) + size < span:
        sizes.append(size)
        size = min(size * growth, largest)
    if not sizes:
        return [start, end]
    scale = span / sum(sizes)
    points = [start]
    for value in sizes:
        points.append(points[-1] + value * scale)
    points[-1] = end
    return points


def merge(*parts: Sequence[float], cuts: Sequence[float] = (),
          tolerance: float = 1e-6) -> np.ndarray:
    values = sorted({round(float(value), 9) for part in parts for value in part}
                    | {round(float(value), 9) for value in cuts})
    kept = [values[0]]
    for value in values[1:]:
        if value - kept[-1] > tolerance:
            kept.append(value)
    return np.asarray(kept)


@dataclass
class Grid:
    xs: np.ndarray
    ys: np.ndarray
    zs: np.ndarray
    cells: np.ndarray
    materials: list[Layer]
    level: str

    @property
    def node_shape(self) -> tuple[int, int, int]:
        return len(self.xs), len(self.ys), len(self.zs)

    def node_id(self, i: int, j: int, k: int) -> int:
        nx, ny, _ = self.node_shape
        return 1 + i + nx * (j + ny * k)

    @property
    def element_count(self) -> int:
        return int((self.cells >= 0).sum())

    def used_nodes(self) -> np.ndarray:
        nx, ny, nz = self.node_shape
        used = np.zeros((nx, ny, nz), dtype=bool)
        solid = self.cells >= 0
        for di in (0, 1):
            for dj in (0, 1):
                for dk in (0, 1):
                    used[di:nx - 1 + di, dj:ny - 1 + dj, dk:nz - 1 + dk] |= solid
        return used


AIR = -2
EMPTY = -1


def build_grid(case: MapdlCase, level: str = "medium") -> Grid:
    if level not in LEVELS:
        raise EngineError(f"unknown mesh level {level!r}; use one of {sorted(LEVELS)}")
    case.validate()
    size = LEVELS[level]
    width, depth, height = case.width_m, case.depth_m, case.height_m
    wall_t = thickness(case.wall)
    roof_t = thickness(case.roof)
    floor_t = thickness(case.floor)
    margin = case.soil_margin_m
    wall_x_lo = layered_axis(0.0, case.wall, -1, size["layer"])
    wall_x_hi = layered_axis(width, case.wall, 1, size["layer"])
    wall_y_lo = layered_axis(0.0, case.wall, -1, size["layer"])
    wall_y_hi = layered_axis(depth, case.wall, 1, size["layer"])
    x_cuts, z_cuts, y_cuts = [], [], []
    for opening in case.openings:
        z_cuts += [opening.z_from_m, opening.z_to_m]
        if opening.facade in ("south", "north"):
            x_cuts += [opening.along_from_m, opening.along_to_m]
        else:
            y_cuts += [opening.along_from_m, opening.along_to_m]
    xs = merge(graded(-wall_t, -wall_t - margin, size["soil_first"], size["soil_largest"]),
               wall_x_lo, uniform(0.0, width, size["span"]), wall_x_hi,
               graded(width + wall_t, width + wall_t + margin, size["soil_first"],
                      size["soil_largest"]), cuts=x_cuts)
    ys = merge(graded(-wall_t, -wall_t - margin, size["soil_first"], size["soil_largest"]),
               wall_y_lo, uniform(0.0, depth, size["span"]), wall_y_hi,
               graded(depth + wall_t, depth + wall_t + margin, size["soil_first"],
                      size["soil_largest"]), cuts=y_cuts)
    zs = merge(graded(-floor_t, -floor_t - case.soil_depth_m, size["soil_first"],
                      size["soil_largest"]),
               layered_axis(0.0, case.floor, -1, size["layer"]),
               uniform(0.0, height, size["span"]),
               layered_axis(height, case.roof, 1, size["layer"]), cuts=z_cuts)
    materials: list[Layer] = []
    index_of: dict[tuple, int] = {}

    def material(layer: Layer) -> int:
        key = (layer.material_id, layer.conductivity_w_mk, layer.density_kg_m3,
               layer.specific_heat_j_kgk)
        if key not in index_of:
            index_of[key] = len(materials)
            materials.append(layer)
        return index_of[key]

    def by_depth(layers: Sequence[Layer], distance: float) -> Layer:
        edge = 0.0
        for layer in layers:
            edge += layer.thickness_m
            if distance < edge - 1e-9:
                return layer
        return layers[-1]

    soil = material(case.soil)
    wall_ids = [material(layer) for layer in case.wall]
    roof_ids = [material(layer) for layer in case.roof]
    floor_ids = [material(layer) for layer in case.floor]
    lookup_wall = {id(layer): index for layer, index in zip(case.wall, wall_ids)}
    lookup_roof = {id(layer): index for layer, index in zip(case.roof, roof_ids)}
    lookup_floor = {id(layer): index for layer, index in zip(case.floor, floor_ids)}
    cx = 0.5 * (xs[:-1] + xs[1:])
    cy = 0.5 * (ys[:-1] + ys[1:])
    cz = 0.5 * (zs[:-1] + zs[1:])
    cells = np.full((len(cx), len(cy), len(cz)), EMPTY, dtype=int)
    for k, z in enumerate(cz):
        for j, y in enumerate(cy):
            for i, x in enumerate(cx):
                inside = 0.0 < x < width and 0.0 < y < depth
                footprint = -wall_t < x < width + wall_t and -wall_t < y < depth + wall_t
                if z < -floor_t:
                    cells[i, j, k] = soil
                elif z < 0.0:
                    cells[i, j, k] = lookup_floor[id(by_depth(case.floor, -z))] if inside \
                        else soil
                elif z < height:
                    if inside:
                        cells[i, j, k] = AIR
                    elif footprint:
                        if y < 0.0 or y > depth:
                            distance = -y if y < 0.0 else y - depth
                            facade = "south" if y < 0.0 else "north"
                            along = x
                        else:
                            distance = -x if x < 0.0 else x - width
                            facade = "west" if x < 0.0 else "east"
                            along = y
                        if any(opening.facade == facade
                               and opening.along_from_m < along < opening.along_to_m
                               and opening.z_from_m < z < opening.z_to_m
                               for opening in case.openings):
                            cells[i, j, k] = EMPTY
                        else:
                            cells[i, j, k] = lookup_wall[id(by_depth(case.wall, distance))]
                elif z < height + roof_t:
                    if footprint:
                        cells[i, j, k] = lookup_roof[id(by_depth(case.roof, z - height))]
    grid = Grid(xs=xs, ys=ys, zs=zs, cells=cells, materials=materials, level=level)
    nodes = int(grid.used_nodes().sum())
    if nodes > STUDENT_NODE_LIMIT:
        raise EngineError(
            f"the {level} mesh has {nodes} nodes, over the {STUDENT_NODE_LIMIT} a student "
            "licence solves; use a coarser level")
    return grid


def _fmt(value: float) -> str:
    return format(float(value), ".8g")


@dataclass
class Deck:
    text: str
    node_count: int
    element_count: int
    air_node: int
    face_area_m2: dict[str, float]
    outer_area_m2: dict[str, float]
    notes: list[str] = field(default_factory=list)


def _inner_weights(grid: Grid, case: MapdlCase) -> tuple[dict[str, dict[int, float]],
                                                         dict[str, float]]:
    xs, ys, zs = grid.xs, grid.ys, grid.zs
    width, depth, height = case.width_m, case.depth_m, case.height_m

    def index(axis: np.ndarray, value: float) -> int:
        position = int(np.argmin(np.abs(axis - value)))
        if abs(axis[position] - value) > 1e-6:
            raise EngineError("a room face does not fall on a grid plane")
        return position

    i0, i1 = index(xs, 0.0), index(xs, width)
    j0, j1 = index(ys, 0.0), index(ys, depth)
    k0, k1 = index(zs, 0.0), index(zs, height)
    weights: dict[str, dict[int, float]] = {name: {} for name in GROUPS}
    areas = {name: 0.0 for name in GROUPS}

    def add(group: str, corners: list[int], area: float) -> None:
        for node in corners:
            weights[group][node] = weights[group].get(node, 0.0) + area / 4.0
        areas[group] += area

    for (group, fixed, axis_a, axis_b, a_range, b_range) in (
            ("south", ("j", j0), "i", "k", (i0, i1), (k0, k1)),
            ("north", ("j", j1), "i", "k", (i0, i1), (k0, k1)),
            ("west", ("i", i0), "j", "k", (j0, j1), (k0, k1)),
            ("east", ("i", i1), "j", "k", (j0, j1), (k0, k1)),
            ("floor", ("k", k0), "i", "j", (i0, i1), (j0, j1)),
            ("roof", ("k", k1), "i", "j", (i0, i1), (j0, j1))):
        axes = {"i": xs, "j": ys, "k": zs}
        for a in range(a_range[0], a_range[1]):
            for b in range(b_range[0], b_range[1]):
                coords = {fixed[0]: fixed[1], axis_a: a, axis_b: b}
                cell = [coords["i"], coords["j"], coords["k"]]
                probe = list(cell)
                if fixed[0] == "j":
                    probe[1] = fixed[1] - 1 if group == "south" else fixed[1]
                elif fixed[0] == "i":
                    probe[0] = fixed[1] - 1 if group == "west" else fixed[1]
                else:
                    probe[2] = fixed[1] - 1 if group == "floor" else fixed[1]
                if not (0 <= probe[0] < grid.cells.shape[0]
                        and 0 <= probe[1] < grid.cells.shape[1]
                        and 0 <= probe[2] < grid.cells.shape[2]):
                    continue
                if grid.cells[probe[0], probe[1], probe[2]] < 0:
                    continue
                span_a = axes[axis_a][a + 1] - axes[axis_a][a]
                span_b = axes[axis_b][b + 1] - axes[axis_b][b]
                corners = []
                for da in (0, 1):
                    for db in (0, 1):
                        point = {fixed[0]: fixed[1], axis_a: a + da, axis_b: b + db}
                        corners.append(grid.node_id(point["i"], point["j"], point["k"]))
                add(group, corners, span_a * span_b)
    return weights, areas


def write_deck(case: MapdlCase, grid: Grid, cycles: int = 3, substeps: int = 4) -> Deck:
    if cycles < 1 or substeps < 1:
        raise EngineError("cycles and substeps must be at least one")
    nx, ny, nz = grid.node_shape
    used = grid.used_nodes()
    lines: list[str] = ["/BATCH", "/NERR,5,10000", "/PREP7", "ET,1,SOLID278", "ET,2,SURF152",
                        "KEYOPT,2,4,1", "KEYOPT,2,5,1", "KEYOPT,2,8,2", "ET,3,MASS71",
                        "KEYOPT,3,3,1", "ET,4,LINK34"]
    for index, layer in enumerate(grid.materials, start=1):
        lines += [f"MP,KXX,{index},{_fmt(layer.conductivity_w_mk)}",
                  f"MP,DENS,{index},{_fmt(layer.density_kg_m3)}",
                  f"MP,C,{index},{_fmt(layer.specific_heat_j_kgk)}"]
    link_material = len(grid.materials) + 1
    lines.append(f"MP,HF,{link_material},1.0")
    node_count = 0
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                if used[i, j, k]:
                    lines.append(f"N,{grid.node_id(i, j, k)},{_fmt(grid.xs[i])},"
                                 f"{_fmt(grid.ys[j])},{_fmt(grid.zs[k])}")
                    node_count += 1
    last_node = grid.node_id(nx - 1, ny - 1, nz - 1)
    air = last_node + 1
    outdoor = {name: air + 1 + index for index, name in enumerate(OUTER_GROUPS)}
    outdoor_air = air + 1 + len(OUTER_GROUPS)
    cx = 0.5 * (grid.xs[:-1] + grid.xs[1:])
    lines.append(f"N,{air},{_fmt(case.width_m / 2)},{_fmt(case.depth_m / 2)},"
                 f"{_fmt(case.height_m / 2)}")
    for index, node in enumerate(list(outdoor.values()) + [outdoor_air]):
        lines.append(f"N,{node},{_fmt(cx[-1] + 5.0 + index)},0,{_fmt(case.height_m)}")
    lines.append("TYPE,1")
    elements = 0
    by_material: dict[int, list[tuple[int, int, int]]] = {}
    for (i, j, k), value in np.ndenumerate(grid.cells):
        if value >= 0:
            by_material.setdefault(int(value), []).append((i, j, k))
    for value, cells in sorted(by_material.items()):
        lines.append(f"MAT,{value + 1}")
        for i, j, k in cells:
            n = grid.node_id
            lines.append("E," + ",".join(str(item) for item in (
                n(i, j, k), n(i + 1, j, k), n(i + 1, j + 1, k), n(i, j + 1, k),
                n(i, j, k + 1), n(i + 1, j, k + 1), n(i + 1, j + 1, k + 1),
                n(i, j + 1, k + 1))))
            elements += 1
    width, depth, height = case.width_m, case.depth_m, case.height_m
    wall_t, roof_t, floor_t = thickness(case.wall), thickness(case.roof), \
        thickness(case.floor)
    tol = 1e-5
    inner_select = {
        "south": [f"NSEL,S,LOC,Y,{_fmt(-tol)},{_fmt(tol)}",
                  f"NSEL,R,LOC,X,{_fmt(-tol)},{_fmt(width + tol)}",
                  f"NSEL,R,LOC,Z,{_fmt(-tol)},{_fmt(height + tol)}"],
        "north": [f"NSEL,S,LOC,Y,{_fmt(depth - tol)},{_fmt(depth + tol)}",
                  f"NSEL,R,LOC,X,{_fmt(-tol)},{_fmt(width + tol)}",
                  f"NSEL,R,LOC,Z,{_fmt(-tol)},{_fmt(height + tol)}"],
        "west": [f"NSEL,S,LOC,X,{_fmt(-tol)},{_fmt(tol)}",
                 f"NSEL,R,LOC,Y,{_fmt(-tol)},{_fmt(depth + tol)}",
                 f"NSEL,R,LOC,Z,{_fmt(-tol)},{_fmt(height + tol)}"],
        "east": [f"NSEL,S,LOC,X,{_fmt(width - tol)},{_fmt(width + tol)}",
                 f"NSEL,R,LOC,Y,{_fmt(-tol)},{_fmt(depth + tol)}",
                 f"NSEL,R,LOC,Z,{_fmt(-tol)},{_fmt(height + tol)}"],
        "floor": [f"NSEL,S,LOC,Z,{_fmt(-tol)},{_fmt(tol)}",
                  f"NSEL,R,LOC,X,{_fmt(-tol)},{_fmt(width + tol)}",
                  f"NSEL,R,LOC,Y,{_fmt(-tol)},{_fmt(depth + tol)}"],
        "roof": [f"NSEL,S,LOC,Z,{_fmt(height - tol)},{_fmt(height + tol)}",
                 f"NSEL,R,LOC,X,{_fmt(-tol)},{_fmt(width + tol)}",
                 f"NSEL,R,LOC,Y,{_fmt(-tol)},{_fmt(depth + tol)}"],
    }
    outer_select = {
        "south": [f"NSEL,S,LOC,Y,{_fmt(-wall_t - tol)},{_fmt(-wall_t + tol)}",
                  f"NSEL,R,LOC,Z,{_fmt(-tol)},{_fmt(height + roof_t + tol)}"],
        "north": [f"NSEL,S,LOC,Y,{_fmt(depth + wall_t - tol)},{_fmt(depth + wall_t + tol)}",
                  f"NSEL,R,LOC,Z,{_fmt(-tol)},{_fmt(height + roof_t + tol)}"],
        "west": [f"NSEL,S,LOC,X,{_fmt(-wall_t - tol)},{_fmt(-wall_t + tol)}",
                 f"NSEL,R,LOC,Z,{_fmt(-tol)},{_fmt(height + roof_t + tol)}"],
        "east": [f"NSEL,S,LOC,X,{_fmt(width + wall_t - tol)},{_fmt(width + wall_t + tol)}",
                 f"NSEL,R,LOC,Z,{_fmt(-tol)},{_fmt(height + roof_t + tol)}"],
        "roof": [f"NSEL,S,LOC,Z,{_fmt(height + roof_t - tol)},{_fmt(height + roof_t + tol)}"],
    }
    ground_select = [f"NSEL,S,LOC,Z,{_fmt(-tol)},{_fmt(tol)}",
                     f"NSEL,U,LOC,X,{_fmt(-wall_t + tol)},{_fmt(width + wall_t - tol)}"]
    ground_select_y = [f"NSEL,S,LOC,Z,{_fmt(-tol)},{_fmt(tol)}",
                       f"NSEL,U,LOC,Y,{_fmt(-wall_t + tol)},{_fmt(depth + wall_t - tol)}",
                       f"NSEL,R,LOC,X,{_fmt(-wall_t - tol)},{_fmt(width + wall_t + tol)}"]
    lines += ["TYPE,2", "R,10", "REAL,10"]
    for group, selection in inner_select.items():
        lines += ["ALLSEL", "*GET,EBEFORE,ELEM,0,NUM,MAXD"] + selection + [
            "ESLN,S,0", "ESEL,R,TYPE,,1", f"ESURF,{air}", "*GET,EAFTER,ELEM,0,NUM,MAXD",
            "ESEL,S,ELEM,,EBEFORE+1,EAFTER", f"CM,IN_{group.upper()},ELEM",
            f"SFE,ALL,1,CONV,0,{_fmt(INSIDE_FILM[group])}"]
    for group, selection in outer_select.items():
        lines += ["ALLSEL", "*GET,EBEFORE,ELEM,0,NUM,MAXD"] + selection + [
            "ESLN,S,0", "ESEL,R,TYPE,,1", f"ESURF,{outdoor[group]}",
            "*GET,EAFTER,ELEM,0,NUM,MAXD", "ESEL,S,ELEM,,EBEFORE+1,EAFTER",
            f"CM,OUT_{group.upper()},ELEM"]
    lines += ["ALLSEL", "*GET,EBEFORE,ELEM,0,NUM,MAXD"] + ground_select + [
        "ESLN,S,0", "ESEL,R,TYPE,,1", f"ESURF,{outdoor['ground']}"] + ground_select_y + [
        "ESLN,S,0", "ESEL,R,TYPE,,1", f"ESURF,{outdoor['ground']}",
        "*GET,EAFTER,ELEM,0,NUM,MAXD", "ESEL,S,ELEM,,EBEFORE+1,EAFTER",
        "CM,OUT_GROUND,ELEM"]
    lines += ["ALLSEL", "TYPE,3", f"R,11,{_fmt(case.air_capacitance_j_k)}", "REAL,11",
              f"E,{air}", "TYPE,4", f"MAT,{link_material}"]
    for prefix, attribute, base in LINK_BANKS:
        peak = _bank_peak(getattr(case, attribute))
        lines.append(f"*GET,{prefix}FIRST,ELEM,0,NUM,MAXD")
        for bit in range(LINK_BITS):
            lines += [f"R,{base + bit},{_fmt(peak * 2 ** bit / (2 ** LINK_BITS - 1))}",
                      f"REAL,{base + bit}", f"E,{air},{outdoor_air}"]
        lines.append(f"{prefix}FIRST={prefix}FIRST+1")
    weights, inner_area = _inner_weights(grid, case)
    floor_nodes = weights["floor"]
    lines.append("ALLSEL")
    classes: dict[float, list[int]] = {}
    for node, area in floor_nodes.items():
        classes.setdefault(round(area, 9), []).append(node)
    floor_total = sum(floor_nodes.values()) or 1.0
    solar_classes = []
    for index, (area, nodes) in enumerate(sorted(classes.items())):
        name = f"FS{index}"
        lines.append(f"NSEL,S,NODE,,{nodes[0]}")
        for node in nodes[1:]:
            lines.append(f"NSEL,A,NODE,,{node}")
        lines.append(f"CM,{name},NODE")
        solar_classes.append((name, area / floor_total))
    lines.append("ALLSEL")
    lines.append(f"*DIM,TT,ARRAY,{outdoor_air}")
    lines.append(f"*DIM,WP,ARRAY,{outdoor_air}")
    for group in GROUPS:
        lines.append(f"*DIM,W_{group.upper()},ARRAY,{outdoor_air}")
        total = sum(weights[group].values()) or 1.0
        for node, area in sorted(weights[group].items()):
            lines.append(f"W_{group.upper()}({node})={_fmt(area / total)}")
    lines += ["NSEL,S,LOC,Z," + _fmt(grid.zs[0] - tol) + "," + _fmt(grid.zs[0] + tol),
              f"D,ALL,TEMP,{_fmt(case.deep_ground_c)}", "ALLSEL", "FINISH"]

    plan = solve_plan(case, cycles)
    lines += ["/SOLU", "ANTYPE,TRANS", "TRNOPT,FULL", "EQSLV,SPARSE", "OUTRES,ALL,NONE",
              "KBC,1", "TIMINT,OFF"]
    lines += _step_loads(case, plan.initial, outdoor, outdoor_air, air, solar_classes)
    lines += ["TIME,1", "NSUBST,1,1,1", "SOLVE", "TIMINT,ON", "/NOPR"]
    moment = 1.0
    written = False
    outputs: list[tuple[float, int, int]] = []
    for selector, seconds, cycle, index in plan.steps:
        if index is not None and not written:
            lines.append("OUTRES,NSOL,LAST")
            written = True
        lines += _step_loads(case, selector, outdoor, outdoor_air, air, solar_classes)
        moment += seconds
        count = substeps if seconds <= 3600 else max(substeps, int(seconds // LEAD_SUBSTEP_S))
        lines += [f"TIME,{_fmt(moment)}", f"NSUBST,{count},{count},{count}", "SOLVE"]
        if index is not None:
            outputs.append((moment, cycle, index))
    header = "cycle,hour,air_c," + ",".join(f"{group}_c" for group in GROUPS)
    names = ",".join(f"M_{group.upper()}" for group in GROUPS)
    lines += ["/GOPR", "FINISH", "/POST1", f"*DIM,TOUT,ARRAY,{len(outputs)}",
              f"*DIM,COUT,ARRAY,{len(outputs)}", f"*DIM,HOUT,ARRAY,{len(outputs)}"]
    for position, (moment, cycle, index) in enumerate(outputs, start=1):
        lines += [f"TOUT({position})={_fmt(moment)}", f"COUT({position})={cycle}",
                  f"HOUT({position})={index}"]
    lines += ["*CFOPEN,mapdl_hourly,csv", "*VWRITE", f"('{header}')",
              f"*DO,STEP,1,{len(outputs)}", "SET,,,,,TOUT(STEP)",
              f"*GET,TAIR,NODE,{air},TEMP", "*VGET,TT(1),NODE,1,TEMP"]
    for group in GROUPS:
        lines += [f"*VOPER,WP(1),TT(1),MULT,W_{group.upper()}(1)",
                  f"*VSCFUN,M_{group.upper()},SUM,WP(1)"]
    lines += ["CYC=COUT(STEP)", "HR=HOUT(STEP)", f"*VWRITE,CYC,HR,TAIR,{names}",
              "(F4.0,',',F6.0,',',F10.5,',',F10.5,',',F10.5,',',F10.5,',',F10.5,"
              "',',F10.5,',',F10.5)", "*ENDDO", "*CFCLOS", "FINISH"]
    outer_area = {group: 0.0 for group in OUTER_GROUPS}
    return Deck(text="\n".join(lines) + "\n", node_count=node_count + 1 + len(outdoor) + 1,
                element_count=elements, air_node=air, face_area_m2=inner_area,
                outer_area_m2=outer_area)


@dataclass(frozen=True)
class SolvePlan:
    initial: slice
    steps: tuple[tuple[int | slice, float, int, int | None], ...]


def solve_plan(case: MapdlCase, cycles: int) -> SolvePlan:
    hours = case.hours
    lead = case.lead_hours
    if lead == 0:
        steps = []
        for cycle in range(1, cycles + 1):
            for hour in range(hours):
                if cycle == cycles:
                    index = hour
                elif cycle == cycles - 1 and hour == hours - 1:
                    index = -1
                else:
                    index = None
                steps.append((hour, 3600.0, cycle, index))
        return SolvePlan(initial=slice(0, hours), steps=tuple(steps))
    if cycles != 1:
        raise EngineError("a case with a lead in runs once through its history")
    daily = max(0, lead // 24 - 2)
    steps = [(slice(24 * day, 24 * day + 24), 86400.0, 1, None) for day in range(daily)]
    for hour in range(24 * daily, hours):
        index = hour - lead
        steps.append((hour, 3600.0, 1, index if index >= -1 else None))
    return SolvePlan(initial=slice(0, min(24, hours)), steps=tuple(steps))


def _step_loads(case: MapdlCase, selector: int | slice, outdoor: Mapping[str, int],
                outdoor_air: int, air: int, solar_classes) -> list[str]:
    def pick(values):
        if isinstance(selector, slice):
            return float(np.mean(np.asarray(values, dtype=float)[selector]))
        return float(values[selector])

    lines = []
    for group, node in outdoor.items():
        lines.append(f"D,{node},TEMP,{_fmt(pick(case.solair_c[group]))}")
        lines += [f"CMSEL,S,OUT_{group.upper()}",
                  f"SFE,ALL,1,CONV,0,{_fmt(pick(case.outside_film_w_m2k[group]))}",
                  "ALLSEL"]
    lines.append(f"D,{outdoor_air},TEMP,{_fmt(pick(case.outdoor_c))}")
    lines.append(f"F,{air},HEAT,{_fmt(pick(case.internal_gain_w))}")
    solar = pick(case.transmitted_solar_w)
    for name, share in solar_classes:
        lines.append(f"F,{name},HEAT,{_fmt(solar * share)}")
    for prefix, attribute, _ in LINK_BANKS:
        code = link_code(pick(getattr(case, attribute)), _bank_peak(getattr(case, attribute)))
        for bit in range(LINK_BITS):
            command = "EALIVE" if code >> bit & 1 else "EKILL"
            lines.append(f"{command},{prefix}FIRST+{bit}")
    lines.append("ALLSEL")
    return lines


def _bank_peak(values: Sequence[float]) -> float:
    return max(max(values), 1e-6)


def link_code(value: float, peak: float) -> int:
    top = 2 ** LINK_BITS - 1
    return int(round(min(1.0, max(0.0, value / peak)) * top))


def solair_c(outdoor_c: np.ndarray, sky_c: np.ndarray, wind_m_s: np.ndarray,
             incident_w_m2: np.ndarray, solar_absorptance: float, emissivity: float,
             sky_view: float) -> tuple[np.ndarray, np.ndarray]:
    kelvin_out = outdoor_c + 273.15
    kelvin_sky = sky_c + 273.15
    radiative = 4.0 * STEFAN_BOLTZMANN * emissivity * ((kelvin_out + kelvin_sky) / 2.0) ** 3
    convective = 4.0 + 4.0 * np.clip(wind_m_s, 0.0, None)
    film = convective + radiative
    equivalent = outdoor_c + (solar_absorptance * np.clip(incident_w_m2, 0.0, None)
                              - sky_view * radiative * (outdoor_c - sky_c)) / film
    return equivalent, film


@dataclass(frozen=True)
class MapdlRun:
    level: str
    hours: int
    air_c: tuple[float, ...]
    surface_c: Mapping[str, tuple[float, ...]]
    face_heat_w: Mapping[str, tuple[float, ...]]
    node_count: int
    element_count: int
    runtime_s: float
    cycles: int
    air_before_c: float | None = None
    face_heat_before_w: Mapping[str, float] | None = None


def parse_hourly(path: Path, case: MapdlCase, deck: Deck, level: str, cycles: int,
                 runtime: float) -> MapdlRun:
    if not path.is_file():
        raise EngineError(f"MAPDL wrote no hourly results at {path}")
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
        parts = [item.strip() for item in line.split(",")]
        if len(parts) != 3 + len(GROUPS):
            continue
        try:
            rows.append([float(item) for item in parts])
        except ValueError:
            continue
    by_hour = {}
    for row in rows:
        hour = int(round(row[1]))
        if int(round(row[0])) == cycles or hour == -1:
            by_hour[hour] = row
    compared = case.compared_hours
    found = sum(1 for hour in range(compared) if hour in by_hour)
    if found != compared:
        raise EngineError(f"MAPDL reported {found} of the {compared} compared hours")

    def unpack(row):
        air = row[2]
        surfaces = {group: row[3 + index] for index, group in enumerate(GROUPS)}
        heat = {group: INSIDE_FILM[group] * deck.face_area_m2[group]
                * (surfaces[group] - air) for group in GROUPS}
        return air, surfaces, heat

    table = [unpack(by_hour[hour]) for hour in range(compared)]
    before = unpack(by_hour[-1]) if -1 in by_hour else None
    return MapdlRun(
        level=level, hours=compared, air_c=tuple(item[0] for item in table),
        surface_c={group: tuple(item[1][group] for item in table) for group in GROUPS},
        face_heat_w={group: tuple(item[2][group] for item in table) for group in GROUPS},
        node_count=deck.node_count, element_count=deck.element_count, runtime_s=runtime,
        cycles=cycles, air_before_c=None if before is None else before[0],
        face_heat_before_w=None if before is None else before[2])


def find_mapdl(root: Path | None = None) -> Path:
    candidates: list[Path] = []
    if os.environ.get("SHELTER_MAPDL_EXE"):
        candidates.append(Path(os.environ["SHELTER_MAPDL_EXE"]))
    if root is None:
        from .ansys import find_ansys_root
        try:
            root = find_ansys_root()
        except ConfigurationError:
            root = None
    if root is not None:
        folder = Path(root) / "ansys" / "bin" / "winx64"
        candidates += sorted(folder.glob("MAPDL[0-9][0-9][0-9].exe"), reverse=True)
        folder = Path(root) / "ansys" / "bin"
        candidates += sorted(folder.glob("mapdl*"), reverse=True)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ConfigurationError(
        "ANSYS Mechanical APDL was not found. Set SHELTER_MAPDL_EXE to the MAPDL "
        "executable, or SHELTER_ANSYS_ROOT to the version folder of the installation.")


def run_text(text: str, workdir: Path, result_name: str, executable: Path | None = None,
             cores: int = 2, timeout_s: float = 3600.0,
             shared_memory: bool = True) -> tuple[Path, float]:
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "shelter.inp"
    source.write_text(text, encoding="utf-8")
    result = workdir / result_name
    for stale in [result, workdir / "shelter.lock", *workdir.glob("shelter*.rth"),
                  *workdir.glob("shelter*.r001")]:
        if stale.exists():
            stale.unlink()
    exe = executable or find_mapdl()
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [str(exe), "-b", *(["-smp"] if shared_memory else ["-dis"]), "-np",
             str(max(1, cores)), "-i", source.name, "-o", "shelter.out", "-j", "shelter"],
            cwd=str(workdir), capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as error:
        raise EngineError(f"MAPDL did not finish within {timeout_s:.0f} s") from error
    runtime = time.perf_counter() - started
    if not result.is_file():
        log = workdir / "shelter.out"
        tail = ""
        if log.is_file():
            text_out = log.read_text(encoding="utf-8", errors="replace")
            errors = re.findall(r"\*\*\* ERROR \*\*\*.*?(?:\n\s*\n)", text_out, flags=re.S)
            tail = (errors[0] if errors else text_out[-1500:]).strip()
        raise EngineError(f"MAPDL exited with code {completed.returncode} and no results. "
                          f"{tail[:1200]}")
    return result, runtime


def run_deck(deck: Deck, workdir: Path, executable: Path | None = None, cores: int = 2,
             timeout_s: float = 3600.0, shared_memory: bool = True) -> tuple[Path, float]:
    return run_text(deck.text, workdir, "mapdl_hourly.csv", executable, cores, timeout_s,
                    shared_memory)


@dataclass(frozen=True)
class Agreement:
    hours: int
    mae_k: float
    rmse_k: float
    bias_k: float
    max_abs_k: float
    face_kwh_mapdl: Mapping[str, float]
    face_kwh_energyplus: Mapping[str, float]
    face_difference_pct: Mapping[str, float | None]
    meets_target: bool
    target_rmse_k: float


def hourly_means(end_of_hour: Sequence[float], before: float | None = None) -> np.ndarray:
    values = np.asarray(end_of_hour, dtype=float)
    first = values[-1] if before is None else before
    previous = np.concatenate([[first], values[:-1]])
    return 0.5 * (previous + values)


def agreement(case: MapdlCase, run: MapdlRun, target_rmse_k: float = 1.0,
              reference: str = "air") -> Agreement:
    mapdl = hourly_means(run.air_c, run.air_before_c)
    if reference == "operative" and case.energyplus_operative_c:
        energyplus = case.compared(case.energyplus_operative_c)
    elif reference in ("air", "operative"):
        energyplus = case.compared(case.energyplus_air_c)
    else:
        raise EngineError(f"unknown reference temperature {reference!r}")
    if len(mapdl) != len(energyplus):
        raise EngineError("MAPDL and EnergyPlus cover different hours")
    difference = mapdl - energyplus
    rmse = float(np.sqrt(np.mean(difference ** 2)))
    before = run.face_heat_before_w or {}
    face_mapdl = {group: float(np.sum(hourly_means(run.face_heat_w[group],
                                                   before.get(group)))) / 1000.0
                  for group in GROUPS}
    face_mapdl["floor"] -= float(np.sum(case.compared(case.transmitted_solar_w))) / 1000.0
    face_plus = {group: float(np.sum(case.compared(case.energyplus_face_w[group]))) / 1000.0
                 for group in GROUPS if group in case.energyplus_face_w}
    percent = {}
    for group, value in face_plus.items():
        percent[group] = None if abs(value) < 1e-6 else \
            100.0 * (face_mapdl[group] - value) / abs(value)
    return Agreement(hours=len(mapdl), mae_k=float(np.mean(np.abs(difference))),
                     rmse_k=rmse, bias_k=float(np.mean(difference)),
                     max_abs_k=float(np.max(np.abs(difference))),
                     face_kwh_mapdl=face_mapdl, face_kwh_energyplus=face_plus,
                     face_difference_pct=percent, meets_target=rmse <= target_rmse_k,
                     target_rmse_k=target_rmse_k)


def grid_convergence(coarse: float, medium: float, fine: float, ratio_coarse: float,
                     ratio_fine: float | None = None,
                     safety: float = GCI_SAFETY) -> dict[str, float | None]:
    r21 = ratio_fine if ratio_fine is not None else ratio_coarse
    r32 = ratio_coarse
    e21 = medium - fine
    e32 = coarse - medium
    if abs(e21) < 1e-12:
        return {"order": None, "gci_fine": 0.0, "extrapolated": fine}
    if e21 * e32 <= 0.0 or r21 <= 1.0 or r32 <= 1.0:
        return {"order": None, "gci_fine": None, "extrapolated": None}
    order = abs(math.log(abs(e32 / e21))) / math.log(r21)
    for _ in range(200):
        shift = math.log((r21 ** order - 1.0) / (r32 ** order - 1.0))
        updated = abs(math.log(abs(e32 / e21)) + shift) / math.log(r21)
        if abs(updated - order) < 1e-10:
            order = updated
            break
        order = updated
    denominator = r21 ** order - 1.0
    if order <= 0.0 or denominator <= 0.0:
        return {"order": None, "gci_fine": None, "extrapolated": None}
    extrapolated = fine - e21 / denominator
    return {"order": order, "gci_fine": safety * abs(e21) / denominator,
            "extrapolated": extrapolated}


METAL_SKIN_M = 0.003
GROUP_OF = {"ROOM_WALL_SOUTH": "south", "ROOM_WALL_NORTH": "north",
            "ROOM_WALL_EAST": "east", "ROOM_WALL_WEST": "west", "ROOM_ROOF": "roof",
            "ROOM_FLOOR": "floor"}


def energyplus_standard_density(elevation_m: float) -> float:
    from .air_quality import standard_pressure_kpa

    return standard_pressure_kpa(elevation_m) * 1000.0 / (DRY_AIR_R * STANDARD_AIR_K)


def glazing_u_hourly(u_value: float, shutter, hours_of_day: np.ndarray) -> np.ndarray:
    values = np.full(len(hours_of_day), float(u_value))
    if shutter is None or not shutter.present or shutter.compliance <= 0.0:
        return values
    closed = 1.0 / (1.0 / u_value + shutter.thermal_resistance_m2k_w)
    shut = np.isin(hours_of_day, shutter.closed_hours)
    values[shut] = shutter.compliance * closed + (1.0 - shutter.compliance) * u_value
    return values


def _hourly_mean(series) -> np.ndarray:
    return series.resample("1h").mean().to_numpy(dtype=float)


def _solid_layers(construction, library) -> tuple[list[Layer], list[str]]:
    kept: list[Layer] = []
    dropped: list[str] = []
    for spec in reversed(construction.layers):
        material = library.material(spec.material_id)
        if spec.thickness_m < METAL_SKIN_M and material.conductivity_w_mk > 10.0:
            dropped.append(material.name)
            continue
        kept.append(Layer(material_id=material.id, thickness_m=spec.thickness_m,
                          conductivity_w_mk=material.conductivity_w_mk,
                          density_kg_m3=material.density_kg_m3,
                          specific_heat_j_kgk=material.specific_heat_j_kgk))
    return kept, dropped


def _optics(construction, library) -> tuple[float, float]:
    outer = library.material(construction.layers[0].material_id)
    solar = outer.solar_absorptance if outer.solar_absorptance is not None else 0.7
    thermal = outer.thermal_absorptance if outer.thermal_absorptance is not None else 0.9
    return solar, thermal


def case_from_run(label: str, design_hash: str, design, site, library, weather, output,
                  model, soil: Layer, soil_margin_m: float = 3.0,
                  soil_depth_m: float = 5.0, compare_from=None) -> MapdlCase:
    from .air_quality import standard_pressure_kpa
    from .geometry import rotate_about_z

    if design.arrangement.value != "single_room":
        raise EngineError(
            f"The MAPDL core models a single room today; {design.arrangement.value} "
            "needs its buffer zone added as a second air node before it can be confirmed "
            "in MAPDL.")
    width, depth, height = (design.plan.width_m, design.plan.depth_m,
                            design.plan.ceiling_height_m)
    outdoor_raw = output.series("Site Outdoor Air Drybulb Temperature")
    index = outdoor_raw.resample("1h").mean().index
    outdoor = _hourly_mean(outdoor_raw)
    sky = _hourly_mean(output.series("Site Sky Temperature"))
    wind = _hourly_mean(output.series("Site Wind Speed"))
    incident = output.wide("Surface Outside Face Incident Solar Radiation Rate per Area")
    if incident.empty:
        raise EngineError("the EnergyPlus run carries no surface solar; run it with "
                          "surface output")
    incident = incident.resample("1h").mean()
    wall_layers, wall_dropped = _solid_layers(design.wall, library)
    roof_layers, roof_dropped = _solid_layers(design.roof, library)
    floor_layers, floor_dropped = _solid_layers(design.floor, library)
    notes: list[str] = []
    for name, dropped in (("wall", wall_dropped), ("roof", roof_dropped),
                          ("floor", floor_dropped)):
        if dropped:
            notes.append(f"The {name} skin of {', '.join(sorted(set(dropped)))} is under "
                         "3 mm of metal and conducts almost nothing away, so it sets only "
                         "the surface optics and is left out of the solid mesh.")

    def sun_on(surface: str) -> np.ndarray:
        if surface in incident:
            return incident[surface].to_numpy(dtype=float)
        return np.zeros(len(index))

    solair: dict[str, tuple[float, ...]] = {}
    films: dict[str, tuple[float, ...]] = {}
    for group, surface, construction in (("south", "ROOM_WALL_SOUTH", design.wall),
                                         ("north", "ROOM_WALL_NORTH", design.wall),
                                         ("east", "ROOM_WALL_EAST", design.wall),
                                         ("west", "ROOM_WALL_WEST", design.wall),
                                         ("roof", "ROOM_ROOF", design.roof)):
        absorb, emit = _optics(construction, library)
        equivalent, film = solair_c(outdoor, sky, wind, sun_on(surface), absorb, emit,
                                    SKY_VIEW[group])
        solair[group] = tuple(equivalent.tolist())
        films[group] = tuple(film.tolist())
    equivalent, film = solair_c(outdoor, sky, wind, sun_on("ROOM_ROOF"), 0.9, 0.9,
                                SKY_VIEW["ground"])
    solair["ground"] = tuple(equivalent.tolist())
    films["ground"] = tuple(film.tolist())

    hours_of_day = np.asarray(index.hour)
    window_ua = np.zeros(len(index))
    openings = []
    for polygon in model.windows:
        if polygon.zone != "ROOM" or polygon.facade is None:
            continue
        facade = polygon.facade.value
        spec = design.windows.get(polygon.facade)
        if spec is None or spec.glazing_id is None:
            continue
        local = rotate_about_z(polygon.vertices, 180.0 - design.plan.azimuth_deg)
        xs = [vertex[0] + width / 2 for vertex in local]
        ys = [vertex[1] + depth / 2 for vertex in local]
        zs = [vertex[2] for vertex in local]
        along = (min(xs), max(xs)) if facade in ("south", "north") else (min(ys), max(ys))
        openings.append(Opening(
            facade=facade, along_from_m=along[0], along_to_m=along[1], z_from_m=min(zs),
            z_to_m=max(zs), area_m2=polygon.area_m2,
            u_value_w_m2k=library.glazing(spec.glazing_id).u_value_w_m2k))
        window_ua += polygon.area_m2 * glazing_u_hourly(
            library.glazing(spec.glazing_id).u_value_w_m2k, spec.shutter, hours_of_day)
    if any(spec.shutter.present and spec.shutter.compliance < 0.999
           for spec in design.windows.values()):
        notes.append("Night shutters that are closed on only some nights are spread as an "
                     "average over every night in MAPDL; EnergyPlus closes them on whole "
                     "nights.")

    transmitted = output.wide("Surface Window Transmitted Solar Radiation Energy")
    if transmitted.empty:
        solar_w = np.zeros(len(index))
    else:
        solar_w = (transmitted.resample("1h").sum().sum(axis=1) / 3600.0).to_numpy(
            dtype=float)
    gains = np.zeros(len(index))
    for name in ("Zone Total Internal Convective Heating Rate",
                 "Zone Total Internal Radiant Heating Rate",
                 "Zone Total Internal Visible Radiation Heating Rate"):
        series = output.series(name, "ROOM", required=False)
        if series is not None and not series.empty:
            gains = gains + _hourly_mean(series)
    flows = np.zeros(len(index))
    for name in ("Zone Infiltration Standard Density Volume Flow Rate",
                 "Zone Ventilation Standard Density Volume Flow Rate"):
        series = output.series(name, "ROOM", required=False)
        if series is not None and not series.empty:
            flows = flows + _hourly_mean(series)
    conductance = flows * energyplus_standard_density(site.elevation_m) * AIR_CP_J_KGK
    air = _hourly_mean(output.series("Zone Mean Air Temperature", "ROOM"))
    operative_series = output.series("Zone Operative Temperature", "ROOM", required=False)
    operative = air if operative_series is None or operative_series.empty         else _hourly_mean(operative_series)
    faces: dict[str, tuple[float, ...]] = {}
    conduction = output.wide("Surface Inside Face Conduction Heat Transfer Rate")
    if not conduction.empty:
        conduction = conduction.resample("1h").mean()
        for surface, group in GROUP_OF.items():
            if surface in conduction:
                faces[group] = tuple(conduction[surface].to_numpy(dtype=float).tolist())
    density = standard_pressure_kpa(site.elevation_m) * 1000.0 / (287.05 * 288.15)
    notes.append("Partition walls inside the room, which EnergyPlus carries as internal "
                 "mass, are not in the MAPDL mesh.")
    lengths = {len(outdoor), len(sky), len(wind), len(solar_w), len(gains),
               len(conductance), len(air), len(operative), len(window_ua)}
    if len(lengths) != 1:
        raise EngineError("the EnergyPlus hourly series do not line up")
    return MapdlCase(
        label=label, design_hash=design_hash, width_m=width, depth_m=depth,
        height_m=height, wall=tuple(wall_layers), roof=tuple(roof_layers),
        floor=tuple(floor_layers), soil=soil, soil_margin_m=soil_margin_m,
        soil_depth_m=soil_depth_m,
        deep_ground_c=float(weather.frame["dry_bulb_c"].mean()),
        air_capacitance_j_k=density * AIR_CP_J_KGK * width * depth * height,
        openings=tuple(openings),
        timestamps=tuple(stamp.isoformat() for stamp in index),
        outdoor_c=tuple(outdoor.tolist()), solair_c=solair, outside_film_w_m2k=films,
        internal_gain_w=tuple(gains.tolist()), transmitted_solar_w=tuple(solar_w.tolist()),
        outdoor_air_w_k=tuple(conductance.tolist()), window_ua_w_k=tuple(window_ua.tolist()),
        energyplus_air_c=tuple(air.tolist()), energyplus_operative_c=tuple(operative.tolist()),
        lead_hours=0 if compare_from is None else int((index < compare_from).sum()), energyplus_face_w=faces, notes=tuple(notes))


REPORT_SUFFIX = "_mapdl.json"
SOLVER_NAME = "ANSYS Mechanical APDL"


def mapdl_version(executable: Path) -> str:
    match = re.search(r"(\d)(\d)(\d)", Path(executable).stem)
    if not match:
        return "unknown"
    return f"20{match.group(1)}{match.group(2)} R{match.group(3)}"


def level_summary(case: MapdlCase, run: MapdlRun, substeps: int) -> MapdlLevel:
    air = hourly_means(run.air_c, run.air_before_c)
    fit = agreement(case, run)
    return MapdlLevel(level=run.level, node_count=run.node_count,
                      element_count=run.element_count, substeps=substeps,
                      runtime_s=run.runtime_s, mean_air_c=float(np.mean(air)),
                      minimum_air_c=float(np.min(air)), rmse_k=fit.rmse_k)


def study(levels: Sequence[MapdlLevel], halved: MapdlLevel | None = None,
          metric: str = "mean_air_c") -> MapdlConvergence | None:
    order = [item for name in LEVELS for item in levels if item.level == name]
    if len(order) != 3:
        return None
    values = [getattr(item, metric) for item in order]
    r32 = (order[1].node_count / order[0].node_count) ** (1.0 / 3.0)
    r21 = (order[2].node_count / order[1].node_count) ** (1.0 / 3.0)
    if r21 <= 1.0 or r32 <= 1.0:
        return None
    result = grid_convergence(*values, ratio_coarse=r32, ratio_fine=r21)
    step = None
    if halved is not None:
        base = next((item for item in order if item.level == halved.level), None)
        if base is not None:
            step = abs(getattr(halved, metric) - getattr(base, metric))
    return MapdlConvergence(metric=metric, refinement_ratio=r21, order=result["order"],
                            gci_fine_k=result["gci_fine"],
                            extrapolated=result["extrapolated"], time_step_difference_k=step)


def build_report(case: MapdlCase, run: MapdlRun, site_id: str, version: str,
                 substeps: int, levels: Sequence[MapdlLevel] = (),
                 convergence: MapdlConvergence | None = None,
                 checks: Sequence[MapdlCheck] = (),
                 generated_at=None) -> MapdlReport:
    from datetime import datetime, timezone

    fit = agreement(case, run)
    faces = tuple(MapdlFace(group=group, mapdl_kwh=fit.face_kwh_mapdl[group],
                            energyplus_kwh=fit.face_kwh_energyplus.get(group),
                            difference_percent=fit.face_difference_pct.get(group))
                  for group in GROUPS)
    summary = MapdlAgreement(hours=fit.hours, mae_k=fit.mae_k, rmse_k=fit.rmse_k,
                             bias_k=fit.bias_k, max_abs_k=fit.max_abs_k,
                             target_rmse_k=fit.target_rmse_k, meets_target=fit.meets_target,
                             faces=faces)
    return MapdlReport(
        design_hash=case.design_hash, label=case.label, site_id=site_id,
        engine_version=__version__, solver=SOLVER_NAME,
        version=version, level=run.level, cycles=run.cycles, substeps=substeps,
        node_count=run.node_count, element_count=run.element_count, runtime_s=run.runtime_s,
        timestamps=case.timestamps[case.lead_hours:],
        outdoor_c=tuple(case.compared(case.outdoor_c).tolist()),
        mapdl_air_c=tuple(hourly_means(run.air_c, run.air_before_c).tolist()),
        energyplus_air_c=tuple(case.compared(case.energyplus_air_c).tolist()),
        lead_days=case.lead_hours // 24, agreement=summary, levels=tuple(levels),
        convergence=convergence, checks=tuple(checks), notes=case.notes,
        sources=(FILM_SOURCE, SOLAIR_SOURCE, GCI_SOURCE),
        generated_at=generated_at or datetime.now(timezone.utc))


def check_record(result) -> MapdlCheck:
    return MapdlCheck(name=result.name, description=result.description,
                      points=result.points, max_abs_k=result.max_abs_k,
                      tolerance_k=result.tolerance_k, passed=result.passed,
                      runtime_s=result.runtime_s)


def write_report(report: MapdlReport, directory: str | Path) -> Path:
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{report.design_hash}_{report.label}{REPORT_SUFFIX}"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(report.model_dump_json(indent=1), encoding="utf-8")
    temporary.replace(path)
    return path


def load_report(path: str | Path) -> MapdlReport:
    source = Path(path)
    if not source.is_file():
        raise EngineError(f"no MAPDL report at {source}")
    return MapdlReport.model_validate_json(source.read_text(encoding="utf-8"))


def find_reports(directory: str | Path | None, design_hash: str) -> tuple[Path, ...]:
    if directory is None or not Path(directory).is_dir():
        return ()
    return tuple(sorted(Path(directory).glob(f"{design_hash}_*{REPORT_SUFFIX}")))
