from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Sequence

import numpy as np

from .errors import GeometryError
from .schema import (Arrangement, DesignSpec, EngineWarning,
                     Facade, HorizonProfile, MIN_HEAD_MARGIN_M,
                     MIN_JAMB_MARGIN_M, MIN_WINDOW_DIMENSION_M,
                     MIN_ZONE_DIMENSION_M, Severity, SurfaceInfo, SurfaceKind,
                     WindowSpec)

Vec3 = tuple[float, float, float]

ROOM = "ROOM"
SUNSPACE = "SUNSPACE"
TROMBE_GAP = "TROMBE_GAP"
VESTIBULE = "VESTIBULE"

SUNSPACE_SOUTH_WWR = 0.75
SUNSPACE_SILL_M = 0.15
TROMBE_SOUTH_WWR = 0.85
TROMBE_SILL_M = 0.05
SUNSPACE_ROOF_GLAZED_FRACTION = 0.60
VESTIBULE_MAX_WIDTH_M = 2.40
HORIZON_RADIUS_M = 300.0
HORIZON_MIN_ANGLE_DEG = 0.5
HORIZON_MAX_HEIGHT_M = 5000.0
HORIZON_SECTOR_OVERLAP = 1.10
HORIZON_MAX_HALF_ANGLE_DEG = 30.0
HORIZON_MIN_SECTORS = 12
COPLANARITY_TOL_M = 1e-6
AREA_TOL_M2 = 1e-9
WINDOW_MESH_OFFSET_M = 0.012
WINDOW_ASPECT = 1.6
REVERSED_SUFFIX = "_REV"

BoundaryCondition = Literal["Outdoors", "Ground", "Surface", "Adiabatic"]


def _as_array(vertices: Sequence[Vec3]) -> np.ndarray:
    array = np.asarray(vertices, dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        raise GeometryError("vertices must be a sequence of xyz triples")
    if array.shape[0] < 3:
        raise GeometryError("a polygon needs at least three vertices")
    if not np.isfinite(array).all():
        raise GeometryError("polygon contains non finite coordinates")
    return array


def newell_normal(vertices: Sequence[Vec3]) -> np.ndarray:
    array = _as_array(vertices)
    rolled = np.roll(array, -1, axis=0)
    normal = np.array([
        float(np.sum((array[:, 1] - rolled[:, 1]) * (array[:, 2] + rolled[:, 2]))),
        float(np.sum((array[:, 2] - rolled[:, 2]) * (array[:, 0] + rolled[:, 0]))),
        float(np.sum((array[:, 0] - rolled[:, 0]) * (array[:, 1] + rolled[:, 1]))),
    ])
    return normal


def polygon_area(vertices: Sequence[Vec3]) -> float:
    return float(np.linalg.norm(newell_normal(vertices)) / 2.0)


def unit_normal(vertices: Sequence[Vec3]) -> np.ndarray:
    normal = newell_normal(vertices)
    length = float(np.linalg.norm(normal))
    if length < AREA_TOL_M2:
        raise GeometryError("degenerate polygon has no normal")
    return normal / length


def azimuth_tilt(normal: Sequence[float]) -> tuple[float, float]:
    vector = np.asarray(normal, dtype=float)
    length = float(np.linalg.norm(vector))
    if length < 1e-12:
        raise GeometryError("cannot derive azimuth from a zero normal")
    vector = vector / length
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, float(vector[2])))))
    horizontal = math.hypot(float(vector[0]), float(vector[1]))
    if horizontal < 1e-9:
        return 0.0, tilt
    azimuth = math.degrees(math.atan2(float(vector[0]), float(vector[1]))) % 360.0
    return azimuth, tilt


def orient_polygon(vertices: Sequence[Vec3], expected_normal: Sequence[float]) -> list[Vec3]:
    array = _as_array(vertices)
    expected = np.asarray(expected_normal, dtype=float)
    expected = expected / float(np.linalg.norm(expected))
    actual = unit_normal(array)
    if float(np.dot(actual, expected)) < 0.0:
        array = array[::-1]
        actual = -actual
    if float(np.dot(actual, expected)) < 0.99:
        raise GeometryError(
            f"polygon normal {actual.round(4).tolist()} does not match the expected "
            f"normal {expected.round(4).tolist()}")
    up_reference = np.array([0.0, 0.0, 1.0]) if abs(actual[2]) < 0.99 \
        else np.array([0.0, 1.0, 0.0])
    up = up_reference - float(np.dot(up_reference, actual)) * actual
    up = up / float(np.linalg.norm(up))
    right = np.cross(up, actual)
    heights = array @ up
    top = float(np.max(heights))
    candidates = np.flatnonzero(heights >= top - 1e-6)
    lefts = array[candidates] @ right
    start = int(candidates[int(np.argmin(lefts))])
    ordered = np.roll(array, -start, axis=0)
    return [tuple(float(value) for value in point) for point in ordered]


def check_coplanar(vertices: Sequence[Vec3], tolerance: float = COPLANARITY_TOL_M) -> float:
    array = _as_array(vertices)
    normal = unit_normal(array)
    offsets = array @ normal
    deviation = float(np.max(offsets) - np.min(offsets))
    if deviation > max(tolerance, 1e-6 * float(np.max(np.abs(array)) + 1.0)):
        raise GeometryError(f"polygon is not planar, deviation {deviation:.6f} m")
    return deviation


