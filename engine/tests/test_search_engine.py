from __future__ import annotations

from datetime import date

import pytest

import fixtures
from conftest import requires_energyplus
from shelter_engine.constructions import build_constructions
from shelter_engine.costing import material_mass_kg
from shelter_engine.geometry import build_geometry
from shelter_engine.schema import (LayerSpec, ParetoPoint, ParetoResult, RunOptions,
                                   RunPeriod)

SHORT = RunPeriod(start=date(2019, 1, 14), end=date(2019, 1, 16), label="short")


def _model(design, library):
    built = build_constructions(design, library)
    return build_geometry(design, built.names)


def test_imported_mass_counts_only_materials_that_are_not_local(library):
    design = fixtures.design()
    imported, total = material_mass_kg(design, _model(design, library), library)
    assert 0.0 < imported < total


def test_a_thicker_imported_layer_imports_more(library):
    base = fixtures.design()
    thick = base.variant(wall=base.wall.model_copy(update={"layers": (
        base.wall.layers[0],
        LayerSpec(material_id=base.wall.layers[1].material_id, thickness_m=0.16),
        *base.wall.layers[2:])}))
    thin_mass, _ = material_mass_kg(base, _model(base, library), library)
    thick_mass, _ = material_mass_kg(thick, _model(thick, library), library)
    assert thick_mass > thin_mass


def test_a_thicker_local_layer_does_not_change_imports(library):
    base = fixtures.design()
    heavier = base.variant(wall=base.wall.model_copy(update={"layers": (
        *base.wall.layers[:2],
        LayerSpec(material_id=base.wall.layers[2].material_id, thickness_m=0.60),
        base.wall.layers[3])}))
    before, total_before = material_mass_kg(base, _model(base, library), library)
    after, total_after = material_mass_kg(heavier, _model(heavier, library), library)
    assert after == pytest.approx(before)
    assert total_after > total_before


@requires_energyplus
def test_metrics_carry_the_search_objectives(site, library):
    from shelter_engine.engine import Engine
    engine = Engine(site, library, fuel="kerosene")
    options = RunOptions(period=SHORT, surface_output=False)
    light = engine.metrics_for(fixtures.design(), options)
    assert {"night_degree_hours_below_kh", "imported_tonnes", "peak_night_co2_ppm",
            "co2_limit_ppm"} <= set(light)
    assert "heating_litres" not in light
    heated = engine.metrics_for(fixtures.design(), options, with_heating=True)
    assert heated["heating_litres"] > 0.0 and heated["heating_demand_kwh"] > 0.0


@requires_energyplus
def test_robustness_ranks_by_the_worst_corner_and_keeps_the_best(site, library):
    from shelter_engine.engine import Engine
    engine = Engine(site, library)
    options = RunOptions(period=SHORT, surface_output=False)
    points = []
    for tag, mass in (("thin", 0.2), ("thick", 0.5)):
        design = fixtures.design(wall=fixtures.wall_construction(mass_m=mass))
        metrics = engine.metrics_for(design, options)
        points.append(ParetoPoint(design_hash=tag, design=design, metrics=metrics,
                                  objectives={
                                      "night_degree_hours_below_kh":
                                          metrics["night_degree_hours_below_kh"],
                                      "imported_tonnes": metrics["imported_tonnes"]}))
    result = ParetoResult(spec_hash="t", site_id=site.id, front=tuple(points),
                          evaluated=2, feasible_evaluated=2,
                          objectives=("night_degree_hours_below_kh", "imported_tonnes"))
    rows = engine.robustness(result, options, top=2, keep=1, workers=2)
    assert len(rows) == 2
    assert sorted(row.robust_rank for row in rows) == [1, 2]
    chosen = next(row for row in rows if row.selected)
    assert chosen.worst == min(row.worst for row in rows)
    for row in rows:
        assert row.best <= row.nominal <= row.worst
        assert row.corner_runs >= 1
