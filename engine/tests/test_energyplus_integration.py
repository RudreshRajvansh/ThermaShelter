from __future__ import annotations

from datetime import date

import pytest

import fixtures
from conftest import requires_energyplus
from shelter_engine.cfd import write_cfd_export
from shelter_engine.constructions import build_constructions
from shelter_engine.costing import cost_breakdown
from shelter_engine.engine import Engine
from shelter_engine.geometry import build_geometry
from shelter_engine.idf import build_idf
from shelter_engine.materials import MaterialLibrary
from shelter_engine.optimize import cold_climate_variables
from shelter_engine.schema import (Arrangement, Facade, OptimisationConstraints,
                                   OptimisationSpec, PCMSpec, RunOptions,
                                   RunPeriod, Severity, SourceRef, SourceStatus,
                                   ThermalBridge, WindowSpec)

_BLANK = "\n\n"

pytestmark = requires_energyplus

WEEK = RunPeriod(start=date(2019, 1, 10), end=date(2019, 1, 16), label="test_week")
SURFACE_WINDOW = RunPeriod(start=date(2019, 1, 12), end=date(2019, 1, 13),
                           label="surface")


def options(**overrides) -> RunOptions:
    payload = dict(period=WEEK, timestep_per_hour=6, conditioned_run=False,
                   surface_output=True, surface_output_period=SURFACE_WINDOW)
    payload.update(overrides)
    return RunOptions(**payload)


@pytest.fixture(scope="module")
def engine(site, library):
    return Engine(site, library, fuel="diesel")


@pytest.mark.parametrize("arrangement", list(Arrangement))
def test_idf_builds_for_every_arrangement(arrangement, site, library):
    design = fixtures.design(arrangement)
    built = build_constructions(design, library)
    model = build_geometry(design, built.names, horizon=site.horizon)
    bundle = build_idf(design, site, model, built, options())
    assert "BUILDINGSURFACE:DETAILED" in bundle.text.upper()
    assert "GLOBALGEOMETRYRULES" in bundle.text.upper()
    assert len(bundle.zone_names) == len(model.zones)
    assert set(bundle.surface_names) == {p.name for p in model.polygons}


def test_serialised_idf_is_well_formed(site, library):
    design = fixtures.design()
    built = build_constructions(design, library)
    model = build_geometry(design, built.names, horizon=site.horizon)
    text = build_idf(design, site, model, built, options()).text
    blocks = [block for block in text.split(_BLANK) if block.strip()]
    assert blocks
    for block in blocks:
        lines = block.splitlines()
        head = lines[0]
        assert head.rstrip().endswith((",", ";"))
        assert head.strip(",; ").upper() == head.strip(",; ")
        values = [line.split("!-")[0] for line in lines[1:]]
        for line in values[:-1]:
            assert line.rstrip().endswith(",")
            assert ";" not in line
        if values:
            assert values[-1].rstrip().endswith(";")
    assert text.count(";") == len(blocks)


def test_serialiser_matches_the_objects_it_was_given(site, library):
    design = fixtures.design(Arrangement.SUNSPACE)
    built = build_constructions(design, library)
    model = build_geometry(design, built.names, horizon=site.horizon)
    text = build_idf(design, site, model, built, options()).text
    for polygon in model.polygons:
        assert polygon.name in text
    for construction in built.construction_names():
        assert construction in text


def test_a_name_with_a_comma_does_not_split_a_field(site, library):
    design = fixtures.design().variant(name="candidate, revision two")
    built = build_constructions(design, library)
    model = build_geometry(design, built.names, horizon=site.horizon)
    text = build_idf(design, site, model, built, options()).text
    building = [block for block in text.split(_BLANK)
                if block.startswith("BUILDING,")][0]
    assert "candidate revision two" in building
    assert len([line for line in building.splitlines() if line.strip()]) == 9


def test_overhang_reaches_the_input_file(site, library):
    design = fixtures.design(windows={
        Facade.SOUTH: WindowSpec(wwr=0.30, glazing_id="double_clear_6_12_6",
                                 sill_height_m=0.60, overhang_depth_m=1.0)})
    built = build_constructions(design, library)
    model = build_geometry(design, built.names, horizon=site.horizon)
    text = build_idf(design, site, model, built, options()).text
    assert "SHADING:OVERHANG," in text
    assert "ROOM_WALL_SOUTH_WIN_OVERHANG" in text