def rotate_about_z(vertices: Sequence[Vec3], clockwise_deg: float) -> list[Vec3]:
    if clockwise_deg % 360.0 == 0.0:
        return [tuple(float(value) for value in point) for point in vertices]
    angle = math.radians(clockwise_deg)
    cosine, sine = math.cos(angle), math.sin(angle)
    rotated: list[Vec3] = []
    for x, y, z in vertices:
        rotated.append((x * cosine + y * sine, -x * sine + y * cosine, z))
    return rotated


def triangulate_fan(vertex_count: int) -> tuple[int, ...]:
    if vertex_count < 3:
        raise GeometryError("cannot triangulate fewer than three vertices")
    indices: list[int] = []
    for corner in range(1, vertex_count - 1):
        indices.extend((0, corner, corner + 1))
    return tuple(indices)


@dataclass
class Polygon:
    name: str
    zone: str
    kind: SurfaceKind
    vertices: list[Vec3]
    construction: str
    outside_boundary: BoundaryCondition = "Outdoors"
    boundary_object: str | None = None
    facade: Facade | None = None
    parent: str | None = None
    sun_exposed: bool = True
    wind_exposed: bool = True
    view_factor_to_ground: float | None = None
    expected_normal: Vec3 | None = None

    @property
    def area_m2(self) -> float:
        return polygon_area(self.vertices)

    @property
    def normal(self) -> Vec3:
        vector = unit_normal(self.vertices)
        return (float(vector[0]), float(vector[1]), float(vector[2]))

    @property
    def azimuth_tilt(self) -> tuple[float, float]:
        return azimuth_tilt(self.normal)

    def info(self) -> SurfaceInfo:
        azimuth, tilt = self.azimuth_tilt
        boundary = self.boundary_object if self.outside_boundary == "Surface" \
            else self.outside_boundary
        return SurfaceInfo(
            name=self.name, zone=self.zone, kind=self.kind, facade=self.facade,
            area_m2=self.area_m2, azimuth_deg=azimuth, tilt_deg=tilt,
            construction=self.construction, outside_boundary=boundary or "Outdoors",
            parent=self.parent)


@dataclass
class ShadingPolygon:
    name: str
    vertices: list[Vec3]
    transmittance_schedule: str | None = None


@dataclass
class ZoneModel:
    name: str
    floor_area_m2: float
    volume_m3: float
    ceiling_height_m: float
    origin: Vec3 = (0.0, 0.0, 0.0)
    inside_convection_algorithm: str | None = None
    conditioned: bool = True


@dataclass
class GeometryModel:
    design_hash: str
    arrangement: Arrangement
    azimuth_deg: float
    zones: list[ZoneModel] = field(default_factory=list)
    polygons: list[Polygon] = field(default_factory=list)
    shading: list[ShadingPolygon] = field(default_factory=list)
    warnings: list[EngineWarning] = field(default_factory=list)

    @property
    def opaque(self) -> list[Polygon]:
        return [p for p in self.polygons if p.kind is not SurfaceKind.WINDOW]

    @property
    def windows(self) -> list[Polygon]:
        return [p for p in self.polygons if p.kind is SurfaceKind.WINDOW]

    def zone(self, name: str) -> ZoneModel:
        for candidate in self.zones:
            if candidate.name == name:
                return candidate
        raise GeometryError(f"unknown zone {name}")

    def by_name(self, name: str) -> Polygon:
        for polygon in self.polygons:
            if polygon.name == name:
                return polygon
        raise GeometryError(f"unknown surface {name}")

    def surface_infos(self) -> list[SurfaceInfo]:
        return [polygon.info() for polygon in self.polygons]

    def bounds(self) -> tuple[Vec3, Vec3]:
        points = [vertex for polygon in self.polygons for vertex in polygon.vertices]
        if not points:
            raise GeometryError("model has no surfaces")
        array = np.asarray(points, dtype=float)
        low = array.min(axis=0)
        high = array.max(axis=0)
        return (tuple(float(v) for v in low), tuple(float(v) for v in high))

    def zone_volumes(self) -> dict[str, float]:
        return {zone.name: zone.volume_m3 for zone in self.zones}

    def exterior_window_area_by_facade(self) -> dict[Facade, float]:
        totals: dict[Facade, float] = {}
        for window in self.windows:
            if window.outside_boundary != "Outdoors" or window.facade is None:
                continue
            totals[window.facade] = totals.get(window.facade, 0.0) + window.area_m2
        return totals

    def total_exterior_opaque_area(self) -> float:
        return sum(p.area_m2 for p in self.opaque if p.outside_boundary == "Outdoors")


def _rect(origin: np.ndarray, right: np.ndarray, up: np.ndarray,
          width: float, height: float) -> list[Vec3]:
    a = origin
    b = origin + right * width
    c = origin + right * width + up * height
    d = origin + up * height
    return [tuple(float(v) for v in point) for point in (a, b, c, d)]


_FACE_NORMALS: dict[str, Vec3] = {
    "floor": (0.0, 0.0, -1.0),
    "roof": (0.0, 0.0, 1.0),
    "south": (0.0, -1.0, 0.0),
    "north": (0.0, 1.0, 0.0),
    "east": (1.0, 0.0, 0.0),
    "west": (-1.0, 0.0, 0.0),
}

