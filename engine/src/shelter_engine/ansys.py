from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from .errors import ConfigurationError, ResultsUnavailable, SimulationFailed
from .schema import CFDExport, CFDField, CFDSolverRecord, SurfaceKind

FIRST_CELL_M = 0.01
LARGEST_CELL_M = 0.20
GROWTH = 1.2
CELL_LIMIT = 1_000_000
PLANE_TOLERANCE_M = 1e-4
MIN_SPACING_M = 1e-4
HEX = 5
QUAD = 3


@dataclass(frozen=True)
class RoomFrame:
    origin: np.ndarray
    axes: np.ndarray
    extent: tuple[float, float, float]

    def to_local(self, points: np.ndarray) -> np.ndarray:
        return (np.asarray(points, dtype=float) - self.origin) @ self.axes.T

    def to_world(self, points: np.ndarray) -> np.ndarray:
        return self.origin + np.asarray(points, dtype=float) @ self.axes


@dataclass(frozen=True)
class Patch:
    name: str
    kind: SurfaceKind
    temperature_c: float
    vertices: np.ndarray
    normal: np.ndarray

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.vertices.min(axis=0), self.vertices.max(axis=0)


@dataclass(frozen=True)
class RoomMesh:
    frame: RoomFrame
    axes: tuple[np.ndarray, np.ndarray, np.ndarray]
    nodes: np.ndarray
    hexes: np.ndarray
    faces: np.ndarray
    face_patch: np.ndarray
    patches: tuple[Patch, ...]

    @property
    def cell_count(self) -> int:
        return int(len(self.hexes))

    @property
    def node_count(self) -> int:
        return int(len(self.nodes))

    @property
    def volume_m3(self) -> float:
        du, dv, dw = (np.diff(axis) for axis in self.axes)
        return float(du.sum() * dv.sum() * dw.sum())

    def patch_faces(self, index: int) -> np.ndarray:
        return self.faces[self.face_patch == index]

    def patch_area_m2(self, index: int) -> float:
        corners = self.nodes[self.patch_faces(index) - 1]
        diagonal_a = corners[:, 2] - corners[:, 0]
        diagonal_b = corners[:, 3] - corners[:, 1]
        return float(0.5 * np.linalg.norm(np.cross(diagonal_a, diagonal_b), axis=1).sum())


def graded_axis(length: float, first: float = FIRST_CELL_M,
                largest: float = LARGEST_CELL_M, growth: float = GROWTH) -> np.ndarray:
    if not (math.isfinite(length) and length > 0.0):
        raise ResultsUnavailable("an axis to mesh needs a positive finite length")
    if not (0.0 < first <= largest) or growth < 1.0:
        raise ResultsUnavailable("cell sizes must satisfy 0 < first <= largest and growth >= 1")
    sizes: list[float] = []
    size = first
    while sum(sizes) + size <= length / 2.0:
        sizes.append(size)
        size = min(size * growth, largest)
    while sizes and length - 2.0 * sum(sizes) < 0.5 * sizes[-1]:
        sizes.pop()
    half = np.concatenate([[0.0], np.cumsum(sizes)])
    gap = length - 2.0 * half[-1]
    count = max(1, math.ceil(gap / largest - 1e-9))
    middle = half[-1] + np.linspace(0.0, gap, count + 1)[1:-1]
    return np.concatenate([half, middle, length - half[::-1]])


def insert_cuts(nodes: np.ndarray, cuts: Sequence[float],
                tolerance: float = 1e-6) -> np.ndarray:
    values = [float(value) for value in nodes]
    if len(values) < 2:
        raise ResultsUnavailable("an axis needs at least two nodes before cuts are inserted")
    protected = {0, len(values) - 1}
    for cut in sorted(float(item) for item in cuts):
        if not values[0] + tolerance < cut < values[-1] - tolerance:
            continue
        array = np.asarray(values)
        index = int(np.searchsorted(array, cut))
        if abs(array[index] - cut) <= tolerance:
            protected.add(index)
            continue
        if abs(array[index - 1] - cut) <= tolerance:
            protected.add(index - 1)
            continue
        below, above = array[index - 1], array[index]
        nearest = index - 1 if cut - below < above - cut else index
        if abs(array[nearest] - cut) < 0.35 * (above - below) and nearest not in protected:
            values[nearest] = cut
            protected.add(nearest)
        else:
            values.insert(index, cut)
            protected = {item + 1 if item >= index else item for item in protected}
            protected.add(index)
    result = np.asarray(values)
    spacing = np.diff(result)
    if spacing.min() < MIN_SPACING_M:
        raise ResultsUnavailable(
            f"two surface edges sit {spacing.min() * 1000:.3f} mm apart, closer than the "
            "mesher can resolve; the boundary geometry needs cleaning before a CFD run")
    return result


