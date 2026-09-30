from __future__ import annotations

import json
import sqlite3

import pytest

import fixtures
from shelter_engine.baseline import (check_library_covers_references,
                                     load_references)
from shelter_engine.constructions import (build_constructions,
                                          phase_change_curve)
from shelter_engine.costing import (buildability, cost_breakdown,
                                    envelope_u_values, glazing_u_values)
from shelter_engine.errors import MaterialError
from shelter_engine.geometry import build_geometry
from shelter_engine.materials import (MaterialLibrary, R_SURFACE_INSIDE_WALL,
                                      R_SURFACE_OUTSIDE, material_from_row)
from shelter_engine.schema import (Arrangement, ConstructionSpec, LayerSpec,
                                   OptimisationConstraints, PCMSpec, SourceStatus)


def _row(**overrides):
    payload = {
        "id": "test_material", "name": "Test material", "conductivity_w_mk": 0.5,
        "density_kg_m3": 1500.0, "specific_heat_j_kgk": 900.0, "cost_inr_m3": 1000.0,
        "colour": "#aabbcc", "source": "Test source", "source_status": "verified",
    }
    payload.update(overrides)
    return payload


def test_material_row_parses_aliases():
    material = material_from_row(_row(k=None, conductivity=0.7,
                                      conductivity_w_mk=None))
    assert material.conductivity_w_mk == 0.7
    assert material.source.status is SourceStatus.VERIFIED


def test_material_without_a_source_is_refused():
    row = _row()
    row.pop("source")
    with pytest.raises(MaterialError, match="no source"):
        material_from_row(row)


def test_material_without_a_colour_is_refused():
    row = _row()
    row.pop("colour")
    with pytest.raises(MaterialError, match="display colour"):
        material_from_row(row)


def test_material_with_a_missing_property_is_refused():
    row = _row(density_kg_m3=None)
    with pytest.raises(MaterialError, match="missing"):
        material_from_row(row)


def test_material_with_a_bad_colour_is_refused():
    with pytest.raises(MaterialError):
        material_from_row(_row(colour="not a colour"))


def test_material_with_negative_conductivity_is_refused():
    with pytest.raises(MaterialError):
        material_from_row(_row(conductivity_w_mk=-1.0))


def test_conductivity_range_must_bracket_the_nominal():
    with pytest.raises(MaterialError):
        material_from_row(_row(conductivity_low=1.0, conductivity_high=2.0))


def test_local_availability_accepts_several_spellings():
    assert material_from_row(_row(locally_available="yes")).locally_available
    assert not material_from_row(_row(locally_available=0)).locally_available
    with pytest.raises(MaterialError):
        material_from_row(_row(locally_available="perhaps"))


def test_delivered_cost_applies_the_transport_multiplier():
    material = material_from_row(_row(locally_available=False,
                                      transport_multiplier=2.5))
    assert material.delivered_cost_inr_m3 == pytest.approx(2500.0)


def test_duplicate_material_ids_are_refused():
    material = material_from_row(_row())
    with pytest.raises(MaterialError, match="duplicate"):
        MaterialLibrary([material, material])


def test_empty_library_is_refused():
    with pytest.raises(MaterialError, match="empty"):
        MaterialLibrary([])


def test_unknown_lookup_names_the_origin(library):
    with pytest.raises(MaterialError, match="unknown material"):
        library.material("unobtainium")
    with pytest.raises(MaterialError, match="unknown glazing"):
        library.glazing("diamond")
    with pytest.raises(MaterialError, match="unknown phase change"):
        library.pcm("ice_nine")


def test_library_round_trips_through_json(tmp_path, library):
    payload = {
        "materials": [json.loads(item.model_dump_json()) for item in library.materials],
        "glazing_systems": [json.loads(item.model_dump_json())
                            for item in library.glazing_systems],
        "phase_change_materials": [json.loads(item.model_dump_json())
                                   for item in library.phase_change_materials],
    }
    for group in payload.values():
        for row in group:
            row["colour"] = row["appearance"]["colour"]
            row["source"] = row["source"]["citation"]
    path = tmp_path / "materials.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    reloaded = MaterialLibrary.from_json(path)
    assert len(reloaded.materials) == len(library.materials)
    assert reloaded.material("rammed_earth").conductivity_w_mk == pytest.approx(0.90)


