from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "tests"))

DATA = ROOT / "data"

pytestmark = pytest.mark.skipif(
    not (DATA / "materials.db").is_file(),
    reason="run scripts/build_data.py first")


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> TestClient:
    import os
    os.environ["SHELTER_DATA_DIR"] = str(DATA)
    os.environ["SHELTER_DB"] = str(tmp_path_factory.mktemp("store") / "runs.db")
    from shelter_api.config import get_settings
    from shelter_api.deps import get_context
    get_settings.cache_clear()
    get_context.cache_clear()
    from shelter_api.main import create_app
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def design() -> dict:
    return json.loads((DATA / "designs" / "single_room.json").read_text())


@pytest.fixture(scope="module")
def reference_design() -> dict:
    path = DATA / "designs" / "reference_current_practice_masonry.json"
    return json.loads(path.read_text())


WINDOW = {"start": "2019-01-10", "end": "2019-01-16"}
SURFACE = {"start": "2019-01-12", "end": "2019-01-13"}


def test_health_reports_ready(client):
    body = client.get("/api/health").json()
    assert body["ready"] is True
    assert body["status"] == "ok"


def test_meta_lists_both_climates(client):
    body = client.get("/api/meta").json()
    ids = {site["id"] for site in body["sites"]}
    assert {"leh", "jaisalmer"} <= ids
    headline = {site["id"]: site["headline_metric"] for site in body["sites"]}
    assert headline["leh"] == "minimum_overnight_operative_c"
    assert headline["jaisalmer"] == "maximum_daytime_operative_c"
    assert body["energyplus_available"] is True


def test_meta_names_the_provenance_gaps(client):
    body = client.get("/api/meta").json()
    assert body["provenance_gaps"]
    joined = " ".join(body["provenance_gaps"])
    assert "verified source" in joined
    assert "uncited" in joined


def test_catalogue_endpoints_carry_sources(client):
    for endpoint in ("materials", "glazing", "phase-change-materials"):
        rows = client.get(f"/api/{endpoint}").json()
        assert rows
        for row in rows:
            assert row["source"]["citation"]
            assert row["appearance"]["colour"].startswith("#")


def test_reference_designs_are_listed_and_fetchable(client):
    names = [item["name"] for item in client.get("/api/references").json()]
    assert "current_practice_masonry" in names
    single = client.get("/api/references/current_practice_masonry").json()
    assert single["is_reference"] is True
    assert client.get("/api/references/nope").status_code == 404


def test_geometry_is_fast_and_named(client, design):
    response = client.post("/api/geometry", json={"design": design})
    assert response.status_code == 200
    payload = response.json()
    names = {surface["name"] for surface in payload["surfaces"]}
    assert "ROOM_WALL_SOUTH" in names
    assert "ROOM_FLOOR" in names
    for surface in payload["surfaces"]:
        assert len(surface["positions"]) % 3 == 0
        assert surface["colour"].startswith("#")


def test_geometry_rejects_an_impossible_design(client, design):
    broken = json.loads(json.dumps(design))
    broken["windows"]["south"]["wwr"] = 0.89
    broken["windows"]["south"]["sill_height_m"] = 2.0
    response = client.post("/api/geometry", json={"design": broken})
    assert response.status_code == 422


def test_unknown_site_is_a_clear_404(client, design):
    response = client.post("/api/geometry",
                           json={"design": design, "site_id": "atlantis"})
    assert response.status_code == 404
    assert "Unknown site" in response.json()["detail"]


def test_simulate_returns_a_full_result(client, design):
    response = client.post("/api/simulate", json={
        "design": design, "period": WINDOW, "surface_period": SURFACE,
        "conditioned_run": True, "with_uncertainty": False})
    assert response.status_code == 200
    body = response.json()
    assert body["comfort"]["minimum_overnight_operative_c"] is not None
    assert body["energy"]["heating_demand_kwh"] > 0
    assert body["reference"]["reference_name"] == "current_practice_masonry"
    assert body["assumptions"]
    assert body["surfaces"]["surfaces"]
    names = {item["name"] for item in body["hourly"]}
    assert {"operative_temperature_c", "outdoor_temperature_c",
            "comfort_lower_bound_c"} <= names


