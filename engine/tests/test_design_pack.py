from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

import pytest

import fixtures
from shelter_engine.constructions import build_constructions
from shelter_engine.design_pack import (PackInputs, build_design_pack, elevation_svg,
                                        plan_svg, quantities_csv)
from shelter_engine.costing import bill_of_quantities
from shelter_engine.geometry import build_geometry
from shelter_engine.schema import Arrangement, Facade


def _model(design, library):
    return build_geometry(design, build_constructions(design, library).names)


@pytest.mark.parametrize("arrangement", list(Arrangement))
def test_every_arrangement_packs_valid_drawings(library, arrangement):
    design = fixtures.design(arrangement)
    payload = build_design_pack(PackInputs(design=design, site=fixtures.site("leh.epw"),
                                           model=_model(design, library), library=library,
                                           idf_text="Version,24.2;"))
    archive = zipfile.ZipFile(io.BytesIO(payload))
    names = archive.namelist()
    assert "energyplus/shelter.idf" in names
    for name in names:
        if name.endswith(".svg"):
            root = ElementTree.fromstring(archive.read(name))
            assert root.tag.endswith("svg")
    readme = archive.read("README.txt").decode("utf-8")
    assert all(name in readme for name in names if name != "README.txt")


def test_elevations_do_not_depend_on_which_way_the_building_faces(library):
    base = fixtures.design(Arrangement.SUNSPACE)
    turned = base.variant(plan=base.plan.model_copy(update={"azimuth_deg": 215.0}))
    for facade in Facade:
        assert elevation_svg(_model(base, library), base, facade) == \
            elevation_svg(_model(turned, library), turned, facade)
    assert plan_svg(_model(base, library), base) != plan_svg(_model(turned, library), turned)


def test_the_quantities_csv_matches_the_bill(library):
    design = fixtures.design()
    bill = bill_of_quantities(design, _model(design, library), library)
    rows = quantities_csv(bill).strip().splitlines()
    assert len(rows) == len(bill.lines) + 1
    assert rows[0].startswith("item_id,name,kind")


def test_extra_files_cannot_escape_or_overwrite(library):
    design = fixtures.design()
    base = dict(design=design, site=fixtures.site("leh.epw"), model=_model(design, library),
                library=library)
    for name in ("../outside.txt", "/root.txt", "design.json"):
        with pytest.raises(ValueError):
            build_design_pack(PackInputs(extra_files={name: b"x"}, **base))
    payload = build_design_pack(PackInputs(extra_files={"ansys/mapdl/a.json": b"{}"}, **base))
    assert "ansys/mapdl/a.json" in zipfile.ZipFile(io.BytesIO(payload)).namelist()