_FACADE_OF: dict[str, Facade] = {
    "south": Facade.SOUTH, "north": Facade.NORTH,
    "east": Facade.EAST, "west": Facade.WEST,
}


def _face_vertices(face: str, x0: float, x1: float, y0: float, y1: float,
                   z0: float, z1: float) -> list[Vec3]:
    if face == "floor":
        return [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)]
    if face == "roof":
        return [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    if face == "south":
        return [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]
    if face == "north":
        return [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)]
    if face == "east":
        return [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]
    if face == "west":
        return [(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)]
    raise GeometryError(f"unknown face {face}")


def _make_polygon(name: str, zone: str, kind: SurfaceKind, raw: Sequence[Vec3],
                  expected_normal: Vec3, construction: str,
                  outside_boundary: BoundaryCondition = "Outdoors",
                  boundary_object: str | None = None,
                  facade: Facade | None = None,
                  parent: str | None = None) -> Polygon:
    if polygon_area(raw) < 1e-6:
        raise GeometryError(f"surface {name} has effectively zero area")
    check_coplanar(raw)
    ordered = orient_polygon(raw, expected_normal)
    sun_exposed = outside_boundary == "Outdoors"
    return Polygon(
        name=name, zone=zone, kind=kind, vertices=ordered, construction=construction,
        outside_boundary=outside_boundary, boundary_object=boundary_object,
        facade=facade, parent=parent, sun_exposed=sun_exposed,
        wind_exposed=sun_exposed, expected_normal=expected_normal)


def _mirror(vertices: Sequence[Vec3]) -> list[Vec3]:
    return list(reversed([tuple(float(v) for v in point) for point in vertices]))


def window_dimensions(wall_width: float, wall_height: float, sill_height: float,
                      wwr: float) -> tuple[float, float]:
    if wwr <= 0.0:
        return 0.0, 0.0
    area = wwr * wall_width * wall_height
    usable_width = wall_width - 2.0 * MIN_JAMB_MARGIN_M
    usable_height = wall_height - sill_height - MIN_HEAD_MARGIN_M
    if usable_width < MIN_WINDOW_DIMENSION_M or usable_height < MIN_WINDOW_DIMENSION_M:
        raise GeometryError(
            f"no room for a window in a {wall_width:.2f} x {wall_height:.2f} m wall "
            f"above a {sill_height:.2f} m sill")
    height = min(usable_height, math.sqrt(area / WINDOW_ASPECT))
    height = max(height, MIN_WINDOW_DIMENSION_M)
    width = area / height
    if width > usable_width:
        width = usable_width
        height = area / width
    if height > usable_height:
        height = usable_height
        width = area / height
    if width > usable_width + 1e-9 or height > usable_height + 1e-9:
        raise GeometryError(
            f"window of {area:.2f} m2 does not fit inside {usable_width:.2f} x "
            f"{usable_height:.2f} m of usable wall")
    return width, height


def _window_on_wall(parent: Polygon, spec: WindowSpec, name: str,
                    glazing_construction: str,
                    outside_boundary: BoundaryCondition = "Outdoors",
                    boundary_object: str | None = None,
                    wwr_override: float | None = None,
                    sill_override: float | None = None) -> Polygon | None:
    wwr = spec.wwr if wwr_override is None else wwr_override
    if wwr <= 0.0:
        return None
    sill = spec.sill_height_m if sill_override is None else sill_override
    array = np.asarray(parent.vertices, dtype=float)
    normal = np.asarray(parent.normal, dtype=float)
    up = np.array([0.0, 0.0, 1.0])
    up = up - float(np.dot(up, normal)) * normal
    if float(np.linalg.norm(up)) < 1e-9:
        raise GeometryError(f"cannot place a window on horizontal surface {parent.name}")
    up = up / float(np.linalg.norm(up))
    right = np.cross(up, normal)
    horizontal = array @ right
    vertical = array @ up
    wall_width = float(np.max(horizontal) - np.min(horizontal))
    wall_height = float(np.max(vertical) - np.min(vertical))
    width, height = window_dimensions(wall_width, wall_height, sill, wwr)
    left = float(np.min(horizontal)) + (wall_width - width) / 2.0
    bottom = float(np.min(vertical)) + sill
    if bottom + height > float(np.max(vertical)) - MIN_HEAD_MARGIN_M + 1e-9:
        bottom = float(np.max(vertical)) - MIN_HEAD_MARGIN_M - height
    plane_offset = float(np.dot(array[0], normal))
    origin = right * left + up * bottom + normal * plane_offset
    raw = _rect(origin, right, up, width, height)
    return _make_polygon(
        name=name, zone=parent.zone, kind=SurfaceKind.WINDOW, raw=raw,
        expected_normal=parent.normal, construction=glazing_construction,
        outside_boundary=outside_boundary, boundary_object=boundary_object,
        facade=parent.facade, parent=parent.name)


@dataclass
class ConstructionNames:
    wall: str
    roof: str
    floor: str
    partition: str
    ceiling: str
    glazing: dict[Facade, str]
    sunspace_glazing: str = ""
    trombe_glazing: str = ""
    trombe_wall: str = ""
    sunspace_floor: str = ""
    interior_door: str = ""


