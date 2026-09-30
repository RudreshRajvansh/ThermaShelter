from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from shelter_engine.errors import ConfigurationError, EngineError
from shelter_engine.schema import MapdlLevel
from shelter_engine.mapdl import (GROUPS, INSIDE_FILM, STUDENT_NODE_LIMIT, Layer,
                                  MapdlCase, Opening, agreement, build_grid, find_mapdl,
                                  graded, grid_convergence, hourly_means, layered_axis,
                                  merge, parse_hourly, solair_c, study, uniform, write_deck,
                                  energyplus_standard_density, glazing_u_hourly,
                                  link_code)
from shelter_engine.mapdl_verify import (check_rectangle, check_step, check_wall,
                                         rectangle_deck, rectangle_series_c, step_deck,
                                         step_profile_c, wall_deck, wall_profile_c)

BRICK = Layer("brick", 0.23, 0.8, 1800.0, 840.0)
WOOL = Layer("wool", 0.05, 0.04, 30.0, 1030.0)
SLAB = Layer("slab", 0.1, 1.4, 2300.0, 880.0)
SOIL = Layer("soil", 1.0, 1.5, 1800.0, 900.0)


def make_case(hours: int = 24, openings=(), width: float = 4.0, depth: float = 3.0,
              height: float = 2.5, **changes) -> MapdlCase:
    outdoor = tuple(-5.0 + 5.0 * np.sin(2 * np.pi * np.arange(hours) / 24.0))
    values = dict(
        label="test", design_hash="abc", width_m=width, depth_m=depth, height_m=height,
        wall=(BRICK, WOOL), roof=(SLAB, WOOL), floor=(SLAB,), soil=SOIL,
        soil_margin_m=1.0, soil_depth_m=1.0, deep_ground_c=5.0,
        air_capacitance_j_k=1.0 * 1005.0 * width * depth * height,
        openings=tuple(openings),
        timestamps=tuple(f"2019-01-10T{hour % 24:02d}:00" for hour in range(hours)),
        outdoor_c=outdoor,
        solair_c={group: outdoor for group in ("south", "north", "east", "west", "roof",
                                               "ground")},
        outside_film_w_m2k={group: (20.0,) * hours for group in ("south", "north", "east",
                                                                  "west", "roof", "ground")},
        internal_gain_w=(100.0,) * hours, transmitted_solar_w=(0.0,) * hours,
        outdoor_air_w_k=tuple(10.0 + (hour % 3) for hour in range(hours)),
        window_ua_w_k=(5.0,) * hours,
        energyplus_air_c=(10.0,) * hours, energyplus_face_w={"south": (-50.0,) * hours})
    values.update(changes)
    return MapdlCase(**values)


def test_solair_equals_air_without_sun_or_cold_sky():
    outdoor = np.array([-10.0, 0.0, 5.0])
    equivalent, film = solair_c(outdoor, outdoor, np.array([0.0, 2.0, 5.0]),
                                np.zeros(3), 0.7, 0.9, 1.0)
    assert np.allclose(equivalent, outdoor)
    assert np.all(film > 4.0)
    assert film[2] > film[0]


def test_solair_rises_with_sun_and_falls_under_a_cold_sky():
    outdoor = np.array([0.0])
    sunny, film = solair_c(outdoor, outdoor, np.array([1.0]), np.array([600.0]), 0.7, 0.9,
                           0.5)
    assert sunny[0] == pytest.approx(0.7 * 600.0 / film[0])
    clear, _ = solair_c(outdoor, np.array([-30.0]), np.array([1.0]), np.zeros(1), 0.7,
                        0.9, 1.0)
    shaded, _ = solair_c(outdoor, np.array([-30.0]), np.array([1.0]), np.zeros(1), 0.7,
                         0.9, 0.0)
    assert clear[0] < 0.0
    assert shaded[0] == pytest.approx(0.0)
    negative, _ = solair_c(outdoor, outdoor, np.array([-3.0]), np.array([-50.0]), 0.7,
                           0.9, 1.0)
    assert negative[0] == pytest.approx(0.0)