def _newell(points: np.ndarray) -> np.ndarray:
    normal = np.zeros(3)
    for start, end in zip(points, np.roll(points, -1, axis=0)):
        normal += np.cross(start, end)
    length = np.linalg.norm(normal)
    if length <= 0.0:
        raise ResultsUnavailable("a boundary surface has no area")
    return normal / length


def patches_of(export: CFDExport) -> tuple[Patch, ...]:
    patches = []
    for surface in export.surfaces:
        points = np.asarray(surface.positions, dtype=float).reshape(-1, 3)
        if len(points) < 3:
            raise ResultsUnavailable(f"{surface.name} has fewer than three vertices")
        patches.append(Patch(name=surface.name, kind=surface.kind,
                             temperature_c=float(surface.inside_face_temperature_c),
                             vertices=points, normal=_newell(points)))
    names = [patch.name for patch in patches]
    if len(set(names)) != len(names):
        raise ResultsUnavailable("boundary surface names must be unique")
    return tuple(patches)


def room_frame(patches: Sequence[Patch]) -> RoomFrame:
    floors = [patch for patch in patches if patch.kind is SurfaceKind.FLOOR]
    if len(floors) != 1 or len(floors[0].vertices) != 4:
        raise ResultsUnavailable(
            "the CFD mesher handles a rectangular room with one four cornered floor")
    corners = floors[0].vertices
    origin = corners[0]
    edge_u = corners[1] - origin
    edge_v = corners[3] - origin
    if abs(edge_u[2]) > PLANE_TOLERANCE_M or abs(edge_v[2]) > PLANE_TOLERANCE_M:
        raise ResultsUnavailable("the floor is not level")
    if abs(np.dot(edge_u, edge_v)) > 1e-6 * np.linalg.norm(edge_u) * np.linalg.norm(edge_v) \
            or not np.allclose(corners[2], corners[1] + edge_v, atol=1e-4):
        raise ResultsUnavailable("the floor is not a rectangle")
    if np.cross(edge_u, edge_v)[2] < 0.0:
        edge_u, edge_v = edge_v, edge_u
    axis_u = edge_u / np.linalg.norm(edge_u)
    axis_v = edge_v / np.linalg.norm(edge_v)
    axes = np.vstack([axis_u, axis_v, [0.0, 0.0, 1.0]])
    top = max(float(patch.vertices[:, 2].max()) for patch in patches)
    height = top - float(origin[2])
    if height <= 0.0:
        raise ResultsUnavailable("the room has no height")
    frame = RoomFrame(origin=origin, axes=axes,
                      extent=(float(np.linalg.norm(edge_u)), float(np.linalg.norm(edge_v)),
                              height))
    for patch in patches:
        local = frame.to_local(patch.vertices)
        on_plane = False
        for axis in range(3):
            for level in (0.0, frame.extent[axis]):
                if np.all(np.abs(local[:, axis] - level) <= PLANE_TOLERANCE_M * 10):
                    on_plane = True
        inside = np.all(local >= -PLANE_TOLERANCE_M * 10) and all(
            np.all(local[:, axis] <= frame.extent[axis] + PLANE_TOLERANCE_M * 10)
            for axis in range(3))
        if not (on_plane and inside):
            raise ResultsUnavailable(
                f"{patch.name} does not lie on a face of the rectangular room, so the room "
                "is not a box and the CFD mesher cannot represent it")
    return frame