def _strip_bounds(x0: float, x1: float, fraction: float) -> tuple[float, float]:
    width = x1 - x0
    centre = 0.5 * (x0 + x1)
    half = 0.5 * width * min(1.0, max(0.0, fraction))
    return centre - half, centre + half


def build_geometry(design: DesignSpec, constructions: ConstructionNames,
                   design_hash: str = "",
                   horizon: HorizonProfile | None = None,
                   include_horizon: bool = True) -> GeometryModel:
    plan = design.plan
    params = design.arrangement_params
    width = plan.width_m
    depth = plan.depth_m
    height = plan.ceiling_height_m
    warnings: list[EngineWarning] = []

    x0, x1 = -width / 2.0, width / 2.0
    y0, y1 = -depth / 2.0, depth / 2.0
    z0, z1 = 0.0, height

    model = GeometryModel(design_hash=design_hash, arrangement=design.arrangement,
                          azimuth_deg=plan.azimuth_deg)
    model.zones.append(ZoneModel(
        name=ROOM, floor_area_m2=plan.floor_area_m2, volume_m3=plan.volume_m3,
        ceiling_height_m=height, conditioned=True))

    polygons: list[Polygon] = []
    polygons.append(_make_polygon(
        f"{ROOM}_FLOOR", ROOM, SurfaceKind.FLOOR,
        _face_vertices("floor", x0, x1, y0, y1, z0, z1), _FACE_NORMALS["floor"],
        constructions.floor, outside_boundary="Ground"))
    polygons.append(_make_polygon(
        f"{ROOM}_ROOF", ROOM, SurfaceKind.ROOF,
        _face_vertices("roof", x0, x1, y0, y1, z0, z1), _FACE_NORMALS["roof"],
        constructions.roof))

    attached_facade: Facade | None = None
    attached_fraction = 1.0
    if design.arrangement == Arrangement.SUNSPACE:
        attached_facade, attached_fraction = Facade.SOUTH, 1.0
    elif design.arrangement == Arrangement.TROMBE_WALL:
        attached_facade = Facade.SOUTH
        attached_fraction = params.trombe_width_fraction
    elif design.arrangement == Arrangement.VESTIBULE:
        attached_facade = params.vestibule_facade
        facade_width = plan.facade_width_m(attached_facade)
        attached_fraction = min(1.0, VESTIBULE_MAX_WIDTH_M / facade_width)

    for face in ("south", "north", "east", "west"):
        facade = _FACADE_OF[face]
        normal = _FACE_NORMALS[face]
        if facade == attached_facade and attached_fraction >= 0.999:
            continue
        if facade == attached_facade:
            segments = _wall_segments(face, x0, x1, y0, y1, z0, z1, attached_fraction)
            for label, verts in segments:
                if label == "C":
                    continue
                polygons.append(_make_polygon(
                    f"{ROOM}_WALL_{face.upper()}_{label}", ROOM, SurfaceKind.WALL,
                    verts, normal, constructions.wall, facade=facade))
            continue
        verts = _face_vertices(face, x0, x1, y0, y1, z0, z1)
        wall = _make_polygon(
            f"{ROOM}_WALL_{face.upper()}", ROOM, SurfaceKind.WALL, verts, normal,
            constructions.wall, facade=facade)
        polygons.append(wall)
        window_spec = design.windows.get(facade)
        if window_spec is not None and window_spec.wwr > 0.0:
            glazing = constructions.glazing.get(facade)
            if glazing is None:
                raise GeometryError(f"no glazing construction registered for {facade.value}")
            window = _window_on_wall(wall, window_spec, f"{wall.name}_WIN", glazing)
            if window is not None:
                polygons.append(window)

    if design.arrangement == Arrangement.SUNSPACE:
        polygons.extend(_build_sunspace(design, constructions, model, x0, x1, y0, z0, z1))
    elif design.arrangement == Arrangement.TROMBE_WALL:
        polygons.extend(_build_trombe(design, constructions, model, x0, x1, y0, z0, z1,
                                      warnings))
    elif design.arrangement == Arrangement.VESTIBULE:
        polygons.extend(_build_vestibule(design, constructions, model, x0, x1, y0, y1,
                                         z0, z1, attached_fraction))

    rotation = plan.azimuth_deg - 180.0
    for polygon in polygons:
        polygon.vertices = rotate_about_z(polygon.vertices, rotation)
        if polygon.expected_normal is not None:
            polygon.expected_normal = tuple(
                rotate_about_z([polygon.expected_normal], rotation)[0])
    for zone in model.zones:
        zone.origin = tuple(rotate_about_z([zone.origin], rotation)[0])

    model.polygons = polygons
    if include_horizon and horizon is not None:
        model.shading = build_horizon_shading(horizon, warnings)
    model.warnings = warnings
    validate_model(model)
    return model