def test_axis_builders():
    points = layered_axis(0.0, (BRICK, WOOL), -1, 0.08)
    assert points[0] == 0.0
    assert points[-1] == pytest.approx(-0.28)
    assert pytest.approx(-0.23) in points
    assert np.all(np.diff(points) < 0)
    assert uniform(0.0, 1.0, 0.3) == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])
    assert uniform(0.0, 0.1, 1.0) == pytest.approx([0.0, 0.1])
    grown = graded(0.0, 3.0, 0.2, 1.0)
    assert grown[0] == 0.0 and grown[-1] == 3.0
    steps = np.diff(grown)
    assert np.all(steps > 0) and steps[0] < steps[-1]
    assert graded(1.0, 1.0, 0.1, 0.5) == [1.0]
    assert graded(0.0, 0.05, 0.1, 0.5) == [0.0, 0.05]
    assert list(merge([0.0, 1.0], [0.5, 1.0 + 1e-9], cuts=[0.25])) == [0.0, 0.25, 0.5, 1.0]


def test_grid_inner_faces_match_the_room():
    case = make_case()
    grid = build_grid(case, "coarse")
    deck = write_deck(case, grid, cycles=1, substeps=1)
    areas = deck.face_area_m2
    assert areas["south"] == pytest.approx(4.0 * 2.5)
    assert areas["east"] == pytest.approx(3.0 * 2.5)
    assert areas["roof"] == pytest.approx(12.0)
    assert areas["floor"] == pytest.approx(12.0)
    assert deck.node_count < STUDENT_NODE_LIMIT
    assert deck.element_count == grid.element_count


def test_window_leaves_a_hole_in_the_wall():
    window = Opening("south", 1.0, 2.5, 0.9, 2.1, 1.8, 2.8)
    case = make_case(openings=[window])
    grid = build_grid(case, "coarse")
    assert np.isclose(grid.xs, 1.0).any() and np.isclose(grid.xs, 2.5).any()
    assert np.isclose(grid.zs, 0.9).any() and np.isclose(grid.zs, 2.1).any()
    deck = write_deck(case, grid, cycles=1, substeps=1)
    assert deck.face_area_m2["south"] == pytest.approx(4.0 * 2.5 - 1.8)
    assert deck.face_area_m2["north"] == pytest.approx(4.0 * 2.5)
    assert "R,49,2.5024438" in deck.text


def test_finer_levels_add_nodes_and_the_student_limit_is_enforced():
    case = make_case()
    coarse = build_grid(case, "coarse")
    medium = build_grid(case, "medium")
    assert medium.element_count > coarse.element_count
    with pytest.raises(EngineError, match="unknown mesh level"):
        build_grid(case, "extreme")
    huge = make_case(width=40.0, depth=30.0, height=6.0)
    with pytest.raises(EngineError):
        build_grid(huge, "fine")


def test_case_validation():
    with pytest.raises(EngineError, match="positive"):
        build_grid(make_case(width=0.0), "coarse")
    with pytest.raises(EngineError, match="no layers"):
        build_grid(make_case(wall=()), "coarse")
    with pytest.raises(EngineError, match="non physical"):
        build_grid(make_case(roof=(Layer("bad", 0.1, 0.0, 1.0, 1.0),)), "coarse")
    with pytest.raises(EngineError, match="missing value"):
        build_grid(make_case(internal_gain_w=(float("nan"),) * 24), "coarse")
    with pytest.raises(EngineError, match="match the timestamps"):
        build_grid(make_case(internal_gain_w=(0.0,) * 23), "coarse")
    with pytest.raises(EngineError, match="two hours"):
        build_grid(make_case(hours=1), "coarse")


def test_deck_steps_every_hour_of_every_cycle():
    case = make_case(hours=6)
    deck = write_deck(case, build_grid(case, "coarse"), cycles=2, substeps=3)
    assert deck.text.count("\nSOLVE\n") == 1 + 2 * 6
    assert f"TIME,{1 + 12 * 3600}" in deck.text
    assert "NSUBST,3,3,3" in deck.text
    assert deck.text.count("EALIVE,VFIRST") + deck.text.count("EKILL,VFIRST") >= 13
    for group in GROUPS:
        assert f"CM,IN_{group.upper()},ELEM" in deck.text
    with pytest.raises(EngineError):
        write_deck(case, build_grid(case, "coarse"), cycles=0)