def _contains(patch: Patch, points: np.ndarray) -> np.ndarray:
    distance = np.abs((points - patch.vertices[0]) @ patch.normal)
    inside = distance <= PLANE_TOLERANCE_M * 10
    tolerance = 1e-9
    signs = []
    for start, end in zip(patch.vertices, np.roll(patch.vertices, -1, axis=0)):
        signs.append(np.cross(end - start, points - start) @ patch.normal)
    signs = np.column_stack(signs)
    return inside & (np.all(signs >= -tolerance, axis=1) | np.all(signs <= tolerance, axis=1))


def _boundary_faces(counts: tuple[int, int, int]) -> np.ndarray:
    nu, nv, nw = counts

    def tag(i, j, k):
        return k * nu * nv + j * nu + i + 1

    blocks = []
    for axis, index in ((0, 0), (0, nu - 1), (1, 0), (1, nv - 1), (2, 0), (2, nw - 1)):
        ranges = [np.arange(n - 1) for n in counts]
        ranges[axis] = np.array([index])
        a, b, c = (item.ravel() for item in np.meshgrid(*ranges, indexing="ij"))
        low = index == 0
        if axis == 0:
            order = ([tag(a, b, c), tag(a, b, c + 1), tag(a, b + 1, c + 1), tag(a, b + 1, c)]
                     if low else
                     [tag(a, b, c), tag(a, b + 1, c), tag(a, b + 1, c + 1), tag(a, b, c + 1)])
        elif axis == 1:
            order = ([tag(a, b, c), tag(a + 1, b, c), tag(a + 1, b, c + 1), tag(a, b, c + 1)]
                     if low else
                     [tag(a, b, c), tag(a, b, c + 1), tag(a + 1, b, c + 1), tag(a + 1, b, c)])
        else:
            order = ([tag(a, b, c), tag(a, b + 1, c), tag(a + 1, b + 1, c), tag(a + 1, b, c)]
                     if low else
                     [tag(a, b, c), tag(a + 1, b, c), tag(a + 1, b + 1, c), tag(a, b + 1, c)])
        blocks.append(np.column_stack(order))
    return np.vstack(blocks)


def build_room_mesh(export: CFDExport, first: float = FIRST_CELL_M,
                    largest: float = LARGEST_CELL_M, growth: float = GROWTH,
                    cell_limit: int = CELL_LIMIT) -> RoomMesh:
    patches = patches_of(export)
    frame = room_frame(patches)
    cuts: list[set[float]] = [set(), set(), set()]
    for patch in patches:
        local = frame.to_local(patch.vertices)
        for axis in range(3):
            cuts[axis].update(round(float(value), 6) for value in local[:, axis])
    axes = tuple(insert_cuts(graded_axis(frame.extent[axis], first, largest, growth),
                             sorted(cuts[axis])) for axis in range(3))
    counts = tuple(len(axis) for axis in axes)
    cells = (counts[0] - 1) * (counts[1] - 1) * (counts[2] - 1)
    if cells > cell_limit:
        raise ResultsUnavailable(
            f"the room mesh would have {cells:,} cells, above the {cell_limit:,} cell limit; "
            "raise the largest cell size or the growth rate")

    grid_u, grid_v, grid_w = np.meshgrid(*axes, indexing="ij")
    local = np.column_stack([grid_u.ravel(order="F"), grid_v.ravel(order="F"),
                             grid_w.ravel(order="F")])
    nodes = frame.to_world(local)

    nu, nv, nw = counts
    i, j, k = (item.ravel() for item in np.meshgrid(
        np.arange(nu - 1), np.arange(nv - 1), np.arange(nw - 1), indexing="ij"))

    def tag(a, b, c):
        return c * nu * nv + b * nu + a + 1

    hexes = np.column_stack([tag(i, j, k), tag(i + 1, j, k), tag(i + 1, j + 1, k),
                             tag(i, j + 1, k), tag(i, j, k + 1), tag(i + 1, j, k + 1),
                             tag(i + 1, j + 1, k + 1), tag(i, j + 1, k + 1)])
    faces = _boundary_faces(counts)
    centroids = nodes[faces - 1].mean(axis=1)

    assignment = np.full(len(faces), -1, dtype=int)
    order = sorted(range(len(patches)),
                   key=lambda index: 0 if patches[index].kind is SurfaceKind.WINDOW else 1)
    for index in order:
        free = assignment < 0
        hit = _contains(patches[index], centroids[free])
        chosen = np.flatnonzero(free)[hit]
        assignment[chosen] = index
    missing = int((assignment < 0).sum())
    if missing:
        raise ResultsUnavailable(
            f"{missing} boundary faces of the room are covered by no exported surface, so "
            "the boundary conditions do not close the room")
    empty = [patches[index].name for index in range(len(patches))
             if not np.any(assignment == index)]
    if empty:
        raise ResultsUnavailable(f"these surfaces received no mesh faces: {', '.join(empty)}")
    return RoomMesh(frame=frame, axes=axes, nodes=nodes, hexes=hexes, faces=faces,
                    face_patch=assignment, patches=patches)


