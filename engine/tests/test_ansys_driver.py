from __future__ import annotations

import os

import numpy as np
import pytest

from cfd_fixtures import closed_room_export
from shelter_engine.ansys import (ChunkRecord, FluentOptions, build_room_mesh,
                                  cell_volumes, find_ansys_root, judge_convergence,
                                  match_zone, parse_residuals, run_fluent,
                                  wall_heat_from_report)
from shelter_engine.errors import ConfigurationError, ResultsUnavailable

OPTIONS = FluentOptions(chunk_iterations=50, min_iterations=150, stable_chunks=3,
                        drift_tolerance_k=0.02, band_tolerance_k=0.1)
SOLVED_RUN = [2.059, 2.072, 2.076, 2.049, 2.054, 2.052, 2.072, 2.084, 2.053]


def history(temperatures, heat=None, start=50):
    return [ChunkRecord(start + 50 * index, value, heat)
            for index, value in enumerate(temperatures)]


def test_an_oscillating_but_stationary_solution_with_a_closed_balance_converges():
    converged, basis = judge_convergence(history(SOLVED_RUN, heat=338.0), -344.2, OPTIONS)
    assert converged
    assert "drifted" in basis and "closed" in basis


def test_a_drifting_temperature_does_not_converge():
    converged, basis = judge_convergence(history([2.0, 2.02, 2.04, 2.06, 2.08, 2.1]),
                                         -344.2, OPTIONS)
    assert not converged and "still drifting" in basis


def test_a_wide_swing_without_drift_does_not_converge():
    converged, basis = judge_convergence(history([2.1, 2.0, 2.2, 2.1, 2.2, 2.0, 2.1]),
                                         -344.2, OPTIONS)
    assert not converged and "swings" in basis


def test_an_open_heat_balance_does_not_converge():
    converged, basis = judge_convergence(history(SOLVED_RUN, heat=-5.0), -344.2, OPTIONS)
    assert not converged and "unbalanced" in basis


def test_the_balance_uses_every_chunk_of_the_window():
    records = history(SOLVED_RUN, heat=338.0)
    records[-1] = ChunkRecord(records[-1].iteration, records[-1].mean_air_c, None)
    converged, basis = judge_convergence(records, -344.2, OPTIONS)
    assert converged and "could not be evaluated" in basis


def test_the_balance_floor_protects_a_near_zero_source():
    converged, _ = judge_convergence(history([2.0] * 6, heat=6.0), 0.0, OPTIONS)
    assert converged


def test_too_few_iterations_or_chunks_do_not_converge():
    assert not judge_convergence([], -300.0, OPTIONS)[0]
    assert not judge_convergence(history([2.0] * 5), -300.0, OPTIONS)[0]
    assert not judge_convergence(history([2.0] * 6, start=0), -300.0,
                                 FluentOptions(min_iterations=500, stable_chunks=3))[0]
    assert not judge_convergence(history([2.0, float("nan"), 2.0, 2.0, 2.0, 2.0]), -300.0,
                                 OPTIONS)[0]


def test_without_a_heat_report_convergence_says_the_balance_was_not_evaluated():
    converged, basis = judge_convergence(history(SOLVED_RUN), -300.0, OPTIONS)
    assert converged and "could not be evaluated" in basis


@pytest.mark.parametrize("kwargs", [dict(processor_count=0), dict(chunk_iterations=0),
                                    dict(max_iterations=10, chunk_iterations=50),
                                    dict(min_iterations=5000), dict(stable_chunks=1),
                                    dict(drift_tolerance_k=0.0), dict(band_tolerance_k=0.0)])
def test_fluent_options_refuse_nonsense(kwargs):
    with pytest.raises(ConfigurationError):
        FluentOptions(**kwargs)


def _hex_centroids(mesh):
    corners = mesh.nodes[mesh.hexes - 1]
    return corners.mean(axis=1)


def test_cell_volumes_follow_fluent_cell_order():
    mesh = build_room_mesh(closed_room_export(rotation_deg=21.0), first=0.05, largest=0.5)
    centroids = _hex_centroids(mesh)
    order = np.random.default_rng(7).permutation(len(centroids))
    volumes = cell_volumes(mesh, centroids[order])
    assert volumes.sum() == pytest.approx(mesh.volume_m3)
    corners = mesh.nodes[mesh.hexes - 1][order]
    expected = np.abs(np.einsum("ij,ij->i", np.cross(corners[:, 1] - corners[:, 0],
                                                     corners[:, 3] - corners[:, 0]),
                                corners[:, 4] - corners[:, 0]))
    assert np.allclose(volumes, expected)


