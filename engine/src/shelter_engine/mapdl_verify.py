from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .errors import EngineError
from .mapdl import Layer, _fmt, layered_axis, run_text, thickness, uniform

RESULT_NAME = "check_nodes.csv"
SECTION_M = 0.1
RECTANGLE_TOLERANCE_K = 0.1
WALL_TOLERANCE_K = 0.01
STEP_TOLERANCE_K = 0.1
VERIFY_NOTE = ("Analytic checks of the element types the shelter model uses. They follow "
               "the idea of the ISO 10211 test reference cases but are not a certified "
               "ISO 10211 validation.")


@dataclass(frozen=True)
class CheckResult:
    name: str
    description: str
    points: int
    max_abs_k: float
    tolerance_k: float
    runtime_s: float

    @property
    def passed(self) -> bool:
        return self.max_abs_k <= self.tolerance_k


@dataclass
class _Column:
    xs: np.ndarray
    ys: np.ndarray
    zs: np.ndarray

    @property
    def shape(self) -> tuple[int, int, int]:
        return len(self.xs), len(self.ys), len(self.zs)

    def node(self, i: int, j: int, k: int) -> int:
        nx, ny, _ = self.shape
        return 1 + i + nx * (j + ny * k)

    @property
    def count(self) -> int:
        nx, ny, nz = self.shape
        return nx * ny * nz

    def coordinates(self) -> np.ndarray:
        nx, ny, nz = self.shape
        grid = np.zeros((self.count, 3))
        for k in range(nz):
            for j in range(ny):
                for i in range(nx):
                    grid[self.node(i, j, k) - 1] = (self.xs[i], self.ys[j], self.zs[k])
        return grid


def _mesh_lines(column: _Column, material_of: Sequence[int]) -> list[str]:
    nx, ny, nz = column.shape
    lines = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                lines.append(f"N,{column.node(i, j, k)},{_fmt(column.xs[i])},"
                             f"{_fmt(column.ys[j])},{_fmt(column.zs[k])}")
    lines.append("TYPE,1")
    current = None
    for k in range(nz - 1):
        for j in range(ny - 1):
            for i in range(nx - 1):
                if material_of[i] != current:
                    current = material_of[i]
                    lines.append(f"MAT,{current}")
                n = column.node
                lines.append("E," + ",".join(str(item) for item in (
                    n(i, j, k), n(i + 1, j, k), n(i + 1, j + 1, k), n(i, j + 1, k),
                    n(i, j, k + 1), n(i + 1, j, k + 1), n(i + 1, j + 1, k + 1),
                    n(i, j + 1, k + 1))))
    return lines


def _write_nodes(total: int) -> list[str]:
    return [f"*DIM,TT,ARRAY,{total}", f"*VGET,TT(1),NODE,1,TEMP",
            f"*CFOPEN,{Path(RESULT_NAME).stem},csv", "*VWRITE,TT(1)", "(E24.14)", "*CFCLOS"]


def _read_nodes(path: Path, expected: int) -> np.ndarray:
    values = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            values.append(float(line))
    if len(values) < expected:
        raise EngineError(f"MAPDL returned {len(values)} node temperatures, expected "
                          f"{expected}")
    return np.asarray(values[:expected])


def rectangle_series_c(x: np.ndarray, z: np.ndarray, width: float, height: float,
                       top_c: float, terms: int = 400) -> np.ndarray:
    x = np.asarray(x, dtype=float)[..., None]
    z = np.asarray(z, dtype=float)[..., None]
    n = np.arange(1, 2 * terms, 2, dtype=float)
    k = n * math.pi / width
    ratio = np.exp(k * (z - height)) * (-np.expm1(-2.0 * k * z)) / (
        -np.expm1(-2.0 * k * height))
    return (4.0 * top_c / math.pi * np.sin(k * x) * ratio / n).sum(axis=-1)


def rectangle_deck(width: float = 1.0, height: float = 1.0, top_c: float = 20.0,
                   cells: int = 40) -> tuple[str, _Column]:
    column = _Column(np.asarray(uniform(0.0, width, width / cells)),
                     np.asarray([0.0, SECTION_M]),
                     np.asarray(uniform(0.0, height, height / cells)))
    tol = 1e-6
    lines = ["/BATCH", "/PREP7", "ET,1,SOLID278", "MP,KXX,1,1.0"]
    lines += _mesh_lines(column, [1] * (len(column.xs) - 1))
    lines += [f"NSEL,S,LOC,Z,{_fmt(height - tol)},{_fmt(height + tol)}",
              f"D,ALL,TEMP,{_fmt(top_c)}",
              f"NSEL,S,LOC,Z,{_fmt(-tol)},{_fmt(tol)}",
              f"NSEL,A,LOC,X,{_fmt(-tol)},{_fmt(tol)}",
              f"NSEL,A,LOC,X,{_fmt(width - tol)},{_fmt(width + tol)}",
              "D,ALL,TEMP,0", "ALLSEL", "FINISH", "/SOLU", "ANTYPE,STATIC", "SOLVE",
              "FINISH", "/POST1", "SET,LAST"]
    lines += _write_nodes(column.count)
    return "\n".join(lines) + "\n", column