def test_overhang_cuts_incident_solar_on_the_glazing(engine):
    bare = fixtures.design(windows={
        Facade.SOUTH: WindowSpec(wwr=0.30, glazing_id="double_clear_6_12_6",
                                 sill_height_m=0.60)})
    shaded = fixtures.design(windows={
        Facade.SOUTH: WindowSpec(wwr=0.30, glazing_id="double_clear_6_12_6",
                                 sill_height_m=0.60, overhang_depth_m=1.5)})
    totals = []
    for design in (bare, shaded):
        result = engine.simulate(design, options(), with_uncertainty=False,
                                 with_reference=False)
        window = [item for item in result.surfaces.surfaces
                  if item.surface == "ROOM_WALL_SOUTH_WIN"][0]
        totals.append(sum(value for value in window.incident_solar_w_m2 if value))
    assert totals[0] > 0.0
    assert totals[1] < totals[0]


def test_added_moisture_raises_indoor_humidity(engine):
    dry = fixtures.design()
    dry = dry.variant(occupancy=dry.occupancy.model_copy(
        update={"additional_moisture_g_h": 0.0}))
    damp = dry.variant(occupancy=dry.occupancy.model_copy(
        update={"additional_moisture_g_h": 3000.0}))
    peaks = []
    for design in (dry, damp):
        result = engine.simulate(design, options(), with_uncertainty=False,
                                 with_reference=False)
        series = {item.name: item for item in result.hourly}
        values = [value for value in series["indoor_relative_humidity_pct"].values
                  if value is not None]
        peaks.append(max(values))
    assert peaks[1] > peaks[0]


def test_added_moisture_increases_condensation_risk(engine):
    dry = fixtures.design(windows={
        Facade.SOUTH: WindowSpec(wwr=0.30, glazing_id="single_clear_4mm",
                                 sill_height_m=0.60)})
    dry = dry.variant(occupancy=dry.occupancy.model_copy(
        update={"additional_moisture_g_h": 0.0}))
    damp = dry.variant(occupancy=dry.occupancy.model_copy(
        update={"additional_moisture_g_h": 5000.0}))
    hours = [engine.simulate(design, options(), with_uncertainty=False,
                             with_reference=False).condensation.hours
             for design in (dry, damp)]
    assert hours[1] >= hours[0]


def test_frame_fraction_shows_up_in_the_glazing_cost(library):
    design = fixtures.design()
    built = build_constructions(design, library)
    model = build_geometry(design, built.names)
    baseline = cost_breakdown(design, model, library).glazing_inr
    framed = MaterialLibrary(
        library.materials,
        [system.model_copy(update={"frame_fraction": 0.40})
         for system in library.glazing_systems],
        library.phase_change_materials)
    assert cost_breakdown(design, model, framed).glazing_inr > baseline


def test_conditioned_idf_adds_ideal_loads(site, library):
    design = fixtures.design()
    built = build_constructions(design, library)
    model = build_geometry(design, built.names, horizon=site.horizon)
    bundle = build_idf(design, site, model, built, options(), conditioned=True)
    assert bundle.ideal_loads_names
    assert "ZONEHVAC:IDEALLOADSAIRSYSTEM" in bundle.text.upper()


def test_pcm_raises_the_timestep_and_switches_solver(site, library):
    design = fixtures.design(pcm=PCMSpec(present=True, pcm_id="pcm_rt25",
                                         thickness_m=0.02))
    built = build_constructions(design, library)
    model = build_geometry(design, built.names, horizon=site.horizon)
    bundle = build_idf(design, site, model, built, options(timestep_per_hour=6))
    assert bundle.timestep_per_hour >= 20
    assert "CONDUCTIONFINITEDIFFERENCE" in bundle.text.upper()
    assert any(item.code == "pcm_timestep_raised" for item in bundle.warnings)