def test_ventilation_links_follow_the_flow():
    case = make_case(hours=3, outdoor_air_w_k=(0.0, 10.0, 20.0))
    text = write_deck(case, build_grid(case, "coarse"), cycles=1, substeps=1).text
    assert "R,29,10.009775" in text
    steps = text.split("\nSOLVE\n")
    first_hour = steps[1]
    assert all(f"EKILL,VFIRST+{bit}" in first_hour for bit in range(10))
    last_hour = steps[3]
    assert all(f"EALIVE,VFIRST+{bit}" in last_hour for bit in range(10))


def test_link_codes_rebuild_the_conductance():
    peak = 133.0
    for value in (0.0, 1.0, 53.4, 95.2, 133.0, 150.0):
        code = link_code(value, peak)
        assert 0 <= code <= 1023
        rebuilt = sum(peak * 2 ** bit / 1023 for bit in range(10) if code >> bit & 1)
        assert rebuilt == pytest.approx(min(value, peak), abs=peak / 2046 + 1e-9)


def test_shutters_lower_the_window_conductance_only_while_closed():
    from shelter_engine.schema import ShutterSpec

    hours = np.arange(24)
    shutter = ShutterSpec(present=True, thermal_resistance_m2k_w=0.4, closed_from_hour=17,
                          closed_until_hour=8, compliance=1.0)
    values = glazing_u_hourly(2.0, shutter, hours)
    closed = 1.0 / (1.0 / 2.0 + 0.4)
    assert values[3] == pytest.approx(closed) and values[17] == pytest.approx(closed)
    assert values[8] == pytest.approx(2.0) and values[12] == pytest.approx(2.0)
    half = glazing_u_hourly(2.0, shutter.model_copy(update={"compliance": 0.5}), hours)
    assert half[3] == pytest.approx(0.5 * closed + 1.0)
    assert np.all(glazing_u_hourly(2.0, ShutterSpec(), hours) == 2.0)
    assert np.all(glazing_u_hourly(2.0, None, hours) == 2.0)


def test_energyplus_standard_density_falls_with_height():
    assert energyplus_standard_density(0.0) == pytest.approx(1.204, abs=2e-3)
    assert energyplus_standard_density(3256.0) == pytest.approx(0.81, abs=0.01)


def test_negative_flows_are_refused():
    with pytest.raises(EngineError, match="negative"):
        build_grid(make_case(window_ua_w_k=(-1.0,) * 24), "coarse")


def write_rows(path: Path, rows) -> Path:
    header = "cycle,hour,air_c,star_c," + ",".join(f"{group}_c" for group in GROUPS)
    widened = [tuple(row[:3]) + (row[2],) + tuple(row[3:]) if len(row) == 3 + len(GROUPS)
               else row for row in rows]
    path.write_text(header + "\n" + "\n".join(",".join(str(value) for value in row)
                                              for row in widened) + "\n", encoding="utf-8")
    return path


def test_parse_keeps_the_last_cycle_in_hour_order(tmp_path):
    case = make_case(hours=3)
    deck = write_deck(case, build_grid(case, "coarse"), cycles=2, substeps=1)
    rows = [(1, hour, 0.0) + (0.0,) * 6 for hour in range(3)]
    rows += [(2, hour, 10.0 + hour) + (11.0 + hour,) * 6 for hour in (2, 0, 1)]
    rows.append(("garbage",))
    path = write_rows(tmp_path / "mapdl_hourly.csv", rows)
    run = parse_hourly(path, case, deck, "coarse", 2, 5.0)
    assert run.air_c == (10.0, 11.0, 12.0)
    assert run.face_heat_w["roof"][0] == pytest.approx(INSIDE_FILM["roof"] * 12.0 * 1.0)


def test_parse_rejects_missing_hours_and_files(tmp_path):
    case = make_case(hours=3)
    deck = write_deck(case, build_grid(case, "coarse"), cycles=1, substeps=1)
    path = write_rows(tmp_path / "short.csv", [(1, 0, 1.0) + (1.0,) * 6])
    with pytest.raises(EngineError, match="1 of the 3 compared hours"):
        parse_hourly(path, case, deck, "coarse", 1, 1.0)
    with pytest.raises(EngineError, match="no hourly results"):
        parse_hourly(tmp_path / "absent.csv", case, deck, "coarse", 1, 1.0)


