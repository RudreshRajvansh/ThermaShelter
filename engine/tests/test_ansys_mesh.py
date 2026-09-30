from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cfd_fixtures import ROOM_D_M, ROOM_H_M, ROOM_W_M, WINDOW, closed_room_export
from shelter_engine.ansys import (build_room_mesh, graded_axis, insert_cuts,
                                  write_mesh)
from shelter_engine.cfd import load_cfd_export
from shelter_engine.errors import ResultsUnavailable

DATA_CFD = Path(__file__).resolve().parents[2] / "data" / "cfd"


def test_graded_axis_is_fine_at_both_walls_and_capped_inside():
    nodes = graded_axis(6.0, first=0.01, largest=0.2, growth=1.2)
    spacing = np.diff(nodes)
    assert nodes[0] == 0.0 and nodes[-1] == pytest.approx(6.0)
    assert spacing[0] == pytest.approx(0.01) and spacing[-1] == pytest.approx(0.01)
    assert spacing.max() <= 0.2 + 1e-9
    assert np.all(spacing > 0.0)
    assert np.allclose(spacing, spacing[::-1])


def test_graded_axis_handles_an_axis_shorter_than_two_first_cells():
    nodes = graded_axis(0.015, first=0.01, largest=0.2)
    assert nodes[0] == 0.0 and nodes[-1] == pytest.approx(0.015)
    assert len(nodes) >= 2


@pytest.mark.parametrize("length, first, largest, growth", [
    (0.0, 0.01, 0.2, 1.2), (float("nan"), 0.01, 0.2, 1.2), (5.0, 0.0, 0.2, 1.2),
    (5.0, 0.3, 0.2, 1.2), (5.0, 0.01, 0.2, 0.9)])
def test_graded_axis_refuses_bad_sizes(length, first, largest, growth):
    with pytest.raises(ResultsUnavailable):
        graded_axis(length, first, largest, growth)


def test_cuts_land_exactly_and_keep_the_axis_increasing():
    base = graded_axis(6.0)
    cuts = [2.0, 4.0, 3.33333, -1.0, 7.0, 0.0, 6.0]
    nodes = insert_cuts(base, cuts)
    for cut in (2.0, 4.0, 3.33333):
        assert np.any(np.abs(nodes - cut) < 1e-9)
    assert np.all(np.diff(nodes) > 0.0)
    assert nodes[0] == 0.0 and nodes[-1] == pytest.approx(6.0)


def test_cuts_closer_than_the_mesher_can_resolve_are_refused():
    with pytest.raises(ResultsUnavailable):
        insert_cuts(np.linspace(0.0, 1.0, 3), [0.30000, 0.30005])


def _check_mesh(mesh):
    corners = mesh.nodes[mesh.hexes - 1]
    signed = np.einsum("ij,ij->i", np.cross(corners[:, 1] - corners[:, 0],
                                            corners[:, 3] - corners[:, 0]),
                       corners[:, 4] - corners[:, 0])
    assert np.all(signed > 0.0)
    assert mesh.volume_m3 == pytest.approx(ROOM_W_M * ROOM_D_M * ROOM_H_M)
    assert np.all(mesh.face_patch >= 0)
    names = [patch.name for patch in mesh.patches]
    window = names.index("ROOM_WALL_SOUTH_WIN")
    south = names.index("ROOM_WALL_SOUTH")
    floor = names.index("ROOM_FLOOR")
    window_area = (WINDOW[1] - WINDOW[0]) * (WINDOW[3] - WINDOW[2])
    assert mesh.patch_area_m2(window) == pytest.approx(window_area, rel=1e-9)
    assert mesh.patch_area_m2(south) == pytest.approx(ROOM_W_M * ROOM_H_M - window_area,
                                                      rel=1e-9)
    assert mesh.patch_area_m2(floor) == pytest.approx(ROOM_W_M * ROOM_D_M, rel=1e-9)
    total = sum(mesh.patch_area_m2(index) for index in range(len(mesh.patches)))
    assert total == pytest.approx(2 * (ROOM_W_M * ROOM_D_M + ROOM_W_M * ROOM_H_M
                                       + ROOM_D_M * ROOM_H_M), rel=1e-9)
    counts = [len(axis) - 1 for axis in mesh.axes]
    assert mesh.cell_count == counts[0] * counts[1] * counts[2]


def test_room_mesh_closes_the_room_and_puts_the_window_on_its_own_faces():
    _check_mesh(build_room_mesh(closed_room_export()))


def test_room_mesh_follows_a_rotated_room():
    mesh = build_room_mesh(closed_room_export(rotation_deg=37.0))
    _check_mesh(mesh)
    assert mesh.frame.axes[2].tolist() == [0.0, 0.0, 1.0]


def test_a_room_that_is_not_a_box_is_refused():
    with pytest.raises(ResultsUnavailable, match="not a box|does not lie"):
        build_room_mesh(closed_room_export(bend=True))


def test_a_room_with_a_missing_wall_is_refused():
    with pytest.raises(ResultsUnavailable, match="covered by no exported surface"):
        build_room_mesh(closed_room_export(drop="ROOM_WALL_NORTH"))


def test_a_room_without_a_floor_is_refused():
    with pytest.raises(ResultsUnavailable, match="floor"):
        build_room_mesh(closed_room_export(drop="ROOM_FLOOR"))


def test_the_cell_limit_is_enforced():
    with pytest.raises(ResultsUnavailable, match="cell limit"):
        build_room_mesh(closed_room_export(), cell_limit=1000)


@pytest.mark.parametrize("path", sorted(DATA_CFD.glob("*.boundary.json")) or [None])
def test_every_stored_boundary_condition_meshes(path):
    if path is None:
        pytest.skip("no stored boundary conditions")
    export = load_cfd_export(path)
    mesh = build_room_mesh(export)
    assert np.all(mesh.face_patch >= 0)
    assert mesh.volume_m3 == pytest.approx(export.zone_volume_m3, rel=1e-6)
    assert mesh.cell_count < 1_000_000


@pytest.mark.parametrize("suffix", [".bdf", ".cgns"])
def test_the_mesh_is_written_for_fluent(tmp_path, suffix):
    pytest.importorskip("gmsh")
    mesh = build_room_mesh(closed_room_export(), first=0.05, largest=0.5)
    path = write_mesh(mesh, tmp_path / f"room{suffix}")
    assert path.is_file() and path.stat().st_size > 0


def test_an_unknown_mesh_format_is_refused(tmp_path):
    mesh = build_room_mesh(closed_room_export(), first=0.05, largest=0.5)
    with pytest.raises(ResultsUnavailable):
        write_mesh(mesh, tmp_path / "room.stl")