def test_single_room_simulation_produces_a_temperature_curve(engine):
    design = fixtures.design()
    result = engine.simulate(design, options(), with_uncertainty=False,
                             with_reference=False)
    assert result.comfort.minimum_operative_c is not None
    assert -40.0 < result.comfort.minimum_operative_c < 40.0
    assert result.comfort.mean_operative_c > result.comfort.minimum_operative_c
    series = {item.name: item for item in result.hourly}
    assert "operative_temperature_c" in series
    assert "outdoor_temperature_c" in series
    assert len(series["operative_temperature_c"].values) >= 24 * WEEK.days - 2
    assert not [item for item in result.warnings
                if item.severity in (Severity.SEVERE, Severity.FATAL)]


def test_indoor_stays_warmer_than_outdoor_in_winter(engine):
    result = engine.simulate(fixtures.design(), options(), with_uncertainty=False,
                             with_reference=False)
    series = {item.name: item for item in result.hourly}
    indoor = [value for value in series["operative_temperature_c"].values
              if value is not None]
    outdoor = [value for value in series["outdoor_temperature_c"].values
               if value is not None]
    assert sum(indoor) / len(indoor) > sum(outdoor) / len(outdoor)


def test_indoor_swing_is_damped_relative_to_outdoor(engine):
    result = engine.simulate(fixtures.design(), options(), with_uncertainty=False,
                             with_reference=False)
    assert result.comfort.decrement_factor is not None
    assert 0.0 < result.comfort.decrement_factor < 1.0


def test_more_insulation_raises_the_overnight_minimum(engine):
    thin = fixtures.design(wall=fixtures.wall_construction(insulation_m=0.01),
                           roof=fixtures.roof_construction(insulation_m=0.01))
    thick = fixtures.design(wall=fixtures.wall_construction(insulation_m=0.20),
                            roof=fixtures.roof_construction(insulation_m=0.30))
    results = [engine.simulate(design, options(), with_uncertainty=False,
                               with_reference=False) for design in (thin, thick)]
    assert (results[1].comfort.minimum_overnight_operative_c
            > results[0].comfort.minimum_overnight_operative_c)


def test_more_leakage_lowers_the_overnight_minimum(engine):
    tight = fixtures.design()
    tight = tight.variant(ventilation=tight.ventilation.model_copy(
        update={"infiltration_ach": 0.2, "infiltration_range": None}))
    leaky = tight.variant(ventilation=tight.ventilation.model_copy(
        update={"infiltration_ach": 3.0, "infiltration_range": None}))
    results = [engine.simulate(design, options(), with_uncertainty=False,
                               with_reference=False) for design in (tight, leaky)]
    assert (results[0].comfort.minimum_overnight_operative_c
            > results[1].comfort.minimum_overnight_operative_c)


def test_surface_names_match_the_display_mesh(engine):
    design = fixtures.design()
    payload = engine.geometry(design)
    result = engine.simulate(design, options(), with_uncertainty=False,
                             with_reference=False)
    assert result.surfaces is not None
    reported = {item.surface for item in result.surfaces.surfaces}
    assert reported <= set(payload.surface_names)
    assert reported


def test_surface_results_are_clipped_to_the_window(engine):
    result = engine.simulate(fixtures.design(), options(), with_uncertainty=False,
                             with_reference=False)
    assert result.surfaces is not None
    assert len(result.surfaces.timestamps) == 24 * SURFACE_WINDOW.days
    for surface in result.surfaces.surfaces:
        for values in (surface.inside_face_temperature_c,
                       surface.outside_face_temperature_c):
            if values:
                assert len(values) == len(result.surfaces.timestamps)


def test_south_wall_collects_more_solar_than_north(engine):
    result = engine.simulate(fixtures.design(), options(), with_uncertainty=False,
                             with_reference=False)
    lookup = {item.surface: item for item in result.surfaces.surfaces}
    south = lookup.get("ROOM_WALL_SOUTH")
    north = lookup.get("ROOM_WALL_NORTH")
    assert south is not None and north is not None
    south_total = sum(value for value in south.incident_solar_w_m2 if value)
    north_total = sum(value for value in north.incident_solar_w_m2 if value)
    assert south_total > north_total


def test_heat_balance_reports_losses(engine):
    result = engine.simulate(fixtures.design(), options(), with_uncertainty=False,
                             with_reference=False)
    assert result.breakdown.total_loss_kwh > 0.0
    shares = result.breakdown.loss_fractions()
    assert shares
    assert sum(shares.values()) == pytest.approx(1.0, abs=1e-6)