def write_mesh(mesh: RoomMesh, path: str | Path) -> Path:
    import gmsh

    target = Path(path)
    if target.suffix.lower() not in (".bdf", ".cgns", ".inp", ".msh"):
        raise ResultsUnavailable(f"cannot write a room mesh as {target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("room")
        volume = gmsh.model.addDiscreteEntity(3)
        gmsh.model.mesh.addNodes(3, volume, np.arange(1, mesh.node_count + 1),
                                 mesh.nodes.ravel())
        gmsh.model.mesh.addElementsByType(volume, HEX, [], mesh.hexes.ravel())
        gmsh.model.addPhysicalGroup(3, [volume], name="fluid")
        for index, patch in enumerate(mesh.patches):
            entity = gmsh.model.addDiscreteEntity(2)
            gmsh.model.mesh.addElementsByType(entity, QUAD, [],
                                              mesh.patch_faces(index).ravel())
            gmsh.model.addPhysicalGroup(2, [entity], name=patch.name.lower())
        gmsh.write(str(target))
    finally:
        gmsh.finalize()
    return target


KELVIN = 273.15
GRAVITY_M_S2 = 9.81
RESIDUAL_ROW = re.compile(r"^\s*(\d+)((?:\s+[0-9.]+e[+-]\d+){7})", re.M)
RESIDUAL_HEADER = re.compile(r"^\s*iter\s+(.+?)\s+time/iter", re.M)
VERSION_FOLDER = re.compile(r"v(\d{3})")


@dataclass(frozen=True)
class FluentOptions:
    processor_count: int = 4
    chunk_iterations: int = 50
    min_iterations: int = 300
    max_iterations: int = 2000
    stable_chunks: int = 4
    drift_tolerance_k: float = 0.02
    band_tolerance_k: float = 0.1
    heat_balance_fraction: float = 0.05
    heat_balance_floor_w: float = 10.0
    start_timeout_s: int = 600
    first_cell_m: float = FIRST_CELL_M
    largest_cell_m: float = LARGEST_CELL_M
    growth: float = GROWTH
    cell_limit: int = CELL_LIMIT

    def __post_init__(self) -> None:
        if self.processor_count < 1:
            raise ConfigurationError("Fluent needs at least one processor")
        if self.chunk_iterations < 1 or self.max_iterations < self.chunk_iterations:
            raise ConfigurationError("iterations must be positive and at least one chunk")
        if self.min_iterations < 0 or self.min_iterations > self.max_iterations:
            raise ConfigurationError("min_iterations must lie between 0 and max_iterations")
        if self.stable_chunks < 2:
            raise ConfigurationError("stability needs at least two monitored chunks")
        if self.drift_tolerance_k <= 0.0 or self.band_tolerance_k <= 0.0 \
                or self.heat_balance_fraction <= 0.0 or self.heat_balance_floor_w < 0.0:
            raise ConfigurationError("convergence tolerances must be positive")


@dataclass(frozen=True)
class ChunkRecord:
    iteration: int
    mean_air_c: float
    wall_heat_w: float | None = None


def judge_convergence(history: Sequence[ChunkRecord], net_source_w: float | None,
                      options: FluentOptions) -> tuple[bool, str]:
    if not history:
        return False, "no iterations were run"
    last = history[-1]
    if last.iteration < options.min_iterations:
        return False, (f"only {last.iteration} iterations were run, below the "
                       f"{options.min_iterations} minimum")
    count = options.stable_chunks
    if len(history) < 2 * count:
        return False, "too few monitored chunks to judge stability"
    recent = history[-count:]
    earlier = history[-2 * count:-count]
    temperatures = [item.mean_air_c for item in earlier + recent]
    if not all(math.isfinite(value) for value in temperatures):
        return False, "the mean air temperature is not finite"
    drift = abs(float(np.mean(temperatures[count:])) - float(np.mean(temperatures[:count])))
    band = max(temperatures) - min(temperatures)
    span = recent[-1].iteration - earlier[0].iteration
    if drift > options.drift_tolerance_k:
        return False, (f"the volume average air temperature is still drifting, "
                       f"{drift:.3f} K between the last two windows of {span} iterations")
    if band > options.band_tolerance_k:
        return False, (f"the volume average air temperature still swings over {band:.3f} K "
                       f"across the last {span} iterations")
    stability = (f"over the last {span} iterations the volume average air temperature "
                 f"drifted {drift:.3f} K and stayed within a {band:.3f} K band")
    heats = [item.wall_heat_w for item in recent
             if item.wall_heat_w is not None and math.isfinite(item.wall_heat_w)]
    if net_source_w is not None and len(heats) == count:
        wall_heat = float(np.mean(heats))
        imbalance = wall_heat + net_source_w
        allowed = max(options.heat_balance_floor_w,
                      options.heat_balance_fraction * abs(net_source_w))
        if abs(imbalance) > allowed:
            return False, (f"heat through the walls ({wall_heat:.1f} W) and the air heat "
                           f"source ({net_source_w:.1f} W) leave {imbalance:.1f} W "
                           "unbalanced")
        return True, f"{stability}, and the air heat balance closed to {imbalance:.1f} W"
    return True, f"{stability}; the wall heat balance could not be evaluated"


def cell_volumes(mesh: RoomMesh, centroids: np.ndarray,
                 tolerance_m: float | None = None) -> np.ndarray:
    from scipy.spatial import cKDTree

    if tolerance_m is None:
        tolerance_m = 0.25 * min(float(np.diff(axis).min()) for axis in mesh.axes)
    points = np.asarray(centroids, dtype=float).reshape(-1, 3)
    if len(points) != mesh.cell_count:
        raise ResultsUnavailable(
            f"Fluent reports {len(points):,} cells but the room mesh has "
            f"{mesh.cell_count:,}")
    middles = [0.5 * (axis[1:] + axis[:-1]) for axis in mesh.axes]
    sizes = [np.diff(axis) for axis in mesh.axes]
    local = np.column_stack([grid.ravel() for grid in np.meshgrid(*middles, indexing="ij")])
    size = np.prod(np.column_stack([grid.ravel()
                                    for grid in np.meshgrid(*sizes, indexing="ij")]), axis=1)
    distance, index = cKDTree(mesh.frame.to_world(local)).query(points, k=1)
    if float(distance.max()) > tolerance_m:
        raise ResultsUnavailable(
            f"a Fluent cell centre sits {float(distance.max()) * 1000:.3f} mm from every mesh "
            "cell, so the solved cells do not belong to this room mesh")
    if len(np.unique(index)) != len(index):
        raise ResultsUnavailable("two Fluent cells map onto the same mesh cell")
    return size[index]


def match_zone(vertices: np.ndarray, patches: Sequence[Patch],
               tolerance_m: float = 2e-3) -> int:
    points = np.asarray(vertices, dtype=float).reshape(-1, 3)
    if len(points) == 0:
        raise ResultsUnavailable("a Fluent wall zone reported no vertices")
    low, high = points.min(axis=0), points.max(axis=0)
    hits = [index for index, patch in enumerate(patches)
            if np.allclose(patch.bounds[0], low, atol=tolerance_m)
            and np.allclose(patch.bounds[1], high, atol=tolerance_m)]
    if len(hits) != 1:
        raise ResultsUnavailable(
            f"a Fluent wall zone spanning {np.round(low, 3).tolist()} to "
            f"{np.round(high, 3).tolist()} matches {len(hits)} exported surfaces")
    return hits[0]


def parse_residuals(text: str) -> tuple[int, dict[str, float]] | None:
    rows = RESIDUAL_ROW.findall(text)
    if not rows:
        return None
    number, raw = rows[-1]
    values = [float(value) for value in raw.split()]
    headers = RESIDUAL_HEADER.findall(text)
    names = headers[-1].split() if headers else []
    if len(names) != len(values):
        names = [f"residual_{index}" for index in range(len(values))]
    return int(number), dict(zip(names, values))


def _registry_roots() -> list[str]:
    try:
        import winreg
    except ImportError:
        return []
    roots: list[str] = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    except OSError:
        return []
    with key:
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            if re.fullmatch(r"AWP_ROOT\d+", name, re.I):
                roots.append((name, value))
            index += 1
    return [value for _, value in sorted(roots, reverse=True)]


def find_ansys_root(environ: dict[str, str] | None = None,
                    registry: Callable[[], list[str]] = _registry_roots) -> Path:
    env = dict(os.environ) if environ is None else environ
    candidates: list[str] = []
    if env.get("SHELTER_ANSYS_ROOT"):
        candidates.append(env["SHELTER_ANSYS_ROOT"])
    candidates.extend(env[name] for name in sorted(
        (name for name in env if re.fullmatch(r"AWP_ROOT\d+", name, re.I)), reverse=True))
    if not candidates:
        candidates.extend(registry())
    for candidate in candidates:
        root = Path(candidate)
        if (root / "fluent").is_dir():
            return root
    raise ConfigurationError(
        "ANSYS Fluent was not found. Set SHELTER_ANSYS_ROOT to the version folder of the "
        r"installation, for example R:\ANSYS Inc\ANSYS Student\v261")


def _prepare_environment(root: Path) -> None:
    match = VERSION_FOLDER.fullmatch(root.name)
    if match is None:
        raise ConfigurationError(
            f"{root} is not an ANSYS version folder such as v261, so the Fluent release "
            "cannot be identified")
    os.environ.setdefault(f"AWP_ROOT{match.group(1)}", str(root))


def _first_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, dict):
        items = value.values()
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return None
    for item in items:
        found = _first_number(item)
        if found is not None:
            return found
    return None