def _wall_segments(face: str, x0: float, x1: float, y0: float, y1: float,
                   z0: float, z1: float,
                   fraction: float) -> list[tuple[str, list[Vec3]]]:
    if face in ("south", "north"):
        a, b = _strip_bounds(x0, x1, fraction)
        pieces = [("L", (x0, a)), ("C", (a, b)), ("R", (b, x1))]
        result = []
        for label, (lo, hi) in pieces:
            if hi - lo < MIN_ZONE_DIMENSION_M:
                continue
            result.append((label, _face_vertices(face, lo, hi, y0, y1, z0, z1)))
        return result
    a, b = _strip_bounds(y0, y1, fraction)
    pieces = [("L", (y0, a)), ("C", (a, b)), ("R", (b, y1))]
    result = []
    for label, (lo, hi) in pieces:
        if hi - lo < MIN_ZONE_DIMENSION_M:
            continue
        result.append((label, _face_vertices(face, x0, x1, lo, hi, z0, z1)))
    return result


def _partition_pair(name_a: str, zone_a: str, name_b: str, zone_b: str,
                    raw: Sequence[Vec3], normal_from_a: Vec3, construction: str,
                    facade: Facade | None, kind: SurfaceKind = SurfaceKind.PARTITION
                    ) -> tuple[Polygon, Polygon]:
    side_a = _make_polygon(name_a, zone_a, kind, raw, normal_from_a, construction,
                           outside_boundary="Surface", boundary_object=name_b,
                           facade=facade)
    opposite = tuple(-value for value in normal_from_a)
    side_b = _make_polygon(name_b, zone_b, kind, _mirror(side_a.vertices), opposite,
                           reversed_construction(construction),
                           outside_boundary="Surface",
                           boundary_object=name_a, facade=opposite_facade(facade))
    return side_a, side_b


def opposite_facade(facade: Facade | None) -> Facade | None:
    if facade is None:
        return None
    return {Facade.NORTH: Facade.SOUTH, Facade.SOUTH: Facade.NORTH,
            Facade.EAST: Facade.WEST, Facade.WEST: Facade.EAST}[facade]


def reversed_construction(name: str) -> str:
    return f"{name}{REVERSED_SUFFIX}"


def _build_sunspace(design: DesignSpec, constructions: ConstructionNames,
                    model: GeometryModel, x0: float, x1: float, y0: float,
                    z0: float, z1: float) -> list[Polygon]:
    params = design.arrangement_params
    sy1 = y0
    sy0 = y0 - params.sunspace_depth_m
    width = x1 - x0
    depth = params.sunspace_depth_m
    height = z1 - z0
    polygons: list[Polygon] = []

    model.zones.append(ZoneModel(
        name=SUNSPACE, floor_area_m2=width * depth, volume_m3=width * depth * height,
        ceiling_height_m=height, origin=(0.0, sy0, 0.0), conditioned=False))

    shared_raw = _face_vertices("south", x0, x1, sy1, sy1, z0, z1)
    room_side, sunspace_side = _partition_pair(
        f"{ROOM}_WALL_SOUTH", ROOM, f"{SUNSPACE}_WALL_NORTH", SUNSPACE,
        shared_raw, _FACE_NORMALS["south"], constructions.partition, Facade.SOUTH)
    polygons.extend((room_side, sunspace_side))

    south_spec = design.windows[Facade.SOUTH]
    glazing = constructions.glazing.get(Facade.SOUTH)
    if glazing is None:
        raise GeometryError("no glazing construction registered for the south facade")
    room_window = _window_on_wall(
        room_side, south_spec, f"{room_side.name}_WIN", glazing,
        outside_boundary="Surface", boundary_object=f"{sunspace_side.name}_WIN")
    if room_window is not None:
        sun_window = _make_polygon(
            f"{sunspace_side.name}_WIN", SUNSPACE, SurfaceKind.WINDOW,
            _mirror(room_window.vertices),
            tuple(-value for value in room_window.normal), glazing,
            outside_boundary="Surface", boundary_object=room_window.name,
            facade=Facade.SOUTH, parent=sunspace_side.name)
        polygons.extend((room_window, sun_window))

    polygons.append(_make_polygon(
        f"{SUNSPACE}_FLOOR", SUNSPACE, SurfaceKind.FLOOR,
        _face_vertices("floor", x0, x1, sy0, sy1, z0, z1), _FACE_NORMALS["floor"],
        constructions.sunspace_floor or constructions.floor, outside_boundary="Ground"))

    sunspace_glazing = constructions.sunspace_glazing or glazing
    roof = _make_polygon(
        f"{SUNSPACE}_ROOF", SUNSPACE, SurfaceKind.ROOF,
        _face_vertices("roof", x0, x1, sy0, sy1, z0, z1), _FACE_NORMALS["roof"],
        constructions.roof)
    polygons.append(roof)
    if params.sunspace_glazed_roof:
        skylight = _skylight_on_roof(roof, f"{roof.name}_WIN", sunspace_glazing,
                                     SUNSPACE_ROOF_GLAZED_FRACTION)
        if skylight is not None:
            polygons.append(skylight)

    south_wall = _make_polygon(
        f"{SUNSPACE}_WALL_SOUTH", SUNSPACE, SurfaceKind.WALL,
        _face_vertices("south", x0, x1, sy0, sy0, z0, z1), _FACE_NORMALS["south"],
        constructions.wall, facade=Facade.SOUTH)
    polygons.append(south_wall)
    glazed = _window_on_wall(
        south_wall, south_spec, f"{south_wall.name}_WIN", sunspace_glazing,
        wwr_override=SUNSPACE_SOUTH_WWR, sill_override=SUNSPACE_SILL_M)
    if glazed is not None:
        polygons.append(glazed)

    for face, lo, hi in (("east", sy0, sy1), ("west", sy0, sy1)):
        polygons.append(_make_polygon(
            f"{SUNSPACE}_WALL_{face.upper()}", SUNSPACE, SurfaceKind.WALL,
            _face_vertices(face, x0, x1, lo, hi, z0, z1), _FACE_NORMALS[face],
            constructions.wall, facade=_FACADE_OF[face]))

    return polygons


