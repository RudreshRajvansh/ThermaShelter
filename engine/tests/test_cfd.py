from __future__ import annotations

import math

import numpy as np
import pytest
from pydantic import ValidationError

from cfd_fixtures import DESIGN_HASH, box_export, cell_cloud, solver_record
from shelter_engine.cfd import (BOUNDARY_SUFFIX, FIELD_SUFFIX, build_cfd_field,
                                export_bounds, find_boundary_conditions,
                                find_cfd_fields, label_of, load_cfd_field,
                                sample_to_grid, write_cfd_export, write_cfd_field)
from shelter_engine.errors import ResultsUnavailable
from shelter_engine.hashing import stable_hash
from shelter_engine.schema import CFDField

BOUNDS = ((0.0, 0.0, 0.0), (4.0, 3.0, 2.5))


def test_every_sample_is_a_solver_cell_value():
    centres, temperature, speed = cell_cloud()
    temperatures, speeds = sample_to_grid(centres, temperature, *BOUNDS, (9, 7, 6),
                                          speed)
    cells = {round(float(value), 9) for value in temperature}
    cell_speeds = {round(float(value), 9) for value in speed}
    assert all(value is not None for value in temperatures)
    assert {round(value, 9) for value in temperatures} <= cells
    assert {round(value, 9) for value in speeds} <= cell_speeds


def test_grid_order_runs_x_fastest_then_y_then_z():
    centres, temperature, _ = cell_cloud()
    dims = (5, 4, 3)
    temperatures, _ = sample_to_grid(centres, temperature, *BOUNDS, dims)
    nx, ny, _ = dims
    bottom = temperatures[0]
    top = temperatures[2 * nx * ny]
    assert top > bottom
    assert temperatures[nx - 1] == pytest.approx(bottom)


def test_points_outside_the_air_volume_stay_empty():
    centres, temperature, _ = cell_cloud()
    temperatures, speeds = sample_to_grid(centres, temperature, (0.0, 0.0, 0.0),
                                          (12.0, 3.0, 2.5), (25, 4, 4))
    assert speeds is None
    assert any(value is None for value in temperatures)
    assert any(value is not None for value in temperatures)
    nx, ny = 25, 4
    far = [temperatures[z * nx * ny + y * nx + (nx - 1)]
           for z in range(4) for y in range(ny)]
    assert all(value is None for value in far)


def test_non_finite_cells_are_dropped_rather_than_sampled():
    centres, temperature, _ = cell_cloud()
    temperature = temperature.copy()
    temperature[::7] = np.nan
    temperatures, _ = sample_to_grid(centres, temperature, *BOUNDS, (6, 5, 4))
    assert all(value is not None and math.isfinite(value) for value in temperatures)


@pytest.mark.parametrize("case", ["flat_centres", "length", "speed_length", "dims",
                                  "bounds", "all_nan", "gap"])
def test_sampling_refuses_bad_inputs(case):
    centres, temperature, speed = cell_cloud()
    kwargs = dict(centres=centres, temperature_c=temperature, bounds_min=BOUNDS[0],
                  bounds_max=BOUNDS[1], dims=(4, 4, 4))
    if case == "flat_centres":
        kwargs["centres"] = centres[:, :2]
    elif case == "length":
        kwargs["temperature_c"] = temperature[:-1]
    elif case == "speed_length":
        kwargs["speed_m_s"] = speed[:-1]
    elif case == "dims":
        kwargs["dims"] = (1, 4, 4)
    elif case == "bounds":
        kwargs["bounds_max"] = (4.0, 0.0, 2.5)
    elif case == "all_nan":
        kwargs["temperature_c"] = np.full_like(temperature, np.nan)
    elif case == "gap":
        kwargs["max_gap_m"] = -1.0
    with pytest.raises(ResultsUnavailable):
        sample_to_grid(**kwargs)


def test_field_is_built_from_the_solver_cells_and_the_boundary_it_used():
    export = box_export()
    centres, temperature, speed = cell_cloud()
    field = build_cfd_field(export, centres, temperature, solver_record(), speed,
                            dims=(8, 6, 5))
    finite = [value for value in field.temperature_c if value is not None]
    assert field.provenance.value == "cfd"
    assert field.boundary_hash == stable_hash(export)
    assert field.min_c == pytest.approx(min(finite))
    assert field.max_c == pytest.approx(max(finite))
    assert field.energyplus_zone_air_c == pytest.approx(5.0)
    assert field.max_speed_m_s == pytest.approx(max(v for v in field.speed_m_s if v is not None))
    assert field.bounds_min == BOUNDS[0] and field.bounds_max == BOUNDS[1]
    assert "ANSYS Fluent" in field.source.citation
    assert not any("did not meet" in note for note in field.notes)