def _cell_data(solver, zone: str, variable: str) -> np.ndarray:
    data = solver.fields.solution_variable_data.get_data(
        variable_name=variable, zone_names=[zone], domain_name="mixture")
    values = data[zone]
    if values is None:
        raise ResultsUnavailable(f"Fluent returned no {variable} for zone {zone}")
    return np.asarray(values, dtype=float)


def _wall_heat_report(settings, walls: Sequence[str]) -> str | None:
    try:
        flux = settings.solution.report_definitions.flux
        if "wall_heat" not in flux.get_object_names():
            flux.create("wall_heat")
        definition = flux["wall_heat"]
        key = next((item for item in ("boundaries", "zones", "zone_names")
                    if item in definition.child_names), None)
        kind = next((item for item in definition.report_type.allowed_values()
                     if "heat" in str(item).lower()), None)
        if key is None or kind is None:
            return None
        definition.report_type = kind
        getattr(definition, key).set_state(list(walls))
        return "wall_heat"
    except Exception:
        return None


def wall_heat_from_report(result) -> float | None:
    entries: dict[str, float] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                number = _first_number(value) if isinstance(value, (list, tuple)) else None
                if number is not None:
                    entries[str(key)] = number
                elif isinstance(value, (dict, list, tuple)):
                    walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(result)
    for key, value in entries.items():
        if key.endswith("(without-sources)"):
            return value
    if len(entries) == 1:
        return next(iter(entries.values()))
    return None