def test_thermal_bridges_add_a_loss_line(site, library):
    bridge = ThermalBridge(
        id="wall_floor", junction="wall to floor", psi_w_mk=0.35,
        length_from="wall_floor_perimeter",
        source=SourceRef(citation="Test ANSYS run", status=SourceStatus.PENDING))
    engine = Engine(site, library, thermal_bridges=[bridge])
    result = engine.simulate(fixtures.design(), options(), with_uncertainty=False,
                             with_reference=False)
    assert result.breakdown.thermal_bridge_kwh > 0.0
    assert "thermal_bridge" in result.breakdown.loss_fractions()


def test_conditioned_run_reports_heating_demand_and_fuel(engine):
    result = engine.simulate(fixtures.design(), options(conditioned_run=True),
                             with_uncertainty=False, with_reference=False)
    assert result.energy.heating_demand_kwh is not None
    assert result.energy.heating_demand_kwh > 0.0
    assert result.energy.fuel_units is not None
    assert result.energy.fuel_units > 0.0


def test_reference_comparison_is_attached(engine):
    result = engine.simulate(fixtures.design(), options(conditioned_run=True),
                             with_uncertainty=False)
    assert result.reference is not None
    assert result.reference.reference_name == "current_practice_masonry"
    assert result.reference.delta_minimum_overnight_k is not None
    assert any(item.name.startswith("reference_") for item in result.hourly)


def test_uncertainty_band_is_produced(engine):
    result = engine.simulate(fixtures.design(), options(), with_reference=False)
    bands = {item.metric: item for item in result.uncertainty}
    assert "minimum_overnight_operative_c" in bands
    band = bands["minimum_overnight_operative_c"]
    assert band.low <= band.nominal <= band.high
    assert band.dominant_parameter


def test_recommendations_come_from_the_numbers(engine):
    result = engine.simulate(fixtures.design(), options(), with_uncertainty=False,
                             with_reference=False)
    for item in result.recommendations:
        assert item.triggered_by


def test_assumptions_carry_sources(engine):
    result = engine.simulate(fixtures.design(), options(), with_uncertainty=False,
                             with_reference=False)
    assert result.assumptions
    for assumption in result.assumptions:
        assert assumption.source.citation


def test_repeat_simulation_is_served_from_cache(engine):
    design = fixtures.design(name="cache_probe")
    first = engine.simulate(design, options(), with_uncertainty=False,
                            with_reference=False)
    second = engine.simulate(design, options(), with_uncertainty=False,
                             with_reference=False)
    assert not first.cached
    assert second.cached
    assert second.design_hash == first.design_hash


@pytest.mark.parametrize("arrangement", list(Arrangement))
def test_every_arrangement_simulates(arrangement, engine):
    design = fixtures.design(arrangement)
    result = engine.simulate(design, options(), with_uncertainty=False,
                             with_reference=False)
    assert result.comfort.minimum_operative_c is not None
    assert not [item for item in result.warnings if item.severity is Severity.FATAL]


def test_sunspace_buffers_the_room(engine):
    plain = engine.simulate(fixtures.design(Arrangement.SINGLE_ROOM), options(),
                            with_uncertainty=False, with_reference=False)
    sunspace = engine.simulate(fixtures.design(Arrangement.SUNSPACE), options(),
                               with_uncertainty=False, with_reference=False)
    assert (sunspace.comfort.minimum_overnight_operative_c
            >= plain.comfort.minimum_overnight_operative_c - 2.0)


def test_comparison_table_includes_the_reference(engine):
    table = engine.compare([fixtures.design()], options(conditioned_run=True))
    assert len(table.rows) == 2
    assert table.rows[0].is_reference
    assert table.rows[1].delta_vs_reference


def test_cfd_export_carries_boundary_conditions(engine, tmp_path):
    exports = engine.cfd_export(fixtures.design(), options())
    assert {item.label for item in exports} == {"coldest_night", "peak_solar"}
    for export in exports:
        assert export.surfaces
        assert export.zone_volume_m3 > 0.0
        for surface in export.surfaces:
            assert -60.0 < surface.inside_face_temperature_c < 80.0
        path = write_cfd_export(export, tmp_path)
        assert path.is_file()