def test_agreement_and_hourly_means(tmp_path):
    assert hourly_means([1.0, 3.0, 5.0]).tolist() == [3.0, 2.0, 4.0]
    case = make_case(hours=3, energyplus_air_c=(2.0, 2.0, 2.0),
                     energyplus_face_w={"south": (100.0,) * 3, "roof": (0.0,) * 3})
    deck = write_deck(case, build_grid(case, "coarse"), cycles=1, substeps=1)
    rows = [(1, hour, 2.5) + (3.5,) * 6 for hour in range(3)]
    run = parse_hourly(write_rows(tmp_path / "rows.csv", rows), case, deck, "coarse", 1, 1.0)
    fit = agreement(case, run)
    assert fit.bias_k == pytest.approx(0.5)
    assert fit.rmse_k == pytest.approx(0.5)
    assert fit.meets_target
    assert fit.face_difference_pct["roof"] is None
    expected = INSIDE_FILM["south"] * 10.0 * 3 / 1000.0
    assert fit.face_kwh_mapdl["south"] == pytest.approx(expected)
    assert fit.face_difference_pct["south"] == pytest.approx(
        100.0 * (expected - 0.3) / 0.3)
    assert not agreement(case, run, target_rmse_k=0.1).meets_target


def test_grid_convergence_recovers_second_order():
    exact, ratio = 10.0, 1.5
    values = [exact + 0.8 * (ratio ** 2) ** level for level in (2, 1, 0)]
    result = grid_convergence(*values, ratio_coarse=ratio)
    assert result["order"] == pytest.approx(2.0)
    assert result["extrapolated"] == pytest.approx(exact)
    assert result["gci_fine"] == pytest.approx(1.25 * 0.8)
    assert grid_convergence(1.0, 2.0, 1.5, ratio)["order"] is None
    assert grid_convergence(1.0, 2.0, 2.0, ratio)["gci_fine"] == 0.0
    assert grid_convergence(3.0, 2.0, 1.5, 1.0)["order"] is None


def test_grid_convergence_with_unequal_ratios():
    exact, r32, r21 = 5.0, 1.27, 1.30
    h1 = 0.1
    h2, h3 = h1 * r21, h1 * r21 * r32
    values = [exact + 3.0 * h ** 2 for h in (h3, h2, h1)]
    result = grid_convergence(*values, ratio_coarse=r32, ratio_fine=r21)
    assert result["order"] == pytest.approx(2.0, rel=1e-6)
    assert result["extrapolated"] == pytest.approx(exact, rel=1e-9)


def test_study_uses_the_three_levels_and_the_time_step():
    def level(name, nodes, mean):
        return MapdlLevel(level=name, node_count=nodes, element_count=nodes, substeps=2,
                          runtime_s=1.0, mean_air_c=mean, minimum_air_c=mean - 5.0,
                          rmse_k=0.5)
    def mean(nodes):
        return 10.0 + 160.0 * nodes ** (-2.0 / 3.0)
    levels = [level("fine", 8000, mean(8000)), level("coarse", 1000, mean(1000)),
              level("medium", 3375, mean(3375))]
    halved = level("medium", 3375, mean(3375) + 0.05)
    result = study(levels, halved)
    assert result.refinement_ratio == pytest.approx((8000 / 3375) ** (1 / 3))
    assert result.order == pytest.approx(2.0, rel=1e-6)
    assert result.extrapolated == pytest.approx(10.0)
    assert result.time_step_difference_k == pytest.approx(0.05)
    assert study(levels[:2]) is None


def test_find_mapdl(monkeypatch, tmp_path):
    fake = tmp_path / "MAPDL261.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("SHELTER_MAPDL_EXE", str(fake))
    assert find_mapdl(tmp_path / "nowhere") == fake
    monkeypatch.delenv("SHELTER_MAPDL_EXE")
    folder = tmp_path / "v261" / "ansys" / "bin" / "winx64"
    folder.mkdir(parents=True)
    (folder / "MAPDL261.exe").write_text("", encoding="utf-8")
    assert find_mapdl(tmp_path / "v261").name == "MAPDL261.exe"
    with pytest.raises(ConfigurationError):
        find_mapdl(tmp_path / "empty")