def _skylight_on_roof(parent: Polygon, name: str, glazing_construction: str,
                      fraction: float) -> Polygon | None:
    if fraction <= 0.0:
        return None
    if fraction >= 1.0:
        raise GeometryError("a skylight cannot cover the whole roof surface")
    array = np.asarray(parent.vertices, dtype=float)
    centroid = array.mean(axis=0)
    scale = math.sqrt(fraction)
    raw = [tuple(float(v) for v in (centroid + (point - centroid) * scale))
           for point in array]
    return _make_polygon(
        name=name, zone=parent.zone, kind=SurfaceKind.WINDOW, raw=raw,
        expected_normal=parent.normal, construction=glazing_construction,
        facade=parent.facade, parent=parent.name)


def _build_trombe(design: DesignSpec, constructions: ConstructionNames,
                  model: GeometryModel, x0: float, x1: float, y0: float,
                  z0: float, z1: float,
                  warnings: list[EngineWarning]) -> list[Polygon]:
    params = design.arrangement_params
    gap = params.trombe_gap_m
    gy1 = y0
    gy0 = y0 - gap
    gx0, gx1 = _strip_bounds(x0, x1, params.trombe_width_fraction)
    width = gx1 - gx0
    height = z1 - z0
    polygons: list[Polygon] = []

    model.zones.append(ZoneModel(
        name=TROMBE_GAP, floor_area_m2=width * gap, volume_m3=width * gap * height,
        ceiling_height_m=height, origin=(0.0, gy0, 0.0),
        inside_convection_algorithm="TrombeWall", conditioned=False))

    if gap < 0.05:
        warnings.append(EngineWarning(
            code="trombe_gap_narrow", severity=Severity.WARNING, origin="geometry",
            message=(f"Trombe air gap of {gap * 1000:.0f} mm is below the 50 mm at which "
                     "the EnergyPlus Trombe wall convection correlation is normally "
                     "applied; convection in the gap is approximate.")))

    shared_raw = _face_vertices("south", gx0, gx1, gy1, gy1, z0, z1)
    room_side, gap_side = _partition_pair(
        f"{ROOM}_WALL_SOUTH_C", ROOM, f"{TROMBE_GAP}_WALL_NORTH", TROMBE_GAP,
        shared_raw, _FACE_NORMALS["south"],
        constructions.trombe_wall or constructions.wall, Facade.SOUTH)
    polygons.extend((room_side, gap_side))

    polygons.append(_make_polygon(
        f"{TROMBE_GAP}_FLOOR", TROMBE_GAP, SurfaceKind.FLOOR,
        _face_vertices("floor", gx0, gx1, gy0, gy1, z0, z1), _FACE_NORMALS["floor"],
        constructions.floor, outside_boundary="Ground"))
    polygons.append(_make_polygon(
        f"{TROMBE_GAP}_ROOF", TROMBE_GAP, SurfaceKind.ROOF,
        _face_vertices("roof", gx0, gx1, gy0, gy1, z0, z1), _FACE_NORMALS["roof"],
        constructions.roof))

    glazing = constructions.trombe_glazing or constructions.glazing.get(Facade.SOUTH)
    if glazing is None:
        raise GeometryError("no glazing construction registered for the Trombe wall")
    south_wall = _make_polygon(
        f"{TROMBE_GAP}_WALL_SOUTH", TROMBE_GAP, SurfaceKind.WALL,
        _face_vertices("south", gx0, gx1, gy0, gy0, z0, z1), _FACE_NORMALS["south"],
        constructions.wall, facade=Facade.SOUTH)
    polygons.append(south_wall)
    spec = design.windows.get(Facade.SOUTH) or WindowSpec(wwr=0.0, glazing_id="none")
    glazed = _window_on_wall(
        south_wall, spec, f"{south_wall.name}_WIN", glazing,
        wwr_override=TROMBE_SOUTH_WWR, sill_override=TROMBE_SILL_M)
    if glazed is not None:
        polygons.append(glazed)

    for face in ("east", "west"):
        polygons.append(_make_polygon(
            f"{TROMBE_GAP}_WALL_{face.upper()}", TROMBE_GAP, SurfaceKind.WALL,
            _face_vertices(face, gx0, gx1, gy0, gy1, z0, z1), _FACE_NORMALS[face],
            constructions.wall, facade=_FACADE_OF[face]))
    return polygons