def test_library_reads_a_sqlite_database(tmp_path, library):
    path = tmp_path / "materials.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE materials (id TEXT, name TEXT, conductivity_w_mk REAL, "
        "density_kg_m3 REAL, specific_heat_j_kgk REAL, cost_inr_m3 REAL, "
        "colour TEXT, source TEXT, source_status TEXT)")
    for material in library.materials:
        connection.execute(
            "INSERT INTO materials VALUES (?,?,?,?,?,?,?,?,?)",
            (material.id, material.name, material.conductivity_w_mk,
             material.density_kg_m3, material.specific_heat_j_kgk,
             material.cost_inr_m3, material.appearance.colour,
             material.source.citation, material.source.status.value))
    connection.commit()
    connection.close()
    reloaded = MaterialLibrary.from_sqlite(path)
    assert len(reloaded.materials) == len(library.materials)


def test_sqlite_without_a_materials_table_is_refused(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    with pytest.raises(MaterialError, match="no 'materials' table"):
        MaterialLibrary.from_sqlite(path)


def test_u_value_matches_a_hand_calculation(library):
    construction = ConstructionSpec(name="single_layer", layers=(
        LayerSpec(material_id="rammed_earth", thickness_m=0.45),))
    expected = 1.0 / (R_SURFACE_INSIDE_WALL + R_SURFACE_OUTSIDE + 0.45 / 0.90)
    assert library.u_value(construction, "wall") == pytest.approx(expected)


def test_insulation_lowers_the_u_value(library):
    bare = ConstructionSpec(name="bare", layers=(
        LayerSpec(material_id="rammed_earth", thickness_m=0.45),))
    insulated = ConstructionSpec(name="insulated", layers=(
        LayerSpec(material_id="eps_thermocol", thickness_m=0.1),
        LayerSpec(material_id="rammed_earth", thickness_m=0.45)))
    assert library.u_value(insulated) < 0.5 * library.u_value(bare)


def test_areal_heat_capacity_tracks_mass(library):
    light = ConstructionSpec(name="light", layers=(
        LayerSpec(material_id="eps_thermocol", thickness_m=0.1),))
    heavy = ConstructionSpec(name="heavy", layers=(
        LayerSpec(material_id="rammed_earth", thickness_m=0.4),))
    assert library.areal_heat_capacity(heavy) > 100 * library.areal_heat_capacity(light)


def test_mason_skill_takes_the_highest(library):
    mixed = ConstructionSpec(name="mixed", layers=(
        LayerSpec(material_id="mud_plaster", thickness_m=0.02),
        LayerSpec(material_id="concrete_dense", thickness_m=0.1)))
    assert library.max_mason_skill([mixed]) == "skilled"


def test_non_local_fraction_is_between_zero_and_one(library):
    assert 0.0 <= library.non_local_fraction([fixtures.wall_construction()]) <= 1.0


def test_constructions_reverse_partitions(library):
    design = fixtures.design(Arrangement.SUNSPACE)
    built = build_constructions(design, library)
    index = built.construction_index()
    partition = built.names.partition
    assert f"{partition}_REV" in index
    assert index[f"{partition}_REV"].layers == list(reversed(index[partition].layers))


def test_thin_layers_become_massless(library):
    design = fixtures.design()
    built = build_constructions(design, library)
    kinds = {material.name: material.kind for material in built.materials}
    assert any(kind == "nomass" for kind in kinds.values())
    assert any(item.code == "thin_layer_massless" for item in built.warnings)


def test_pcm_forces_the_finite_difference_solver(library):
    design = fixtures.design(pcm=PCMSpec(present=True, pcm_id="pcm_rt25",
                                         thickness_m=0.02))
    built = build_constructions(design, library)
    assert built.requires_conduction_finite_difference
    assert built.phase_change


def test_pcm_without_an_id_is_refused():
    with pytest.raises(Exception):
        PCMSpec(present=True)


def test_phase_change_curve_is_monotonic():
    points = phase_change_curve(25.0, 3.0, 170000.0, 2000.0)
    temperatures = [point[0] for point in points]
    enthalpies = [point[1] for point in points]
    assert temperatures == sorted(temperatures)
    assert enthalpies == sorted(enthalpies)
    assert len(points) >= 4


def test_trombe_wall_gets_a_dark_absorber(library):
    design = fixtures.design(Arrangement.TROMBE_WALL)
    built = build_constructions(design, library)
    assert built.names.trombe_wall
    assert any(item.name == "trombe_wall_solar_absorptance"
               for item in built.assumptions)
    dark = [material for material in built.materials
            if material.name.endswith("_DARK")]
    assert dark
    assert dark[0].fields["Solar_Absorptance"] == pytest.approx(0.95)


def test_shutters_produce_a_shaded_construction(library):
    design = fixtures.design()
    built = build_constructions(design, library)
    assert built.shades
    assert any(name.endswith("_SHADED") for name in built.construction_index())


def test_cost_breakdown_adds_up(library):
    design = fixtures.design()
    built = build_constructions(design, library)
    model = build_geometry(design, built.names)
    cost = cost_breakdown(design, model, library)
    assert cost.total_inr == pytest.approx(
        cost.envelope_inr + cost.glazing_inr + cost.shutters_inr + cost.pcm_inr)
    assert cost.envelope_inr > 0.0
    assert cost.glazing_inr > 0.0


def test_pcm_adds_cost(library):
    plain = fixtures.design()
    with_pcm = fixtures.design(pcm=PCMSpec(present=True, pcm_id="pcm_rt25",
                                           thickness_m=0.03))
    costs = []
    for design in (plain, with_pcm):
        built = build_constructions(design, library)
        model = build_geometry(design, built.names)
        costs.append(cost_breakdown(design, model, library).total_inr)
    assert costs[1] > costs[0]


def test_thicker_walls_cost_more(library):
    thin = fixtures.design(wall=fixtures.wall_construction(mass_m=0.2))
    thick = fixtures.design(wall=fixtures.wall_construction(mass_m=0.6))
    costs = []
    for design in (thin, thick):
        built = build_constructions(design, library)
        model = build_geometry(design, built.names)
        costs.append(cost_breakdown(design, model, library).envelope_inr)
    assert costs[1] > costs[0]


def test_buildability_flags_a_thick_wall(library):
    design = fixtures.design(wall=fixtures.wall_construction(mass_m=0.6))
    built = build_constructions(design, library)
    model = build_geometry(design, built.names)
    metrics = buildability(design, model, library)
    assert metrics.seismic_flag
    assert metrics.seismic_note


def test_buildability_respects_a_custom_threshold(library):
    design = fixtures.design(wall=fixtures.wall_construction(mass_m=0.6))
    built = build_constructions(design, library)
    model = build_geometry(design, built.names)
    constraints = OptimisationConstraints(seismic_wall_thickness_flag_m=1.5,
                                          max_wall_thickness_m=1.6)
    metrics = buildability(design, model, library, constraints)
    assert not metrics.seismic_flag


def test_envelope_and_glazing_u_values_are_reported(library):
    design = fixtures.design()
    assert set(envelope_u_values(design, library)) >= {"wall", "roof", "floor"}
    assert glazing_u_values(design, library)


def test_reference_catalogue_is_covered_by_the_library(library):
    catalogue = load_references()
    assert check_library_covers_references(library, catalogue) == ()
    assert catalogue.default.is_reference


def test_reference_catalogue_reports_uncited_designs():
    catalogue = load_references()
    assert catalogue.uncited() == catalogue.names()


def test_missing_reference_materials_are_listed():
    minimal = MaterialLibrary([material_from_row(_row())])
    missing = check_library_covers_references(minimal)
    assert missing
    assert all(item.startswith(("material:", "glazing:")) for item in missing)


def test_bill_of_quantities_matches_the_cost_and_the_mass(library):
    from shelter_engine.costing import bill_of_quantities, material_mass_kg

    for design in (fixtures.design(), fixtures.design(Arrangement.SUNSPACE),
                   fixtures.design(pcm=PCMSpec(present=True, pcm_id="pcm_rt25",
                                               thickness_m=0.02))):
        built = build_constructions(design, library)
        model = build_geometry(design, built.names)
        bill = bill_of_quantities(design, model, library)
        cost = cost_breakdown(design, model, library)
        assert bill.total_cost_inr == pytest.approx(cost.total_inr)
        imported, total = material_mass_kg(design, model, library)
        materials = [line for line in bill.lines if line.kind == "material"]
        assert sum(line.mass_kg for line in materials) == pytest.approx(total)
        assert bill.imported_mass_kg == pytest.approx(imported)
        assert len({line.item_id for line in materials}) == len(materials)
        for line in materials:
            assert line.volume_m3 > 0.0 and line.area_m2 > 0.0
            assert line.mass_kg == pytest.approx(
                line.volume_m3 * library.material(line.item_id).density_kg_m3)
        glazing = [line for line in bill.lines if line.kind == "glazing"]
        assert glazing and all(line.mass_kg is None for line in glazing)
        assert all(line.name in bill.unweighed for line in glazing)
        weighed = sum(line.mass_kg for line in bill.lines if line.mass_kg is not None)
        assert bill.total_mass_kg == pytest.approx(weighed)
    pcm = [line for line in bill.lines if line.kind == "pcm"]
    assert len(pcm) == 1 and pcm[0].mass_kg > 0.0
    shutters = [line for line in bill.lines if line.kind == "shutter"]
    assert len(shutters) == 1 and shutters[0].mass_kg is None


def test_eco_niwas_sheet_checks_the_cold_zone_limits(library):
    from shelter_engine.compliance import (ENVELOPE_U_COLD_MAX, ROOF_U_MAX, eco_niwas_sheet,
                                           minimum_vlt)
    from shelter_engine.schema import ClimateObjective

    site = fixtures.site("leh.epw")
    design = fixtures.design()
    model = _geometry(design, library)
    sheet = eco_niwas_sheet(design, model, library, site)
    assert sheet.assessed and sheet.climate_zone == "Cold"
    items = {item.clause: item for item in sheet.items}
    roof = items["5.2.1.3, 9.4"]
    assert roof.value == pytest.approx(library.u_value(design.roof, "roof"))
    assert roof.limit == ROOF_U_MAX and roof.passes
    envelope = items["5.2.1.5, 9.7"]
    assert library.u_value(design.wall, "wall") < envelope.value < 3.0
    assert envelope.limit == ENVELOPE_U_COLD_MAX
    assert items["5.2.1.1, 9.1"].value is None and items["5.2.1.1, 9.1"].passes is None
    assert sheet.mandatory_pass is True

    thin = ConstructionSpec(name="bare_sheet", layers=(
        LayerSpec(material_id=design.roof.layers[0].material_id, thickness_m=0.002),))
    leaky = design.variant(roof=thin)
    failing = eco_niwas_sheet(leaky, _geometry(leaky, library), library, site)
    assert failing.mandatory_pass is False

    warm = site.model_copy(update={"climate_objective": ClimateObjective.COOLING_DOMINATED})
    skipped = eco_niwas_sheet(design, model, library, warm)
    assert not skipped.assessed and skipped.items == () and "RETV" in skipped.zone_basis

    assert minimum_vlt(0.10) == 0.27
    assert minimum_vlt(0.305) == 0.20
    assert minimum_vlt(0.70) == 0.11
    assert minimum_vlt(0.75) is None


def _geometry(design, library):
    return build_geometry(design, build_constructions(design, library).names)
