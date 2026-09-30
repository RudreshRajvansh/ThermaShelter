from __future__ import annotations

import io
import json
import zipfile
from xml.etree import ElementTree

import pytest

from shelter_engine.errors import EngineError
from shelter_engine.evidence import (MANIFEST, mapdl_evidence, read_manifest, series_svg,
                                     sha256_of, write_folder, zip_folder)
from shelter_engine.schema import HourlySeries


def test_folders_record_every_file_with_its_hash(tmp_path):
    folder = write_folder(tmp_path / "run", {"a.json": b"{}", "plots/b.svg": b"<svg/>"},
                          {"run_hash": "x"})
    manifest = read_manifest(folder)
    assert manifest["run_hash"] == "x"
    assert [item["file"] for item in manifest["files"]] == ["a.json", "plots/b.svg"]
    assert manifest["files"][0]["sha256"] == sha256_of(b"{}")
    write_folder(folder, {"c.txt": b"c"}, {"extra": 1})
    merged = read_manifest(folder)
    assert {item["file"] for item in merged["files"]} == {"a.json", "plots/b.svg", "c.txt"}
    assert merged["run_hash"] == "x" and merged["extra"] == 1
    names = zipfile.ZipFile(io.BytesIO(zip_folder(folder))).namelist()
    assert MANIFEST in names and "plots/b.svg" in names


def test_names_cannot_leave_the_folder_or_replace_the_manifest(tmp_path):
    for name in ("../x", "/x", MANIFEST, ""):
        with pytest.raises(EngineError):
            write_folder(tmp_path / "run", {name: b"x"}, {})
    with pytest.raises(EngineError):
        read_manifest(tmp_path / "absent")
    with pytest.raises(EngineError):
        zip_folder(tmp_path / "absent")


def test_mapdl_runs_add_their_own_subfolder(tmp_path):
    folder = mapdl_evidence(tmp_path, "abc", "week", {"shelter.inp": b"/BATCH"},
                            {"rmse_k": 1.2})
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    assert manifest["mapdl_week"]["rmse_k"] == 1.2
    assert (folder / "mapdl_week" / "shelter.inp").read_bytes() == b"/BATCH"


def test_plots_are_valid_svg_and_skip_gaps():
    series = [HourlySeries(name="operative", unit="C", timestamps=("a", "b", "c", "d"),
                           values=(1.0, None, 3.0, 4.0)),
              HourlySeries(name="empty", unit="C", timestamps=("a",), values=(None,))]
    root = ElementTree.fromstring(series_svg("title", series))
    lines = [item for item in root.iter() if item.tag.endswith("polyline")]
    assert len(lines) == 1
    empty = ElementTree.fromstring(series_svg("none", []))
    assert "No hourly series" in "".join(empty.itertext())