def _wall_heat(settings, report: str | None) -> float | None:
    if report is None:
        return None
    try:
        return wall_heat_from_report(
            settings.solution.report_definitions.compute(report_defs=[report]))
    except Exception:
        return None


def _apply_physics(settings, export: CFDExport, mesh: RoomMesh,
                   assignment: dict[str, int], fluid: str, net_source_w: float) -> None:
    setup = settings.setup
    setup.models.energy.enabled = True
    setup.models.viscous.model = "k-omega"
    setup.models.viscous.k_omega_model = "sst"
    operating = setup.general.operating_conditions
    operating.gravity.set_state({"enable": True, "components": [0.0, 0.0, -GRAVITY_M_S2]})
    operating.operating_pressure = float(export.air_pressure_pa)
    operating.operating_temperature = float(export.zone_air_temperature_c) + KELVIN
    setup.materials.fluid["air"].density.option = "incompressible-ideal-gas"
    methods = settings.solution.methods
    methods.spatial_discretization.discretization_scheme["pressure"] = "body-force-weighted"
    methods.expert.implicit_bodyforce_treatment = True
    for zone, index in assignment.items():
        wall = setup.boundary_conditions.wall[zone]
        wall.thermal.thermal_condition = "Temperature"
        wall.thermal.temperature.set_state(
            {"option": "value", "value": mesh.patches[index].temperature_c + KELVIN})
    sources = setup.cell_zone_conditions.fluid[fluid].sources
    sources.enable = True
    sources.terms.set_state({"energy": [{"option": "value",
                                         "value": net_source_w / mesh.volume_m3}]})


