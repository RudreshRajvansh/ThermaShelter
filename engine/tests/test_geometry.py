from __future__ import annotations

import numpy as np
import pytest

import fixtures
from shelter_engine.constructions import build_constructions
from shelter_engine.errors import GeometryError
from shelter_engine.geometry import (SUNSPACE, TROMBE_GAP, VESTIBULE,
                                     azimuth_tilt, build_geometry,
                                     build_horizon_shading, newell_normal,
                                     orient_polygon, polygon_area,
                                     rotate_about_z, triangulate_fan,
                                     unit_normal, validate_model,
                                     window_dimensions)
from shelter_engine.mesh import build_mesh, facade_glazing_ratio
from shelter_engine.schema import (Arrangement, ArrangementParams, Facade,
                                   PlanSpec, SurfaceKind, WindowSpec)


def model_for(arrangement, library, **overrides):
    design = fixtures.design(arrangement, **overrides)
    constructions = build_constructions(design, library)
    return design, constructions, build_geometry(design, constructions.names,
                                                 design_hash="test")


def test_polygon_area_and_normal_are_consistent():
    square = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 0.0, 3.0), (0.0, 0.0, 3.0)]
    assert polygon_area(square) == pytest.approx(6.0)
    normal = unit_normal(square)
    assert abs(float(np.dot(normal, np.array([0.0, 1.0, 0.0])))) == pytest.approx(1.0)


def test_orient_polygon_reverses_wrong_winding():
    square = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 0.0, 3.0), (0.0, 0.0, 3.0)]
    ordered = orient_polygon(square, (0.0, -1.0, 0.0))
    assert unit_normal(ordered)[1] == pytest.approx(-1.0)
    reversed_order = orient_polygon(square[::-1], (0.0, -1.0, 0.0))
    assert ordered == reversed_order


def test_orient_polygon_starts_at_upper_left_seen_from_outside():
    square = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 0.0, 3.0), (0.0, 0.0, 3.0)]
    ordered = orient_polygon(square, (0.0, -1.0, 0.0))
    assert ordered[0] == (0.0, 0.0, 3.0)


def test_orient_polygon_rejects_a_perpendicular_expectation():
    square = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 0.0, 3.0), (0.0, 0.0, 3.0)]
    with pytest.raises(GeometryError):
        orient_polygon(square, (0.0, 0.0, 1.0))


def test_azimuth_and_tilt_conventions():
    assert azimuth_tilt((0.0, -1.0, 0.0)) == (pytest.approx(180.0), pytest.approx(90.0))
    assert azimuth_tilt((1.0, 0.0, 0.0)) == (pytest.approx(90.0), pytest.approx(90.0))
    assert azimuth_tilt((0.0, 0.0, 1.0))[1] == pytest.approx(0.0)
    assert azimuth_tilt((0.0, 0.0, -1.0))[1] == pytest.approx(180.0)


def test_rotation_is_clockwise_from_north():
    rotated = rotate_about_z([(0.0, -1.0, 0.0)], 10.0)[0]
    assert azimuth_tilt(rotated)[0] == pytest.approx(190.0)


def test_triangulate_fan_covers_the_polygon():
    assert triangulate_fan(4) == (0, 1, 2, 0, 2, 3)
    with pytest.raises(GeometryError):
        triangulate_fan(2)


def test_newell_normal_is_zero_for_a_degenerate_polygon():
    line = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
    assert float(np.linalg.norm(newell_normal(line))) == pytest.approx(0.0)
    with pytest.raises(GeometryError):
        unit_normal(line)


@pytest.mark.parametrize("arrangement", list(Arrangement))
def test_every_arrangement_builds_and_validates(arrangement, library):
    _, _, model = model_for(arrangement, library)
    validate_model(model)
    assert len(model.zones) == (1 if arrangement is Arrangement.SINGLE_ROOM else 2)
    assert len({polygon.name for polygon in model.polygons}) == len(model.polygons)


@pytest.mark.parametrize("arrangement", list(Arrangement))
def test_surface_orientations_match_expectations(arrangement, library):
    _, _, model = model_for(arrangement, library)
    for polygon in model.polygons:
        azimuth, tilt = polygon.azimuth_tilt
        if polygon.kind is SurfaceKind.FLOOR:
            assert tilt == pytest.approx(180.0)
        elif polygon.kind is SurfaceKind.ROOF and polygon.parent is None:
            assert tilt == pytest.approx(0.0)
        elif polygon.facade is not None and polygon.kind in (
                SurfaceKind.WALL, SurfaceKind.PARTITION):
            assert tilt == pytest.approx(90.0)
            assert azimuth == pytest.approx(polygon.facade.nominal_azimuth_deg)