def wall_profile_c(positions: np.ndarray, layers: Sequence[Layer], inside_c: float,
                   outside_c: float, inside_h: float, outside_h: float) -> np.ndarray:
    resistance = 1.0 / inside_h + sum(layer.thickness_m / layer.conductivity_w_mk
                                      for layer in layers) + 1.0 / outside_h
    flux = (inside_c - outside_c) / resistance
    edges = np.concatenate([[0.0], np.cumsum([layer.thickness_m for layer in layers])])
    surface = inside_c - flux / inside_h
    drops = np.concatenate([[0.0], np.cumsum([flux * layer.thickness_m
                                               / layer.conductivity_w_mk
                                               for layer in layers])])
    result = np.empty(len(positions))
    for index, x in enumerate(np.asarray(positions, dtype=float)):
        layer = min(int(np.searchsorted(edges, x, side="right")) - 1, len(layers) - 1)
        layer = max(layer, 0)
        result[index] = surface - drops[layer] - flux * (x - edges[layer]) \
            / layers[layer].conductivity_w_mk
    return result


def wall_deck(layers: Sequence[Layer], inside_c: float, outside_c: float, inside_h: float,
              outside_h: float, step_m: float) -> tuple[str, _Column]:
    xs = np.asarray(layered_axis(0.0, layers, 1, step_m))
    column = _Column(xs, np.asarray([0.0, SECTION_M]), np.asarray([0.0, SECTION_M]))
    edges = np.concatenate([[0.0], np.cumsum([layer.thickness_m for layer in layers])])
    centres = 0.5 * (xs[:-1] + xs[1:])
    material_of = [min(int(np.searchsorted(edges, c)), len(layers)) for c in centres]
    lines = ["/BATCH", "/PREP7", "ET,1,SOLID278", "ET,2,SURF152", "KEYOPT,2,4,1",
             "KEYOPT,2,5,1", "KEYOPT,2,8,2"]
    for index, layer in enumerate(layers, start=1):
        lines.append(f"MP,KXX,{index},{_fmt(layer.conductivity_w_mk)}")
    lines += _mesh_lines(column, material_of)
    total = thickness(layers)
    inside_node, outside_node = column.count + 1, column.count + 2
    tol = 1e-7
    lines += [f"N,{inside_node},{_fmt(-1.0)},0,0", f"N,{outside_node},{_fmt(total + 1.0)},0,0",
              "TYPE,2", "R,10", "REAL,10",
              f"NSEL,S,LOC,X,{_fmt(-tol)},{_fmt(tol)}", "ESLN,S,0", "ESEL,R,TYPE,,1",
              "*GET,EBEFORE,ELEM,0,NUM,MAXD", f"ESURF,{inside_node}",
              "*GET,EAFTER,ELEM,0,NUM,MAXD", "ESEL,S,ELEM,,EBEFORE+1,EAFTER",
              f"SFE,ALL,1,CONV,0,{_fmt(inside_h)}", "ALLSEL",
              f"NSEL,S,LOC,X,{_fmt(total - tol)},{_fmt(total + tol)}", "ESLN,S,0",
              "ESEL,R,TYPE,,1", "*GET,EBEFORE,ELEM,0,NUM,MAXD", f"ESURF,{outside_node}",
              "*GET,EAFTER,ELEM,0,NUM,MAXD", "ESEL,S,ELEM,,EBEFORE+1,EAFTER",
              f"SFE,ALL,1,CONV,0,{_fmt(outside_h)}", "ALLSEL",
              f"D,{inside_node},TEMP,{_fmt(inside_c)}",
              f"D,{outside_node},TEMP,{_fmt(outside_c)}", "FINISH", "/SOLU",
              "ANTYPE,STATIC", "SOLVE", "FINISH", "/POST1", "SET,LAST"]
    lines += _write_nodes(column.count)
    return "\n".join(lines) + "\n", column


def step_profile_c(depth_m: np.ndarray, seconds: float, diffusivity_m2_s: float,
                   initial_c: float, surface_c: float) -> np.ndarray:
    from scipy.special import erf

    return surface_c + (initial_c - surface_c) * erf(
        np.asarray(depth_m, dtype=float) / (2.0 * math.sqrt(diffusivity_m2_s * seconds)))