def test_analytic_references():
    assert rectangle_series_c(np.array([0.5]), np.array([0.5]), 1.0, 1.0, 20.0)[0] == \
        pytest.approx(5.0, abs=1e-6)
    edge = rectangle_series_c(np.array([0.5]), np.array([0.0]), 1.0, 1.0, 20.0)[0]
    assert edge == pytest.approx(0.0, abs=1e-9)
    profile = wall_profile_c(np.array([0.0, 0.23, 0.28]), (BRICK, WOOL), 20.0, -20.0,
                             1 / 0.13, 25.0)
    resistance = 0.13 + 0.23 / 0.8 + 0.05 / 0.04 + 1 / 25.0
    flux = 40.0 / resistance
    assert profile[0] == pytest.approx(20.0 - 0.13 * flux)
    assert profile[1] == pytest.approx(profile[0] - 0.23 / 0.8 * flux)
    assert profile[2] == pytest.approx(-20.0 + flux / 25.0)
    step = step_profile_c(np.array([0.0, 10.0]), 3600.0, 1e-6, 0.0, 20.0)
    assert step[0] == pytest.approx(20.0)
    assert step[1] == pytest.approx(0.0)


def test_verification_decks():
    text, column = rectangle_deck(cells=8)
    assert column.count == 9 * 2 * 9
    assert text.count("\nE,") == 64
    text, column = wall_deck((BRICK, WOOL), 20.0, -20.0, 7.7, 25.0, 0.05)
    assert "MAT,1" in text and "MAT,2" in text
    assert column.xs[-1] == pytest.approx(0.28)
    text, column = step_deck(SLAB, 0.5, 0.01, 0.0, 20.0, 3, 2)
    assert text.count("\nSOLVE") == 3
    assert "IC,ALL,TEMP,0" in text


def _mapdl_or_skip() -> Path:
    try:
        return find_mapdl()
    except ConfigurationError:
        pytest.skip("ANSYS Mechanical APDL is not installed")


def test_mapdl_meets_the_analytic_checks(tmp_path):
    executable = _mapdl_or_skip()
    for result in (check_rectangle(tmp_path / "rectangle", executable),
                   check_wall((BRICK, WOOL), tmp_path / "wall", executable),
                   check_step(SLAB, tmp_path / "step", executable)):
        assert result.passed, (result.name, result.max_abs_k)


def test_report_round_trip_and_lookup(tmp_path):
    from shelter_engine.mapdl import (MapdlRun, build_report, find_reports, load_report,
                                      mapdl_version, write_report)

    case = make_case(hours=3, energyplus_air_c=(1.0, 2.0, 3.0))
    run = MapdlRun(level="coarse", hours=3, air_c=(1.0, 2.0, 3.0),
                   surface_c={group: (0.0,) * 3 for group in GROUPS},
                   face_heat_w={group: (0.0,) * 3 for group in GROUPS}, node_count=10,
                   element_count=8, runtime_s=1.0, cycles=1)
    report = build_report(case, run, "leh", "2026 R1", 2)
    assert report.agreement.faces[0].group == "south"
    assert report.mapdl_air_c == pytest.approx((2.0, 1.5, 2.5))
    path = write_report(report, tmp_path / "mapdl")
    assert load_report(path) == report
    assert find_reports(tmp_path / "mapdl", "abc") == (path,)
    assert find_reports(tmp_path / "absent", "abc") == ()
    assert find_reports(None, "abc") == ()
    with pytest.raises(EngineError):
        load_report(tmp_path / "none.json")
    assert mapdl_version(Path("MAPDL261.exe")) == "2026 R1"
    assert mapdl_version(Path("mapdl")) == "unknown"