@pytest.mark.parametrize("azimuth", [0.0, 45.0, 150.0, 180.0, 270.0, 359.9])
def test_building_rotation_moves_every_facade_together(azimuth, library):
    plan = PlanSpec(floor_area_m2=42.0, aspect_ratio=1.8, ceiling_height_m=2.7,
                    azimuth_deg=azimuth)
    _, _, model = model_for(Arrangement.SINGLE_ROOM, library, plan=plan)
    south = model.by_name("ROOM_WALL_SOUTH")
    north = model.by_name("ROOM_WALL_NORTH")
    assert south.azimuth_tilt[0] == pytest.approx(azimuth, abs=1e-6)
    assert north.azimuth_tilt[0] == pytest.approx((azimuth + 180.0) % 360.0, abs=1e-6)


def test_interzone_pairs_are_reciprocal_and_opposed(library):
    for arrangement in (Arrangement.SUNSPACE, Arrangement.TROMBE_WALL,
                        Arrangement.VESTIBULE):
        _, _, model = model_for(arrangement, library)
        pairs = [p for p in model.polygons if p.outside_boundary == "Surface"]
        assert pairs
        for polygon in pairs:
            partner = model.by_name(polygon.boundary_object)
            assert partner.boundary_object == polygon.name
            assert partner.zone != polygon.zone
            assert polygon.area_m2 == pytest.approx(partner.area_m2)
            dot = float(np.dot(np.asarray(polygon.normal),
                               np.asarray(partner.normal)))
            assert dot == pytest.approx(-1.0, abs=1e-6)


def test_interzone_partner_uses_the_reversed_construction(library):
    _, _, model = model_for(Arrangement.SUNSPACE, library)
    room_side = model.by_name("ROOM_WALL_SOUTH")
    sun_side = model.by_name(room_side.boundary_object)
    assert sun_side.construction == f"{room_side.construction}_REV"


def test_windows_sit_inside_their_parent(library):
    for arrangement in list(Arrangement):
        _, _, model = model_for(arrangement, library)
        for window in model.windows:
            parent = model.by_name(window.parent)
            assert window.area_m2 < parent.area_m2
            assert float(np.dot(np.asarray(window.normal),
                                np.asarray(parent.normal))) == pytest.approx(1.0)


def test_window_dimensions_respect_margins():
    width, height = window_dimensions(6.0, 2.7, 0.9, 0.3)
    assert width <= 6.0 - 2 * 0.15 + 1e-9
    assert height <= 2.7 - 0.9 - 0.15 + 1e-9
    assert width * height == pytest.approx(0.3 * 6.0 * 2.7, rel=1e-6)


def test_window_dimensions_refuse_an_impossible_wall():
    with pytest.raises(GeometryError):
        window_dimensions(0.4, 2.7, 0.9, 0.3)


def test_wwr_that_cannot_fit_is_rejected_at_the_schema(library):
    with pytest.raises(Exception):
        fixtures.design(Arrangement.SINGLE_ROOM, windows={
            Facade.SOUTH: WindowSpec(wwr=0.85, glazing_id="single_clear_4mm",
                                     sill_height_m=1.6)})


def test_trombe_gap_is_a_separate_thin_zone(library):
    design, _, model = model_for(Arrangement.TROMBE_WALL, library)
    gap = model.zone(TROMBE_GAP)
    assert gap.inside_convection_algorithm == "TrombeWall"
    assert gap.volume_m3 > 0.0
    expected_depth = design.arrangement_params.trombe_gap_m
    low, high = model.bounds()
    assert high[1] - low[1] == pytest.approx(design.plan.depth_m + expected_depth,
                                             rel=1e-6)


def test_narrow_trombe_gap_raises_a_warning(library):
    params = ArrangementParams(trombe_gap_m=0.03, trombe_glazing_id="single_clear_4mm")
    _, _, model = model_for(Arrangement.TROMBE_WALL, library,
                            arrangement_params=params)
    assert any(item.code == "trombe_gap_narrow" for item in model.warnings)


def test_partial_width_trombe_splits_the_south_wall(library):
    params = ArrangementParams(trombe_width_fraction=0.6,
                               trombe_glazing_id="single_clear_4mm")
    _, _, model = model_for(Arrangement.TROMBE_WALL, library,
                            arrangement_params=params)
    names = {polygon.name for polygon in model.polygons}
    assert {"ROOM_WALL_SOUTH_L", "ROOM_WALL_SOUTH_C", "ROOM_WALL_SOUTH_R"} <= names


def test_full_width_trombe_produces_no_side_segments(library):
    params = ArrangementParams(trombe_width_fraction=1.0,
                               trombe_glazing_id="single_clear_4mm")
    _, _, model = model_for(Arrangement.TROMBE_WALL, library,
                            arrangement_params=params)
    names = {polygon.name for polygon in model.polygons}
    assert "ROOM_WALL_SOUTH_C" in names
    assert "ROOM_WALL_SOUTH_L" not in names


