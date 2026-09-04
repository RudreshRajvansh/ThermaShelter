from __future__ import annotations

import numpy as np

from .constructions import ConstructionSet
from .errors import GeometryError
from .geometry import (GeometryModel, Polygon, WINDOW_MESH_OFFSET_M,
                       triangulate_fan)
from .materials import MaterialLibrary
from .schema import (Facade, GeometryPayload, MeshSurface, SurfaceKind)

WINDOW_OPACITY = 0.32
GROUND_COLOUR = "#7a6a52"
FALLBACK_COLOUR = "#9a9a9a"


def _appearance_for(polygon: Polygon, constructions: ConstructionSet,
                    library: MaterialLibrary) -> tuple[str, str, float]:
    index = constructions.construction_index()
    materials = constructions.material_index()
    construction = index.get(polygon.construction)
    if construction is None or not construction.layers:
        return FALLBACK_COLOUR, "rough", 1.0
    if polygon.kind is SurfaceKind.WINDOW:
        layer = materials.get(construction.layers[0])
        if layer is not None and layer.source_id:
            try:
                system = library.glazing(layer.source_id)
                return (system.appearance.colour, system.appearance.finish,
                        WINDOW_OPACITY)
            except Exception:
                pass
        return FALLBACK_COLOUR, "transparent", WINDOW_OPACITY
    visible_index = -1 if polygon.kind in (SurfaceKind.FLOOR,) else 0
    layer = materials.get(construction.layers[visible_index])
    if layer is None or not layer.source_id:
        return FALLBACK_COLOUR, "rough", 1.0
    try:
        material = library.material(layer.source_id)
    except Exception:
        try:
            pcm = library.pcm(layer.source_id)
            return pcm.appearance.colour, pcm.appearance.finish, 1.0
        except Exception:
            return FALLBACK_COLOUR, "rough", 1.0
    return material.appearance.colour, material.appearance.finish, 1.0


def _offset_vertices(polygon: Polygon, distance: float) -> list[tuple[float, float, float]]:
    normal = np.asarray(polygon.normal, dtype=float) * distance
    return [tuple(float(value) for value in (np.asarray(vertex, dtype=float) + normal))
            for vertex in polygon.vertices]


def build_mesh(model: GeometryModel, constructions: ConstructionSet,
               library: MaterialLibrary) -> GeometryPayload:
    surfaces: list[MeshSurface] = []
    for polygon in model.polygons:
        colour, finish, opacity = _appearance_for(polygon, constructions, library)
        vertices = polygon.vertices
        if polygon.kind is SurfaceKind.WINDOW:
            vertices = _offset_vertices(polygon, WINDOW_MESH_OFFSET_M)
        positions: list[float] = []
        for vertex in vertices:
            positions.extend(float(value) for value in vertex)
        surfaces.append(MeshSurface(
            name=polygon.name, zone=polygon.zone, kind=polygon.kind,
            facade=polygon.facade, positions=tuple(positions),
            indices=triangulate_fan(len(vertices)), normal=polygon.normal,
            colour=colour, finish=finish, area_m2=polygon.area_m2,
            opacity=opacity, parent=polygon.parent))
    low, high = model.bounds()
    payload = GeometryPayload(
        design_hash=model.design_hash, arrangement=model.arrangement,
        azimuth_deg=model.azimuth_deg, bounds_min=low, bounds_max=high,
        surfaces=tuple(surfaces), zone_volumes_m3=model.zone_volumes(),
        warnings=tuple(model.warnings))
    verify_mesh_matches_surfaces(payload, model)
    return payload


def verify_mesh_matches_surfaces(payload: GeometryPayload,
                                 model: GeometryModel) -> None:
    mesh_names = set(payload.surface_names)
    model_names = {polygon.name for polygon in model.polygons}
    missing = model_names - mesh_names
    extra = mesh_names - model_names
    if missing or extra:
        raise GeometryError(
            "the display mesh and the simulation surfaces have drifted apart: "
            f"missing {sorted(missing)}, unexpected {sorted(extra)}")
    if len(mesh_names) != len(payload.surfaces):
        raise GeometryError("the display mesh contains duplicate surface names")
    lookup = {polygon.name: polygon for polygon in model.polygons}
    for surface in payload.surfaces:
        polygon = lookup[surface.name]
        if abs(surface.area_m2 - polygon.area_m2) > 1e-6 * max(1.0, polygon.area_m2):
            raise GeometryError(
                f"mesh area for {surface.name} does not match the simulated surface")
        if float(np.dot(np.asarray(surface.normal),
                        np.asarray(polygon.normal))) < 0.999:
            raise GeometryError(
                f"mesh normal for {surface.name} does not match the simulated surface")


def paintable_surface_names(payload: GeometryPayload) -> tuple[str, ...]:
    return tuple(surface.name for surface in payload.surfaces
                 if surface.kind is not SurfaceKind.SHADING)


def facade_glazing_ratio(model: GeometryModel) -> dict[Facade, float]:
    opaque: dict[Facade, float] = {}
    glazed: dict[Facade, float] = {}
    for polygon in model.polygons:
        if polygon.facade is None or polygon.outside_boundary != "Outdoors":
            continue
        if polygon.kind is SurfaceKind.WINDOW:
            glazed[polygon.facade] = glazed.get(polygon.facade, 0.0) + polygon.area_m2
        elif polygon.kind is SurfaceKind.WALL:
            opaque[polygon.facade] = opaque.get(polygon.facade, 0.0) + polygon.area_m2
    ratios: dict[Facade, float] = {}
    for facade in set(opaque) | set(glazed):
        gross = opaque.get(facade, 0.0)
        window = glazed.get(facade, 0.0)
        if gross <= 0.0:
            continue
        ratios[facade] = window / gross
    return ratios