def _build_vestibule(design: DesignSpec, constructions: ConstructionNames,
                     model: GeometryModel, x0: float, x1: float, y0: float,
                     y1: float, z0: float, z1: float,
                     fraction: float) -> list[Polygon]:
    params = design.arrangement_params
    facade = params.vestibule_facade
    depth = params.vestibule_depth_m
    height = z1 - z0
    polygons: list[Polygon] = []

    if facade in (Facade.NORTH, Facade.SOUTH):
        vx0, vx1 = _strip_bounds(x0, x1, fraction)
        if facade == Facade.NORTH:
            vy0, vy1 = y1, y1 + depth
            shared_face = "north"
        else:
            vy0, vy1 = y0 - depth, y0
            shared_face = "south"
        shared_y = y1 if facade == Facade.NORTH else y0
        shared_raw = _face_vertices(shared_face, vx0, vx1, shared_y, shared_y, z0, z1)
        vwidth = vx1 - vx0
    else:
        vy0, vy1 = _strip_bounds(y0, y1, fraction)
        if facade == Facade.EAST:
            vx0, vx1 = x1, x1 + depth
            shared_face = "east"
            shared_x = x1
        else:
            vx0, vx1 = x0 - depth, x0
            shared_face = "west"
            shared_x = x0
        shared_raw = _face_vertices(shared_face, shared_x, shared_x, vy0, vy1, z0, z1)
        vwidth = vy1 - vy0

    model.zones.append(ZoneModel(
        name=VESTIBULE, floor_area_m2=vwidth * depth,
        volume_m3=vwidth * depth * height, ceiling_height_m=height,
        origin=(0.5 * (vx0 + vx1), 0.5 * (vy0 + vy1), 0.0), conditioned=False))

    room_side, vest_side = _partition_pair(
        f"{ROOM}_WALL_{shared_face.upper()}_C", ROOM,
        f"{VESTIBULE}_WALL_{_opposite(shared_face).upper()}", VESTIBULE,
        shared_raw, _FACE_NORMALS[shared_face], constructions.partition, facade)
    polygons.extend((room_side, vest_side))

    polygons.append(_make_polygon(
        f"{VESTIBULE}_FLOOR", VESTIBULE, SurfaceKind.FLOOR,
        _face_vertices("floor", vx0, vx1, vy0, vy1, z0, z1), _FACE_NORMALS["floor"],
        constructions.floor, outside_boundary="Ground"))
    polygons.append(_make_polygon(
        f"{VESTIBULE}_ROOF", VESTIBULE, SurfaceKind.ROOF,
        _face_vertices("roof", vx0, vx1, vy0, vy1, z0, z1), _FACE_NORMALS["roof"],
        constructions.roof))
    for face in ("south", "north", "east", "west"):
        if face == _opposite(shared_face):
            continue
        polygons.append(_make_polygon(
            f"{VESTIBULE}_WALL_{face.upper()}", VESTIBULE, SurfaceKind.WALL,
            _face_vertices(face, vx0, vx1, vy0, vy1, z0, z1), _FACE_NORMALS[face],
            constructions.wall, facade=_FACADE_OF[face]))
    return polygons


def _opposite(face: str) -> str:
    return {"north": "south", "south": "north", "east": "west", "west": "east"}[face]


def build_horizon_shading(horizon: HorizonProfile,
                          warnings: list[EngineWarning]) -> list[ShadingPolygon]:
    order = sorted(range(len(horizon.azimuths_deg)),
                   key=lambda i: horizon.azimuths_deg[i])
    azimuths = [horizon.azimuths_deg[i] for i in order]
    altitudes = [horizon.blocked_altitude_deg[i] for i in order]
    count = len(azimuths)
    sector = 360.0 / count
    shading: list[ShadingPolygon] = []
    clipped = 0
    if count < HORIZON_MIN_SECTORS:
        warnings.append(EngineWarning(
            code="horizon_profile_coarse", severity=Severity.WARNING,
            origin="geometry",
            message=(f"The horizon profile has only {count} sectors. Flat shading "
                     f"panels are capped at {HORIZON_MAX_HALF_ANGLE_DEG:.0f} degrees "
                     "half width, so a profile this coarse leaves unshaded gaps "
                     "between panels and understates valley shading. Sample the "
                     f"terrain on at least {HORIZON_MIN_SECTORS} compass directions.")))
    for index, (azimuth, altitude) in enumerate(zip(azimuths, altitudes)):
        if altitude < HORIZON_MIN_ANGLE_DEG:
            continue
        height = HORIZON_RADIUS_M * math.tan(math.radians(altitude))
        if height > HORIZON_MAX_HEIGHT_M:
            height = HORIZON_MAX_HEIGHT_M
            clipped += 1
        half = math.radians(min(sector * HORIZON_SECTOR_OVERLAP / 2.0,
                                HORIZON_MAX_HALF_ANGLE_DEG))
        centre = math.radians(azimuth)
        left = centre - half
        right = centre + half
        p_left = (HORIZON_RADIUS_M * math.sin(left), HORIZON_RADIUS_M * math.cos(left))
        p_right = (HORIZON_RADIUS_M * math.sin(right), HORIZON_RADIUS_M * math.cos(right))
        raw = [
            (p_left[0], p_left[1], 0.0),
            (p_right[0], p_right[1], 0.0),
            (p_right[0], p_right[1], height),
            (p_left[0], p_left[1], height),
        ]
        inward = (-math.sin(centre), -math.cos(centre), 0.0)
        ordered = orient_polygon(raw, inward)
        shading.append(ShadingPolygon(name=f"HORIZON_{index:02d}", vertices=ordered))
    if clipped:
        warnings.append(EngineWarning(
            code="horizon_clipped", severity=Severity.WARNING, origin="geometry",
            count=clipped,
            message=(f"{clipped} horizon sectors exceeded the {HORIZON_MAX_HEIGHT_M:.0f} m "
                     "shading panel height cap and were clipped.")))
    return shading