def test_sunspace_has_a_glazed_roof_when_requested(library):
    _, _, model = model_for(Arrangement.SUNSPACE, library)
    assert any(polygon.name == f"{SUNSPACE}_ROOF_WIN" for polygon in model.polygons)


def test_sunspace_without_a_glazed_roof(library):
    params = ArrangementParams(sunspace_glazed_roof=False,
                               sunspace_glazing_id="single_clear_4mm")
    _, _, model = model_for(Arrangement.SUNSPACE, library,
                            arrangement_params=params)
    assert not any(polygon.name.endswith("ROOF_WIN") for polygon in model.polygons)


def test_vestibule_occupies_its_facade(library):
    _, _, model = model_for(Arrangement.VESTIBULE, library)
    assert model.zone(VESTIBULE).volume_m3 > 0.0
    assert any(polygon.zone == VESTIBULE and polygon.kind is SurfaceKind.FLOOR
               for polygon in model.polygons)


def test_horizon_shading_skips_flat_sectors(library):
    warnings = []
    from shelter_engine.schema import HorizonProfile
    profile = HorizonProfile(
        azimuths_deg=(0.0, 90.0, 180.0, 270.0),
        blocked_altitude_deg=(0.0, 12.0, 0.2, 30.0),
        source=fixtures.SYNTHETIC)
    shading = build_horizon_shading(profile, warnings)
    assert len(shading) == 2
    assert [item.code for item in warnings] == ["horizon_profile_coarse"]


def test_horizon_shading_accepts_a_fine_profile():
    from shelter_engine.schema import HorizonProfile
    warnings = []
    profile = HorizonProfile(
        azimuths_deg=tuple(float(index * 10) for index in range(36)),
        blocked_altitude_deg=tuple(0.0 if index % 2 else 15.0 for index in range(36)),
        source=fixtures.SYNTHETIC)
    shading = build_horizon_shading(profile, warnings)
    assert len(shading) == 18
    assert not warnings


def test_horizon_panels_face_the_site(library):
    from shelter_engine.schema import HorizonProfile
    profile = HorizonProfile(
        azimuths_deg=tuple(float(index * 10) for index in range(36)),
        blocked_altitude_deg=tuple(20.0 for _ in range(36)),
        source=fixtures.SYNTHETIC)
    shading = build_horizon_shading(profile, [])
    for panel in shading:
        centroid = np.asarray(panel.vertices, dtype=float).mean(axis=0)
        normal = unit_normal(panel.vertices)
        assert float(np.dot(normal[:2], -centroid[:2])) > 0.0


def test_mesh_matches_the_simulated_surfaces(library):
    for arrangement in list(Arrangement):
        design, constructions, model = model_for(arrangement, library)
        payload = build_mesh(model, constructions, library)
        assert set(payload.surface_names) == {p.name for p in model.polygons}
        for surface in payload.surfaces:
            assert surface.colour.startswith("#")
            assert len(surface.positions) % 3 == 0
            assert max(surface.indices) < len(surface.positions) // 3


def test_mesh_windows_are_translucent_and_offset(library):
    design, constructions, model = model_for(Arrangement.SINGLE_ROOM, library)
    payload = build_mesh(model, constructions, library)
    windows = [surface for surface in payload.surfaces
               if surface.kind is SurfaceKind.WINDOW]
    assert windows
    for surface in windows:
        assert surface.opacity < 1.0


def test_facade_glazing_ratio_is_bounded(library):
    _, _, model = model_for(Arrangement.SINGLE_ROOM, library)
    ratios = facade_glazing_ratio(model)
    assert ratios
    assert all(0.0 <= value <= 1.0 for value in ratios.values())


def test_zone_volumes_match_the_plan(library):
    design, _, model = model_for(Arrangement.SINGLE_ROOM, library)
    assert model.zone("ROOM").volume_m3 == pytest.approx(design.plan.volume_m3)


def test_plan_dimensions_reproduce_the_area():
    plan = PlanSpec(floor_area_m2=42.0, aspect_ratio=1.8)
    assert plan.width_m * plan.depth_m == pytest.approx(42.0)
    assert plan.width_m / plan.depth_m == pytest.approx(1.8)


def test_geometry_is_deterministic(library):
    _, _, first = model_for(Arrangement.SUNSPACE, library)
    _, _, second = model_for(Arrangement.SUNSPACE, library)
    assert [p.name for p in first.polygons] == [p.name for p in second.polygons]
    assert [p.vertices for p in first.polygons] == [p.vertices for p in second.polygons]