def _version(solver) -> str:
    try:
        text = str(solver.get_fluent_version())
    except Exception:
        return "unknown release"
    return text.replace("Ansys Fluent", "").replace("ANSYS Fluent", "").strip() or text


def _transcript(directory: Path, since: float) -> str:
    files = [path for path in directory.glob("fluent-*.trn")
             if path.stat().st_mtime >= since - 5.0]
    if not files:
        return ""
    return max(files, key=lambda path: path.stat().st_mtime).read_text(errors="ignore")


def run_fluent(export: CFDExport, workdir: str | Path,
               options: FluentOptions | None = None,
               progress: Callable[[str], None] | None = None) -> CFDField:
    from .cfd import build_cfd_field

    options = options or FluentOptions()
    say = progress or (lambda message: None)
    if export.air_pressure_pa is None or not export.air_pressure_pa > 0.0:
        raise ResultsUnavailable(
            "the boundary conditions carry no air pressure, and air density at altitude "
            "cannot be assumed; export them again from the engine")
    net_source_w = export.net_air_heat_source_w
    if net_source_w is None:
        raise ResultsUnavailable(
            "the boundary conditions carry no internal gain, infiltration or mixing heat "
            "flow, so the air heat balance EnergyPlus solved cannot be reproduced; export "
            "them again from the engine")

    target = Path(workdir)
    target.mkdir(parents=True, exist_ok=True)
    stem = f"{export.design_hash}_{export.label}"
    mesh = build_room_mesh(export, options.first_cell_m, options.largest_cell_m,
                           options.growth, options.cell_limit)
    mesh_path = write_mesh(mesh, target / f"{stem}.bdf")
    say(f"{stem}: {mesh.cell_count:,} cell mesh written")
    _prepare_environment(find_ansys_root())

    import ansys.fluent.core as pyfluent

    started_at = time.time()
    try:
        solver = pyfluent.launch_fluent(
            mode=pyfluent.FluentMode.SOLVER, dimension=pyfluent.Dimension.THREE,
            precision=pyfluent.Precision.DOUBLE, processor_count=options.processor_count,
            ui_mode=pyfluent.UIMode.NO_GUI, start_timeout=options.start_timeout_s,
            cwd=str(target))
    except Exception as error:
        raise SimulationFailed(f"ANSYS Fluent did not start: {error}") from error

    try:
        settings = solver.settings
        solver.tui.file.import_.nastran.bulkdata(str(mesh_path))
        zones = settings.setup.cell_zone_conditions
        if not list(zones.fluid.get_object_names()):
            for name in list(zones.solid.get_object_names()):
                solver.tui.define.boundary_conditions.zone_type(name, "fluid")
        fluids = list(zones.fluid.get_object_names())
        if len(fluids) != 1:
            raise SimulationFailed(f"expected one air zone after import, found {fluids}")
        fluid = fluids[0]
        walls = list(settings.setup.boundary_conditions.wall.get_object_names())
        assignment: dict[str, int] = {}
        for zone in walls:
            request = pyfluent.SurfaceFieldDataRequest(
                surfaces=[zone], data_types=[pyfluent.SurfaceDataType.Vertices])
            block = solver.fields.field_data.get_field_data(request)[zone]
            vertices = block[pyfluent.SurfaceDataType.Vertices] \
                if isinstance(block, dict) else block.vertices
            assignment[zone] = match_zone(vertices, mesh.patches)
        if sorted(assignment.values()) != list(range(len(mesh.patches))):
            raise SimulationFailed(
                "the Fluent wall zones do not correspond one to one with the exported "
                "surfaces")

        _apply_physics(settings, export, mesh, assignment, fluid, net_source_w)
        settings.solution.initialization.hybrid_initialize()
        centroids = _cell_data(solver, fluid, "SV_CENTROID").reshape(-1, 3)
        volumes = cell_volumes(mesh, centroids)
        report = _wall_heat_report(settings, walls)

        history: list[ChunkRecord] = []
        converged, basis = False, "no iterations were run"
        iteration = 0
        while iteration < options.max_iterations:
            settings.solution.run_calculation.iterate(iter_count=options.chunk_iterations)
            iteration += options.chunk_iterations
            temperature = _cell_data(solver, fluid, "SV_T")
            mean = float(np.dot(temperature, volumes) / volumes.sum()) - KELVIN
            history.append(ChunkRecord(iteration, mean, _wall_heat(settings, report)))
            say(f"{stem}: iteration {iteration}, mean air {mean:.3f} C, wall heat "
                f"{history[-1].wall_heat_w if history[-1].wall_heat_w is not None else 'n/a'}")
            converged, basis = judge_convergence(history, net_source_w, options)
            if converged:
                break

        temperature_c = _cell_data(solver, fluid, "SV_T") - KELVIN
        speed = np.sqrt(sum(_cell_data(solver, fluid, name) ** 2
                            for name in ("SV_U", "SV_V", "SV_W")))
        parsed = parse_residuals(_transcript(target, started_at))
        iterations, residuals = parsed if parsed else (iteration, {})
        version = _version(solver)
        settings.file.write(file_type="case-data", file_name=str(target / f"{stem}.cas.h5"))
    except (ResultsUnavailable, SimulationFailed):
        raise
    except Exception as error:
        raise SimulationFailed(
            f"ANSYS Fluent failed on {stem}: {type(error).__name__}: {error}",
            run_dir=str(target)) from error
    finally:
        try:
            solver.exit()
        except Exception:
            pass

    mean_air_c = float(np.dot(temperature_c, volumes) / volumes.sum())
    record = CFDSolverRecord(
        solver="ANSYS Fluent", version=version, turbulence_model="k-omega SST",
        buoyancy_model="full buoyancy, incompressible ideal gas at the site pressure",
        radiation_model="none, surfaces held at the EnergyPlus inside face temperatures",
        operating_pressure_pa=float(export.air_pressure_pa), cell_count=mesh.cell_count,
        iterations=int(iterations), converged=converged, residuals=residuals)
    notes = (
        "Convergence was judged on the solution rather than on scaled residuals, which "
        f"plateau for air moved only by surface temperature differences: {basis}.",
        f"Volume average air temperature {mean_air_c:.2f} C against the "
        f"{export.zone_air_temperature_c:.2f} C EnergyPlus reported for the room as one "
        "well mixed volume.",
        f"The internal convective gain, infiltration and interzone mixing heat flows were "
        f"applied as one uniform volumetric source totalling {net_source_w:.1f} W, so where "
        "leaks or occupants actually sit is not resolved.",
    )
    return build_cfd_field(export, centroids, temperature_c, record, speed,
                           extra_notes=notes)