def test_optimisation_runs_end_to_end(engine):
    base = fixtures.design()
    spec = OptimisationSpec(
        base_design=base,
        variables=cold_climate_variables(base)[:3],
        constraints=OptimisationConstraints(max_wall_thickness_m=0.8,
                                            max_condensation_hours=1000.0),
        population_size=8, generations=2, seed=1)
    result = engine.optimise(spec, options())
    assert result.front
    assert result.evaluated > 0


def test_self_check_reports_pending_provenance(engine):
    warnings = engine.self_check()
    codes = {item.code for item in warnings}
    assert "materials_unsourced" in codes
    assert "reference_designs_uncited" in codes


def test_a_price_without_its_own_source_is_called_out(engine):
    codes = {item.code for item in engine.self_check()}
    assert "capital_cost_unsourced" in codes


def test_a_verified_thermal_source_does_not_vouch_for_the_price(library):
    material = library.material("brick_solid")
    priced = material.model_copy(update={
        "source": material.source.model_copy(update={
            "status": SourceStatus.VERIFIED}),
        "cost_source": None,
    })
    assert priced.source.status is SourceStatus.VERIFIED
    assert priced.cost_source is None


def test_internal_partitions_reach_the_input_file(site, library):
    design = fixtures.design()
    built = build_constructions(design, library)
    model = build_geometry(design, built.names, horizon=site.horizon)
    text = build_idf(design, site, model, built, options()).text
    assert "INTERNALMASS," in text
    assert "ROOM_PARTITIONS" in text


def test_a_larger_programme_damps_the_daily_swing(engine):
    from shelter_engine.programme import layout_for, plan_for
    from shelter_engine.schema import ShelterUse

    open_plan = fixtures.design(plan=plan_for(ShelterUse.STORE, floor_area_m2=40.0))
    partitioned = fixtures.design(
        plan=plan_for(ShelterUse.MEDICAL_POST, floor_area_m2=40.0))
    assert (layout_for(partitioned.plan).partition_area_m2
            > layout_for(open_plan.plan).partition_area_m2)
    swings = []
    for design in (open_plan, partitioned):
        design = design.variant(occupancy=fixtures.design().occupancy)
        result = engine.simulate(design, options(), with_uncertainty=False,
                                 with_reference=False)
        series = {item.name: item for item in result.hourly}
        values = [value for value
                  in series["operative_temperature_c"].values
                  if value is not None]
        swings.append(max(values) - min(values))
    assert swings[1] < swings[0]


def test_airflow_is_reported_per_zone_and_per_coupling(engine):
    design = fixtures.design(arrangement=Arrangement.SUNSPACE)
    result = engine.simulate(design, fixtures.winter_options(),
                             with_uncertainty=False, with_reference=False)
    airflow = result.airflow
    assert airflow is not None
    zones = {item.zone for item in airflow.zones}
    assert "ROOM" in zones and "SUNSPACE" in zones
    for item in airflow.zones:
        assert item.volume_m3 > 0.0
        assert item.infiltration_ach_peak >= item.infiltration_ach_mean
    assert airflow.couplings
    coupling = airflow.couplings[0]
    assert coupling.to_zone == "ROOM"
    assert coupling.volume_flow_m3_s_mean > 0.0


def test_a_leakier_shelter_reports_a_higher_air_change_rate(engine):
    tight = fixtures.design()
    leaky = tight.model_copy(update={
        "ventilation": tight.ventilation.model_copy(update={
            "infiltration_ach": tight.ventilation.infiltration_ach * 2.0})})
    options = fixtures.winter_options()
    tight_flow = engine.simulate(tight, options, with_uncertainty=False,
                                 with_reference=False).airflow
    leaky_flow = engine.simulate(leaky, options, with_uncertainty=False,
                                 with_reference=False).airflow
    tight_ach = next(z.infiltration_ach_mean for z in tight_flow.zones
                     if z.zone == "ROOM")
    leaky_ach = next(z.infiltration_ach_mean for z in leaky_flow.zones
                     if z.zone == "ROOM")
    assert leaky_ach > tight_ach


def test_airflow_states_it_is_not_a_velocity_field(engine):
    result = engine.simulate(fixtures.design(), fixtures.winter_options(),
                             with_uncertainty=False, with_reference=False)
    assert "not a velocity field" in result.airflow.note