def test_report_schema_refuses_inconsistent_numbers():
    from pydantic import ValidationError

    from shelter_engine.schema import MapdlAgreement, MapdlCheck

    with pytest.raises(ValidationError):
        MapdlAgreement(hours=3, mae_k=0.5, rmse_k=0.6, bias_k=0.0, max_abs_k=1.0,
                       target_rmse_k=1.0, meets_target=False)
    with pytest.raises(ValidationError):
        MapdlAgreement(hours=3, mae_k=0.9, rmse_k=0.6, bias_k=0.0, max_abs_k=1.0,
                       target_rmse_k=1.0, meets_target=True)
    with pytest.raises(ValidationError):
        MapdlCheck(name="x", description="y", points=1, max_abs_k=0.2, tolerance_k=0.1,
                   passed=True, runtime_s=1.0)


def test_a_lead_in_runs_daily_then_hourly_and_writes_only_the_compared_hours():
    from shelter_engine.mapdl import solve_plan

    case = make_case(hours=24 * 5, lead_hours=24 * 4)
    plan = solve_plan(case, 1)
    daily = [step for step in plan.steps if step[1] == 86400.0]
    hourly = [step for step in plan.steps if step[1] == 3600.0]
    assert len(daily) == 2 and len(hourly) == 24 * 3
    assert daily[0][0] == slice(0, 24) and daily[1][0] == slice(24, 48)
    written = [step[3] for step in plan.steps if step[3] is not None]
    assert written == list(range(-1, 24))
    assert plan.initial == slice(0, 24)
    with pytest.raises(EngineError):
        solve_plan(case, 2)
    with pytest.raises(EngineError, match="lead in"):
        build_grid(make_case(hours=24, lead_hours=23), "coarse")
    deck = write_deck(case, build_grid(case, "coarse"), cycles=1, substeps=1)
    assert deck.text.count("\nSOLVE\n") == 1 + 2 + 72
    assert "TOUT(25)" in deck.text and "HOUT(1)=-1" in deck.text


def test_cycles_without_a_lead_in_keep_the_hour_before_for_the_average():
    from shelter_engine.mapdl import solve_plan

    plan = solve_plan(make_case(hours=4), 2)
    written = [step[3] for step in plan.steps if step[3] is not None]
    assert written == [-1, 0, 1, 2, 3]
    assert [step[3] for step in solve_plan(make_case(hours=4), 1).steps] == [0, 1, 2, 3]


def test_the_hour_before_sets_the_first_average(tmp_path):
    case = make_case(hours=5, lead_hours=2, energyplus_air_c=(0.0, 0.0, 4.0, 5.0, 6.0))
    deck = write_deck(case, build_grid(case, "coarse"), cycles=1, substeps=1)
    rows = [(1, -1, 2.0) + (2.0,) * 6] + [(1, hour, 4.0 + hour) + (4.0,) * 6
                                        for hour in range(3)]
    run = parse_hourly(write_rows(tmp_path / "lead.csv", rows), case, deck, "coarse", 1, 1.0)
    assert run.air_before_c == 2.0
    assert hourly_means(run.air_c, run.air_before_c).tolist() == [3.0, 4.5, 5.5]
    fit = agreement(case, run)
    assert fit.hours == 3
    assert fit.bias_k == pytest.approx(((3 - 4) + (4.5 - 5) + (5.5 - 6)) / 3)


def test_glazing_passes_in_only_the_absorbed_sun_it_reports():
    import pandas as pd

    from shelter_engine.mapdl import glazing_inward_solar

    index = pd.date_range("2019-01-10", periods=3, freq="h")

    class Output:
        def wide(self, name):
            values = {"Surface Window Heat Gain Energy": [0.0, 500.0, 900.0],
                      "Surface Window Heat Loss Energy": [100.0, 0.0, 0.0]}[name]
            return pd.DataFrame({"W1": [value * 3600.0 for value in values]}, index=index)

    solar = np.array([0.0, 400.0, 1000.0])
    ua = np.array([10.0, 10.0, 10.0])
    air = np.array([10.0, 10.0, 10.0])
    outdoor = np.array([0.0, 0.0, 0.0])
    gain = glazing_inward_solar(Output(), solar, ua, air, outdoor)
    assert gain.tolist() == [0.0, 200.0, 0.0]


