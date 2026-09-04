from __future__ import annotations

from pathlib import Path

import numpy as np

from .schema import SiteSpec, SourceRef, SourceStatus, TerrainMesh

TERRAIN_SOURCE = SourceRef(
    citation=("Digital elevation model supplied by Layer 0 as a triangulated mesh in "
              "site local coordinates, x east, y north, z metres above the site"),
    status=SourceStatus.VERIFIED)

MAX_VERTICES = 40000


def _grid_stride(vertex_count: int) -> int:
    if vertex_count <= MAX_VERTICES:
        return 1
    side = int(round(vertex_count ** 0.5))
    stride = 2
    while (side // stride + 1) ** 2 > MAX_VERTICES:
        stride += 1
    return stride


def load_terrain_obj(path: Path, site: SiteSpec) -> TerrainMesh | None:
    if not path.is_file():
        return None
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        elif line.startswith("f "):
            corners: list[int] = []
            for token in line.split()[1:]:
                head = token.split("/", 1)[0]
                try:
                    index = int(head)
                except ValueError:
                    corners = []
                    break
                corners.append(index - 1 if index > 0 else len(vertices) + index)
            for step in range(1, len(corners) - 1):
                faces.append((corners[0], corners[step], corners[step + 1]))
    if len(vertices) < 3 or not faces:
        return None

    points = np.asarray(vertices, dtype=float)
    triangles = np.asarray(faces, dtype=int)
    inside = ((triangles >= 0) & (triangles < len(points))).all(axis=1)
    triangles = triangles[inside]
    if triangles.size == 0:
        return None

    side = int(round(len(points) ** 0.5))
    stride = _grid_stride(len(points))
    regular = side * side == len(points)
    if stride > 1 and regular:
        grid = np.arange(len(points)).reshape(side, side)
        keep = grid[::stride, ::stride]
        rows, columns = keep.shape
        remap = {int(value): index for index, value in enumerate(keep.ravel())}
        points = points[keep.ravel()]
        rebuilt: list[tuple[int, int, int]] = []
        for row in range(rows - 1):
            for column in range(columns - 1):
                a = row * columns + column
                b = a + 1
                c = a + columns
                d = c + 1
                rebuilt.append((a, c, b))
                rebuilt.append((b, c, d))
        triangles = np.asarray(rebuilt, dtype=int)
        del remap

    spacing = None
    unique_x = np.unique(np.round(points[:, 0], 3))
    if unique_x.size > 1:
        spacing = float(np.min(np.diff(unique_x)))

    radius = float(np.max(np.abs(points[:, :2])))
    heights = points[:, 2]
    note = (
        "Real topography around the site from the Layer 0 digital elevation model, "
        "drawn in metres relative to the site. It is the same ground the 36 direction "
        "horizon profile was measured from, so what stands between the shelter and "
        "the sun on screen is what stood in its way in the simulation. Heights are "
        "not exaggerated.")
    if stride > 1:
        note += (f" Sampled every {stride} points of the supplied grid for display, "
                 "which thins the mesh without moving any vertex it keeps.")
    return TerrainMesh(
        site_id=site.id,
        positions=tuple(float(value) for value in points.ravel()),
        indices=tuple(int(value) for value in triangles.ravel()),
        inner_radius_m=0.0, outer_radius_m=radius,
        ridge_max_m=float(np.max(heights)),
        derived_from_horizon=False, source=TERRAIN_SOURCE, note=note,
        elevation_min_m=float(np.min(heights)),
        elevation_max_m=float(np.max(heights)),
        site_elevation_m=site.elevation_m,
        sample_spacing_m=spacing,
        vertex_count=int(len(points)))