def test_an_unconverged_field_says_so_first():
    centres, temperature, _ = cell_cloud()
    field = build_cfd_field(box_export(), centres, temperature,
                            solver_record(converged=False), dims=(4, 4, 4))
    assert "did not meet its convergence criteria" in field.notes[0]
    assert field.speed_m_s is None and field.max_speed_m_s is None


def test_cells_that_miss_the_geometry_are_not_a_field():
    centres, temperature, _ = cell_cloud()
    with pytest.raises(ResultsUnavailable):
        build_cfd_field(box_export(), centres + 100.0, temperature, solver_record(),
                        dims=(4, 4, 4))


def test_boundary_geometry_must_enclose_a_volume():
    with pytest.raises(ResultsUnavailable):
        export_bounds(box_export(flat=True))


@pytest.mark.parametrize("change", ["short", "empty", "minimum", "mean", "speed",
                                    "negative_speed", "bounds", "dims", "provenance"])
def test_field_validation_refuses_inconsistent_payloads(change):
    centres, temperature, speed = cell_cloud()
    field = build_cfd_field(box_export(), centres, temperature, solver_record(), speed,
                            dims=(4, 4, 4))
    payload = field.model_dump(mode="json")
    if change == "short":
        payload["temperature_c"] = payload["temperature_c"][:-1]
    elif change == "empty":
        payload["temperature_c"] = [None] * len(payload["temperature_c"])
    elif change == "minimum":
        payload["min_c"] = payload["min_c"] - 1.0
    elif change == "mean":
        payload["mean_c"] = payload["max_c"] + 1.0
    elif change == "speed":
        payload["max_speed_m_s"] = None
    elif change == "negative_speed":
        payload["speed_m_s"] = [-1.0] * len(payload["speed_m_s"])
    elif change == "bounds":
        payload["bounds_max"] = payload["bounds_min"]
    elif change == "dims":
        payload["dims"] = [1, 16, 4]
    elif change == "provenance":
        payload["provenance"] = "derived"
    with pytest.raises(ValidationError):
        CFDField.model_validate(payload)


def test_files_round_trip_and_never_cross_match(tmp_path):
    export = box_export()
    centres, temperature, _ = cell_cloud()
    field = build_cfd_field(export, centres, temperature, solver_record(),
                            dims=(4, 4, 4))
    boundary_path = write_cfd_export(export, tmp_path)
    field_path = write_cfd_field(field, tmp_path)
    (tmp_path / f"{DESIGN_HASH}_{FIELD_SUFFIX}").write_text("{}", encoding="utf-8")
    (tmp_path / f"{DESIGN_HASH}_coldest_night.json").write_text("{}", encoding="utf-8")

    assert boundary_path.name.endswith(BOUNDARY_SUFFIX)
    assert find_boundary_conditions(tmp_path, DESIGN_HASH) == (boundary_path,)
    assert find_cfd_fields(tmp_path, DESIGN_HASH) == (field_path,)
    assert find_cfd_fields(tmp_path, "f" * 32) == ()
    assert find_cfd_fields(tmp_path, "*") == ()
    assert find_cfd_fields(tmp_path, "") == ()
    assert find_cfd_fields(tmp_path / "missing", DESIGN_HASH) == ()
    assert label_of(field_path, DESIGN_HASH) == "coldest_night"
    assert label_of(boundary_path, DESIGN_HASH) == "coldest_night"
    assert load_cfd_field(field_path) == field
    with pytest.raises(ResultsUnavailable):
        load_cfd_field(tmp_path / "absent.field.json")
    with pytest.raises(ResultsUnavailable):
        label_of(tmp_path / "notes.txt", DESIGN_HASH)


def test_net_air_heat_source_adds_only_reported_flows():
    export = box_export()
    assert export.net_air_heat_source_w == pytest.approx(-500.0)
    silent = export.model_copy(update={"internal_convective_gain_w": None,
                                       "infiltration_sensible_w": None})
    assert silent.net_air_heat_source_w is None