def test_cell_volumes_refuse_cells_from_another_mesh():
    mesh = build_room_mesh(closed_room_export(), first=0.05, largest=0.5)
    centroids = _hex_centroids(mesh)
    with pytest.raises(ResultsUnavailable, match="cells but"):
        cell_volumes(mesh, centroids[:-1])
    assert cell_volumes(mesh, centroids + 1.2e-5).sum() == pytest.approx(mesh.volume_m3)
    with pytest.raises(ResultsUnavailable, match="do not belong"):
        cell_volumes(mesh, centroids + np.array([0.02, 0.0, 0.0]))


def test_wall_zones_match_their_surface_including_a_wall_with_a_window():
    mesh = build_room_mesh(closed_room_export(), first=0.05, largest=0.5)
    names = [patch.name for patch in mesh.patches]
    for index, patch in enumerate(mesh.patches):
        vertices = mesh.nodes[np.unique(mesh.patch_faces(index)) - 1]
        assert names[match_zone(vertices, mesh.patches)] == patch.name
    with pytest.raises(ResultsUnavailable, match="matches 0"):
        match_zone(np.array([[0.0, 0.0, 0.0], [0.1, 0.1, 0.1]]), mesh.patches)
    with pytest.raises(ResultsUnavailable, match="no vertices"):
        match_zone(np.empty((0, 3)), mesh.patches)


TRANSCRIPT = """
  iter  continuity  x-velocity  y-velocity  z-velocity      energy           k       omega     time/iter
   598  1.5507e+02  2.6984e-03  2.1160e-03  7.9943e-03  6.6347e-06  6.7387e-03  1.9891e-02  0:00:21  2
   599  1.5501e+02  2.6900e-03  2.1100e-03  7.9900e-03  6.6300e-06  6.7300e-03  1.9800e-02  0:00:10  1
"""


def test_residuals_are_read_from_the_last_transcript_row():
    iteration, residuals = parse_residuals(TRANSCRIPT)
    assert iteration == 599
    assert list(residuals) == ["continuity", "x-velocity", "y-velocity", "z-velocity",
                               "energy", "k", "omega"]
    assert residuals["energy"] == pytest.approx(6.63e-6)
    assert parse_residuals("no iterations here") is None
    headless = TRANSCRIPT.split("\n", 2)[2]
    assert "residual_0" in parse_residuals(headless)[1]


def test_wall_heat_is_read_without_the_volumetric_source():
    fluent_2026 = [{"wall_heat": [-21.7004027637131, "W"],
                    "wall_heat(User Energy Source)": [-344.2010942876103, "W"],
                    "wall_heat(without-sources)": [322.5006915238972, "W"]}]
    assert wall_heat_from_report(fluent_2026) == pytest.approx(322.5006915238972)
    assert wall_heat_from_report([{"wall_heat": [118.0, "W"]}]) == pytest.approx(118.0)
    ambiguous = [{"wall_heat": [1.0, "W"], "wall_heat(other)": [2.0, "W"]}]
    assert wall_heat_from_report(ambiguous) is None
    assert wall_heat_from_report([]) is None
    assert wall_heat_from_report([{"wall_heat": ["n/a", "W"]}]) is None
    assert wall_heat_from_report([{"wall_heat": [float("nan"), "W"]}]) is None


def test_the_ansys_root_comes_from_the_environment_or_is_refused(tmp_path):
    root = tmp_path / "v261"
    (root / "fluent").mkdir(parents=True)
    assert find_ansys_root({"AWP_ROOT261": str(root)}, registry=lambda: []) == root
    assert find_ansys_root({"SHELTER_ANSYS_ROOT": str(root)}, registry=lambda: []) == root
    assert find_ansys_root({}, registry=lambda: [str(root)]) == root
    with pytest.raises(ConfigurationError):
        find_ansys_root({"AWP_ROOT261": str(tmp_path / "missing")}, registry=lambda: [])
    with pytest.raises(ConfigurationError):
        find_ansys_root({}, registry=lambda: [])


def test_boundary_conditions_without_pressure_or_heat_flows_never_reach_fluent(tmp_path):
    export = closed_room_export()
    with pytest.raises(ResultsUnavailable, match="air pressure"):
        run_fluent(export.model_copy(update={"air_pressure_pa": None}), tmp_path)
    silent = export.model_copy(update={"internal_convective_gain_w": None,
                                       "infiltration_sensible_w": None,
                                       "mixing_sensible_w": None})
    with pytest.raises(ResultsUnavailable, match="heat flow"):
        run_fluent(silent, tmp_path)


@pytest.mark.skipif(os.environ.get("SHELTER_RUN_FLUENT") != "1",
                    reason="set SHELTER_RUN_FLUENT=1 to run ANSYS Fluent")
def test_fluent_solves_a_small_room(tmp_path):
    options = FluentOptions(processor_count=2, first_cell_m=0.05, largest_cell_m=0.3,
                            min_iterations=100, max_iterations=400)
    field = run_fluent(closed_room_export(), tmp_path, options)
    assert field.provenance.value == "cfd"
    assert -10.0 < field.mean_c < 10.0
    assert field.solver.cell_count > 0