def step_deck(layer: Layer, depth_m: float, first_m: float, initial_c: float,
              surface_c: float, hours: int, substeps: int) -> tuple[str, _Column]:
    from .mapdl import graded

    xs = np.asarray(graded(0.0, depth_m, first_m, 0.1, growth=1.15))
    column = _Column(xs, np.asarray([0.0, SECTION_M]), np.asarray([0.0, SECTION_M]))
    tol = 1e-7
    lines = ["/BATCH", "/PREP7", "ET,1,SOLID278", f"MP,KXX,1,{_fmt(layer.conductivity_w_mk)}",
             f"MP,DENS,1,{_fmt(layer.density_kg_m3)}",
             f"MP,C,1,{_fmt(layer.specific_heat_j_kgk)}"]
    lines += _mesh_lines(column, [1] * (len(xs) - 1))
    lines += ["FINISH", "/SOLU", "ANTYPE,TRANS", "TRNOPT,FULL", "OUTRES,NSOL,LAST",
              "KBC,1", "TIMINT,ON", f"IC,ALL,TEMP,{_fmt(initial_c)}",
              f"NSEL,S,LOC,X,{_fmt(-tol)},{_fmt(tol)}", f"D,ALL,TEMP,{_fmt(surface_c)}",
              "ALLSEL"]
    for hour in range(1, hours + 1):
        lines += [f"TIME,{hour * 3600}", f"NSUBST,{substeps},{substeps},{substeps}",
                  "SOLVE"]
    lines += ["FINISH", "/POST1", "SET,LAST"]
    lines += _write_nodes(column.count)
    return "\n".join(lines) + "\n", column


def check_rectangle(workdir: Path, executable: Path | None = None, cells: int = 40,
                    timeout_s: float = 600.0) -> CheckResult:
    width = height = 1.0
    top = 20.0
    text, column = rectangle_deck(width, height, top, cells)
    path, runtime = run_text(text, workdir, RESULT_NAME, executable, 1, timeout_s)
    temperatures = _read_nodes(path, column.count)
    points = column.coordinates()
    probe = np.round(np.arange(0.125, 1.0, 0.125), 6)
    keep = (np.isin(np.round(points[:, 0], 6), probe)
            & np.isin(np.round(points[:, 2], 6), probe) & (points[:, 1] == 0.0))
    if not keep.any():
        raise EngineError("the rectangle mesh does not reach the probe points; use a cell "
                          "count divisible by 8")
    exact = rectangle_series_c(points[keep, 0], points[keep, 2], width, height, top)
    error = float(np.max(np.abs(temperatures[keep] - exact)))
    return CheckResult(
        name="rectangle",
        description=(f"Steady conduction in a 1 m square held at {top:.0f} C on one side "
                     "and 0 C on the other three, against its Fourier series at 49 points "
                     f"on a {cells} by {cells} mesh"),
        points=int(keep.sum()), max_abs_k=error, tolerance_k=RECTANGLE_TOLERANCE_K,
        runtime_s=runtime)


def check_wall(layers: Sequence[Layer], workdir: Path, executable: Path | None = None,
               step_m: float = 0.05, timeout_s: float = 600.0) -> CheckResult:
    if not layers:
        raise EngineError("the wall check needs at least one layer")
    inside, outside, h_in, h_out = 20.0, -20.0, 1 / 0.13, 25.0
    text, column = wall_deck(layers, inside, outside, h_in, h_out, step_m)
    path, runtime = run_text(text, workdir, RESULT_NAME, executable, 1, timeout_s)
    temperatures = _read_nodes(path, column.count)
    points = column.coordinates()
    exact = wall_profile_c(points[:, 0], layers, inside, outside, h_in, h_out)
    error = float(np.max(np.abs(temperatures - exact)))
    return CheckResult(
        name="wall",
        description=(f"Steady heat through the {len(layers)} layer wall with ISO 6946 "
                     "films, 20 C inside and -20 C outside, against the series "
                     "resistance profile at every node"),
        points=column.count, max_abs_k=error, tolerance_k=WALL_TOLERANCE_K,
        runtime_s=runtime)


def check_step(layer: Layer, workdir: Path, executable: Path | None = None,
               hours: int = 24, substeps: int = 2, timeout_s: float = 600.0) -> CheckResult:
    diffusivity = layer.conductivity_w_mk / (layer.density_kg_m3 * layer.specific_heat_j_kgk)
    reach = 2.0 * math.sqrt(diffusivity * hours * 3600.0)
    depth = max(8.0 * reach, 0.5)
    initial, surface = 0.0, 20.0
    text, column = step_deck(layer, depth, 0.01, initial, surface, hours, substeps)
    path, runtime = run_text(text, workdir, RESULT_NAME, executable, 1, timeout_s)
    temperatures = _read_nodes(path, column.count)
    points = column.coordinates()
    exact = step_profile_c(points[:, 0], hours * 3600.0, diffusivity, initial, surface)
    inside = points[:, 0] > 1e-9
    error = float(np.max(np.abs(temperatures[inside] - exact[inside])))
    return CheckResult(
        name="step",
        description=(f"A {surface - initial:.0f} K step on the face of deep "
                     f"{layer.material_id}, after {hours} hourly steps of {substeps} "
                     "substeps, against the error function solution"),
        points=int(inside.sum()), max_abs_k=error, tolerance_k=STEP_TOLERANCE_K,
        runtime_s=runtime)