def test_simulated_surfaces_match_the_geometry_names(client, design):
    geometry = client.post("/api/geometry", json={"design": design}).json()
    simulated = client.post("/api/simulate", json={
        "design": design, "period": WINDOW, "surface_period": SURFACE,
        "conditioned_run": False, "with_uncertainty": False,
        "with_reference": False}).json()
    mesh = {surface["name"] for surface in geometry["surfaces"]}
    reported = {item["surface"] for item in simulated["surfaces"]["surfaces"]}
    assert reported
    assert reported <= mesh


def test_simulate_is_stored_and_retrievable(client, design):
    body = client.post("/api/simulate", json={
        "design": design, "period": WINDOW, "conditioned_run": False,
        "with_uncertainty": False, "with_reference": False}).json()
    design_hash = body["design_hash"]
    stored = client.get(f"/api/runs/{design_hash}")
    assert stored.status_code == 200
    assert stored.json()["design_hash"] == design_hash
    listing = client.get("/api/runs").json()
    assert any(item["design_hash"] == design_hash for item in listing)


def test_repeat_simulation_is_served_from_cache(client, design):
    payload = {"design": design, "period": WINDOW, "conditioned_run": False,
               "with_uncertainty": False, "with_reference": False}
    first = client.post("/api/simulate", json=payload).json()
    second = client.post("/api/simulate", json=payload).json()
    assert second["cached"] is True
    assert second["design_hash"] == first["design_hash"]


def test_unknown_run_is_a_404(client):
    assert client.get("/api/runs/deadbeef").status_code == 404


def test_compare_puts_the_reference_first(client, design, reference_design):
    response = client.post("/api/compare", json={
        "designs": [design], "period": WINDOW, "conditioned_run": True})
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 2
    assert rows[0]["is_reference"] is True
    assert rows[1]["delta_vs_reference"]


def test_cooling_climate_flips_the_headline(client, design):
    body = client.post("/api/simulate", json={
        "design": design, "site_id": "jaisalmer",
        "period": {"start": "2019-06-10", "end": "2019-06-16"},
        "conditioned_run": False, "with_uncertainty": False,
        "with_reference": False}).json()
    assert body["headline_metric"] == "maximum_daytime_operative_c"
    assert body["comfort"]["maximum_daytime_operative_c"] is not None


def test_cfd_absence_is_stated_not_errored(client):
    response = client.get("/api/cfd/0123456789abcdef0123456789abcdef")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "has not been run" in body["reason"]


def test_cfd_is_served_when_it_exists(client):
    exports = sorted((DATA / "cfd").glob("*_coldest_night.json"))
    if not exports:
        pytest.skip("no precomputed fluid dynamics export")
    design_hash = exports[0].name.split("_")[0]
    body = client.get(f"/api/cfd/{design_hash}").json()
    assert body["surfaces"]
    assert body["zone_air_temperature_c"] is not None
    assert any("no air temperature field" in note.lower() or
               "computes no air temperature" in note for note in body["notes"])


def test_validation_reports_absence_honestly(client):
    body = client.get("/api/validation").json()
    assert body["available"] is False
    assert "Layer 1" in body["detail"]


def test_projects_round_trip(client, design):
    payload = {"id": "demo_project", "name": "Demo project", "design": design,
               "period": WINDOW}
    saved = client.put("/api/projects/demo_project", json=payload)
    assert saved.status_code == 200
    assert client.get("/api/projects/demo_project").json()["name"] == "Demo project"
    assert any(item["id"] == "demo_project"
               for item in client.get("/api/projects").json())
    assert client.delete("/api/projects/demo_project").status_code == 200
    assert client.get("/api/projects/demo_project").status_code == 404


def test_project_id_mismatch_is_rejected(client, design):
    payload = {"id": "one", "name": "One", "design": design, "period": WINDOW}
    assert client.put("/api/projects/two", json=payload).status_code == 422


def test_optimisation_runs_as_a_job(client, design):
    response = client.post("/api/optimise", json={
        "base_design": design,
        "variables": [{"name": "wall.layers.2.thickness_m", "kind": "real",
                       "low": 0.15, "high": 0.5}],
        "objectives": ["degree_hours_total_kh", "total_cost_inr"],
        "constraints": {"max_condensation_hours": 1000.0},
        "population_size": 8, "generations": 1,
        "period": {"start": "2019-01-10", "end": "2019-01-12"}})
    assert response.status_code == 200
    job = response.json()
    assert job["status"] in ("running", "done")
    final = client.get(f"/api/optimise/{job['job_id']}").json()
    assert final["status"] in ("running", "done", "failed"), final
    if final["status"] == "done":
        assert final["result"]["front"]