def validate_model(model: GeometryModel) -> None:
    names: set[str] = set()
    zone_names = {zone.name for zone in model.zones}
    for polygon in model.polygons:
        if polygon.name in names:
            raise GeometryError(f"duplicate surface name {polygon.name}")
        if len(polygon.name) > 100:
            raise GeometryError(f"surface name {polygon.name} exceeds 100 characters")
        names.add(polygon.name)
        if polygon.zone not in zone_names:
            raise GeometryError(f"surface {polygon.name} names unknown zone {polygon.zone}")
        if polygon.area_m2 < 1e-6:
            raise GeometryError(f"surface {polygon.name} has zero area")
        check_coplanar(polygon.vertices)
        if polygon.expected_normal is not None:
            actual = np.asarray(polygon.normal, dtype=float)
            expected = np.asarray(polygon.expected_normal, dtype=float)
            expected = expected / float(np.linalg.norm(expected))
            if float(np.dot(actual, expected)) < 0.99:
                raise GeometryError(
                    f"surface {polygon.name} faces {actual.round(3).tolist()} but should "
                    f"face {expected.round(3).tolist()}; vertex order is wrong")

    for polygon in model.polygons:
        if polygon.outside_boundary != "Surface":
            continue
        partner_name = polygon.boundary_object
        if not partner_name or partner_name not in names:
            raise GeometryError(
                f"interzone surface {polygon.name} points at missing partner {partner_name}")
        partner = model.by_name(partner_name)
        if partner.boundary_object != polygon.name:
            raise GeometryError(
                f"interzone pair {polygon.name} and {partner_name} is not reciprocal")
        if partner.zone == polygon.zone:
            raise GeometryError(
                f"interzone pair {polygon.name} and {partner_name} share zone {polygon.zone}")
        if abs(polygon.area_m2 - partner.area_m2) > 1e-6 * max(1.0, polygon.area_m2):
            raise GeometryError(
                f"interzone pair {polygon.name} and {partner_name} have different areas")
        if float(np.dot(np.asarray(polygon.normal), np.asarray(partner.normal))) > -0.99:
            raise GeometryError(
                f"interzone pair {polygon.name} and {partner_name} do not face each other")

    for window in model.windows:
        if window.parent is None:
            raise GeometryError(f"window {window.name} has no parent surface")
        parent = model.by_name(window.parent)
        if parent.zone != window.zone:
            raise GeometryError(
                f"window {window.name} and parent {parent.name} are in different zones")
        if float(np.dot(np.asarray(window.normal), np.asarray(parent.normal))) < 0.999:
            raise GeometryError(
                f"window {window.name} is not parallel to parent {parent.name}")
        if window.area_m2 >= parent.area_m2:
            raise GeometryError(
                f"window {window.name} is not smaller than parent {parent.name}")
        _assert_inside(window, parent)

    for zone in model.zones:
        if zone.volume_m3 <= 0.0:
            raise GeometryError(f"zone {zone.name} has non positive volume")
        owned = [p for p in model.polygons if p.zone == zone.name
                 and p.kind is not SurfaceKind.WINDOW]
        if len(owned) < 4:
            raise GeometryError(
                f"zone {zone.name} has only {len(owned)} opaque surfaces and cannot enclose")


def _assert_inside(window: Polygon, parent: Polygon) -> None:
    normal = np.asarray(parent.normal, dtype=float)
    up = np.array([0.0, 0.0, 1.0])
    up = up - float(np.dot(up, normal)) * normal
    if float(np.linalg.norm(up)) < 1e-9:
        up = np.array([0.0, 1.0, 0.0])
        up = up - float(np.dot(up, normal)) * normal
    up = up / float(np.linalg.norm(up))
    right = np.cross(up, normal)
    parent_array = np.asarray(parent.vertices, dtype=float)
    window_array = np.asarray(window.vertices, dtype=float)
    for axis, label in ((right, "horizontally"), (up, "vertically")):
        low, high = float(np.min(parent_array @ axis)), float(np.max(parent_array @ axis))
        w_low, w_high = float(np.min(window_array @ axis)), float(np.max(window_array @ axis))
        if w_low < low - 1e-6 or w_high > high + 1e-6:
            raise GeometryError(
                f"window {window.name} extends {label} beyond parent {parent.name}")
    plane = float(np.dot(parent_array[0], normal))
    offsets = window_array @ normal
    if float(np.max(np.abs(offsets - plane))) > 1e-6:
        raise GeometryError(
            f"window {window.name} is not coplanar with parent {parent.name}")


def facade_azimuths(design: DesignSpec) -> dict[Facade, float]:
    rotation = design.plan.azimuth_deg - 180.0
    return {facade: (facade.nominal_azimuth_deg + rotation) % 360.0
            for facade in Facade}


def surface_lookup(model: GeometryModel) -> dict[str, SurfaceInfo]:
    return {info.name: info for info in model.surface_infos()}