def test_prior_months_step_through_the_year_before_the_lead_in():
    import pandas as pd

    from shelter_engine.mapdl import Prior, prior_months, solve_plan

    index = pd.date_range("2019-01-01", "2019-12-31 23:00", freq="h")
    frame = pd.DataFrame({
        "dry_bulb_c": 10.0 * np.sin(2 * np.pi * (index.dayofyear - 110) / 365.0),
        "horizontal_infrared_wh_m2": 250.0, "wind_speed_m_s": 2.0,
        "global_horizontal_wh_m2": 0.0}, index=index)
    months = prior_months(frame, pd.Timestamp("2018-12-11"))
    assert len(months) == 12
    assert months[0][0] == 31.0 and months[-1][0] == 10.0
    assert months[6][1] > months[0][1]
    assert all(ground < outdoor for _, outdoor, ground in months)
    assert prior_months(frame, pd.Timestamp("2019-03-01"))[-1][0] == 28.0
    case = make_case(hours=24 * 4, lead_hours=24 * 3, prior=months)
    plan = solve_plan(case, 1)
    assert isinstance(plan.initial, Prior)
    assert sum(1 for step in plan.steps if isinstance(step[0], Prior)) == 12
    text = write_deck(case, build_grid(case, "coarse"), cycles=1, substeps=1).text
    assert "NSUBST,7,7,7" in text
    with pytest.raises(EngineError, match="prior month"):
        build_grid(make_case(prior=((0.0, 1.0, 1.0),)), "coarse")


def test_surfaces_reach_the_air_by_convection_and_the_star_node_by_radiation(tmp_path):
    from shelter_engine.mapdl import INSIDE_CONVECTION, INSIDE_RADIATION

    case = make_case(hours=3, radiant_gain_w=(50.0, 60.0, 70.0),
                     energyplus_operative_c=(10.0, 10.0, 10.0))
    deck = write_deck(case, build_grid(case, "coarse"), cycles=1, substeps=1)
    assert "CM,IR_SOUTH,ELEM" in deck.text and "CM,IN_SOUTH,ELEM" in deck.text
    assert f"SFE,ALL,1,CONV,0,{INSIDE_RADIATION:.8g}" in deck.text
    assert "TSTAR" in deck.text
    assert INSIDE_FILM["south"] == pytest.approx(1 / 0.13, rel=0.02)
    assert INSIDE_FILM["roof"] == pytest.approx(1 / 0.10, rel=0.02)
    assert INSIDE_FILM["floor"] == pytest.approx(1 / 0.17, rel=0.02)
    rows = [(1, hour, 10.0, 14.0) + (12.0,) * 6 for hour in range(3)]
    run = parse_hourly(write_rows(tmp_path / "star.csv", rows), case, deck, "coarse", 1, 1.0)
    expected = deck.face_area_m2["north"] * (INSIDE_CONVECTION["north"] * 2.0
                                             - INSIDE_RADIATION * 2.0)
    assert run.face_heat_w["north"][0] == pytest.approx(expected)
    assert run.star_c == (14.0, 14.0, 14.0)
    fit = agreement(case, run, reference="operative")
    assert fit.bias_k == pytest.approx(12.0 - 10.0)
    with pytest.raises(EngineError, match="match the timestamps"):
        build_grid(make_case(radiant_gain_w=(1.0,)), "coarse")


def test_the_floor_keeps_its_absorbed_sun_and_the_rest_spreads_by_area():
    from shelter_engine.mapdl import face_solar_shares, solar_shares

    weights = {"floor": {1: 6.0, 2: 6.0}, "roof": {3: 12.0}, "south": {1: 2.0, 4: 8.0}}
    shares = solar_shares(weights, 0.7)
    assert sum(shares.values()) == pytest.approx(1.0)
    floor = 0.7 + 0.3 * 12.0 / 34.0
    assert shares[2] == pytest.approx(floor / 2 + 0.0)
    assert shares[1] == pytest.approx(floor / 2 + 0.3 * 10.0 / 34.0 * 0.2)
    assert solar_shares(weights, 1.0)[3] == 0.0
    faces = face_solar_shares({"floor": 12.0, "roof": 12.0, "south": 10.0}, 0.7)
    assert sum(faces.values()) == pytest.approx(1.0)
    assert faces["floor"] == pytest.approx(floor)
    assert face_solar_shares({}, 0.7) == {"floor": 1.0}
    with pytest.raises(EngineError):
        solar_shares({"roof": {1: 1.0}}, 0.7)