def test_openapi_documents_every_response_shape(client):
    schema = client.get("/openapi.json").json()
    assert len(schema["paths"]) >= 20
    components = schema["components"]["schemas"]
    for name in ("SimulationResult", "GeometryPayload", "ComparisonTable",
                 "DesignSpec", "EngineWarning", "Assumption"):
        assert name in components


def test_sun_path_is_physically_sane(client):
    body = client.get("/api/sun-path?site_id=leh&day=2019-01-15").json()
    assert body["noon"]["altitude_deg"] == pytest.approx(34.5, abs=1.0)
    assert body["noon"]["azimuth_deg"] == pytest.approx(180.0, abs=12.0)
    assert body["daylight_hours"] > 0.0
    assert body["source"]["citation"]


def test_terrain_delays_the_sun_in_a_valley(client):
    body = client.get("/api/sun-path?site_id=leh&day=2019-01-15").json()
    assert body["has_horizon"] is True
    assert body["visible_sunrise_hour"] > body["astronomical_sunrise_hour"]
    assert body["daylight_lost_to_terrain_h"] > 0.0


def test_summer_sun_climbs_higher_than_winter(client):
    winter = client.get("/api/sun-path?site_id=leh&day=2019-01-15").json()
    summer = client.get("/api/sun-path?site_id=leh&day=2019-06-21").json()
    assert summer["noon"]["altitude_deg"] > winter["noon"]["altitude_deg"] + 30


def test_sun_path_rejects_a_bad_date(client):
    assert client.get("/api/sun-path?day=not-a-date").status_code == 422


def test_terrain_mesh_is_drawable(client):
    body = client.get("/api/terrain?site_id=leh").json()
    assert body["derived_from_horizon"] is True
    assert len(body["positions"]) % 3 == 0
    assert len(body["indices"]) % 3 == 0
    assert max(body["indices"]) < len(body["positions"]) // 3
    assert body["ridge_max_m"] > 0.0


def test_terrain_display_radii_do_not_change_the_angles(client):
    near = client.get("/api/terrain?site_id=leh&inner_radius_m=50&outer_radius_m=100").json()
    far = client.get("/api/terrain?site_id=leh&inner_radius_m=50&outer_radius_m=400").json()
    assert far["ridge_max_m"] == pytest.approx(near["ridge_max_m"] * 4.0, rel=1e-3)


def test_terrain_rejects_an_inverted_ring(client):
    response = client.get("/api/terrain?inner_radius_m=400&outer_radius_m=100")
    assert response.status_code == 422


def test_climate_summary_is_read_from_the_weather_file(client):
    body = client.get("/api/climate?site_id=leh").json()
    assert body["hours"] == 8760
    assert len(body["months"]) == 12
    assert body["heating_degree_days"] > body["cooling_degree_days"]
    assert body["annual_global_horizontal_kwh_m2"] > 0
    assert body["design_day_window"]["timestamps"]
    assert body["sun_path"]["positions"]


def test_climate_reports_the_altitude_pressure_check(client):
    body = client.get("/api/climate?site_id=leh").json()
    assert body["expected_station_pressure_pa"] < 70000
    assert body["air_density_kg_m3"] < body["sea_level_air_density_kg_m3"]
    assert abs(
        body["mean_station_pressure_pa"] - body["expected_station_pressure_pa"]
    ) / body["expected_station_pressure_pa"] < 0.05


def test_climate_flips_the_design_day_in_a_cooling_climate(client):
    cold = client.get("/api/climate?site_id=leh").json()
    hot = client.get("/api/climate?site_id=jaisalmer").json()
    assert cold["design_day"][5:7] in ("01", "02", "12")
    assert hot["design_day"][5:7] in ("05", "06", "07", "08")
    assert hot["cooling_degree_days"] > hot["heating_degree_days"]


def test_climate_monthly_ground_temperatures_are_carried(client):
    body = client.get("/api/climate?site_id=leh").json()
    january = body["months"][0]
    assert january["ground_temperature_c"] is not None
    assert january["mean_daily_swing_k"] > 0
