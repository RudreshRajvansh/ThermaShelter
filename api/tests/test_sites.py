from __future__ import annotations

import io
import math
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
LATITUDE = 34.2
LONGITUDE = 77.6
SITE_ID = "n34.20000_e77.60000"
SITE_ELEVATION = 3000.0

pytestmark = pytest.mark.skipif(
    not (DATA / "materials.db").is_file() or not (DATA / "sites" / "leh.json").is_file(),
    reason="run scripts/start.py first so the Layer 0 data exists")


def _pressure_hpa(elevation_m: float) -> float:
    from shelter_engine.weather import standard_pressure_pa
    return standard_pressure_pa(elevation_m) / 100.0


GEOCODED = {
    "kargil": [{"name": "Kargil", "latitude": 34.55765, "longitude": 76.12622,
                "elevation": 2686.0, "admin1": "Ladakh", "country": "India",
                "country_code": "IN", "population": 16000},
               {"name": "Kargil", "latitude": 24.2, "longitude": 81.9,
                "admin1": "Madhya Pradesh", "country": "India", "country_code": "IN",
                "population": 900},
               {"name": "broken"}],
    "leh": [{"name": "Le Havre", "latitude": 49.49346, "longitude": 0.10785,
             "admin1": "Normandy", "country": "France", "country_code": "FR",
             "population": 170000},
            {"name": "Leh", "latitude": 34.16504, "longitude": 77.58402,
             "elevation": 3502.0, "admin1": "Ladakh", "country": "India",
             "country_code": "IN", "population": 30000}],
}


def fake_fetch(url: str, params: dict) -> dict:
    if "archive-api" in url:
        start = datetime.fromisoformat(params["start_date"])
        end = datetime.fromisoformat(params["end_date"]) + timedelta(hours=23)
        count = int((end - start).total_seconds() // 3600) + 1
        moments = [start + timedelta(hours=index) for index in range(count)]
        temperature, ghi, dni, dhi = [], [], [], []
        for moment in moments:
            solar_hour = (moment.hour + 5.5) % 24
            seasonal = -12.0 * math.cos(2 * math.pi * (moment.timetuple().tm_yday - 15) / 365)
            diurnal = 6.0 * math.sin(2 * math.pi * (solar_hour - 9.0) / 24.0)
            temperature.append(round(4.0 + seasonal + diurnal, 2))
            sun = max(0.0, math.sin(math.pi * (solar_hour - 6.0) / 12.0))
            ghi.append(round(750.0 * sun, 1))
            dni.append(round(800.0 * sun, 1))
            dhi.append(round(90.0 * sun, 1))
        return {
            "latitude": LATITUDE + 0.03, "longitude": LONGITUDE - 0.02,
            "elevation": SITE_ELEVATION,
            "hourly": {
                "time": [moment.strftime("%Y-%m-%dT%H:%M") for moment in moments],
                "temperature_2m": temperature,
                "dew_point_2m": [round(value - 12.0, 2) for value in temperature],
                "relative_humidity_2m": [40.0] * count,
                "surface_pressure": [round(_pressure_hpa(SITE_ELEVATION), 1)] * count,
                "wind_speed_10m": [3.0] * count,
                "wind_direction_10m": [270.0] * count,
                "shortwave_radiation": ghi,
                "direct_normal_irradiance": dni,
                "diffuse_radiation": dhi,
                "cloud_cover": [20.0] * count,
            }}
    if "nominatim" in url:
        return {"name": "Test Valley", "address": {"state": "Ladakh", "country_code": "in"}}
    if "geocoding-api" in url:
        return {"results": GEOCODED.get(params["name"].casefold(), [])}
    raise AssertionError(f"unexpected request to {url}")


def fake_tile(zoom: int, x: int, y: int) -> bytes:
    from PIL import Image

    world = 2 ** zoom * 256
    rows, _ = np.mgrid[0:256, 0:256]
    latitude = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * (y * 256 + rows + 0.5) / world))))
    height = np.where(latitude > LATITUDE + 0.03, SITE_ELEVATION + 900.0, SITE_ELEVATION)
    value = height + 32768.0
    red = np.floor(value / 256.0)
    green = np.floor(value - red * 256.0)
    blue = np.round((value - np.floor(value)) * 256.0)
    image = Image.fromarray(np.stack([red, green, blue], axis=-1).astype(np.uint8), "RGB")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def offline_fetch(url: str, params: dict) -> dict:
    from shelter_api.sites import SiteUnavailable
    raise SiteUnavailable("archive-api.open-meteo.com could not be reached (URLError).")


def offline_tile(zoom: int, x: int, y: int) -> bytes:
    from shelter_api.sites import SiteUnavailable
    raise SiteUnavailable("s3.amazonaws.com could not be reached (URLError).")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    shutil.copy2(DATA / "materials.db", root / "materials.db")
    (root / "sites").mkdir()
    shutil.copy2(DATA / "sites" / "leh.json", root / "sites" / "leh.json")
    shutil.copytree(DATA / "weather", root / "weather",
                    ignore=shutil.ignore_patterns("n*_e*.epw", "s*_w*.epw", "n*_w*.epw",
                                                  "s*_e*.epw"))
    shutil.copytree(DATA / "designs", root / "designs")

    names = ("SHELTER_DATA_DIR", "SHELTER_DB", "SHELTER_CFD_DIR", "SHELTER_SITES",
             "SHELTER_MATERIALS")
    saved = {name: os.environ.get(name) for name in names}
    os.environ["SHELTER_DATA_DIR"] = str(root)
    os.environ["SHELTER_DB"] = str(root / "runs.db")
    os.environ["SHELTER_CFD_DIR"] = str(root / "cfd")
    os.environ.pop("SHELTER_SITES", None)
    os.environ.pop("SHELTER_MATERIALS", None)

    import shelter_api.sites as sites
    from shelter_api.config import get_settings
    from shelter_api.deps import get_context
    from shelter_api.main import create_app

    original = sites.fetch_json
    original_tile = sites.fetch_tile_png
    sites.fetch_json = fake_fetch
    sites.fetch_tile_png = fake_tile
    get_settings.cache_clear()
    get_context.cache_clear()
    try:
        with TestClient(create_app()) as client:
            yield client, root, sites
    finally:
        sites.fetch_json = original
        sites.fetch_tile_png = original_tile
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()
        get_context.cache_clear()


def resolve(client, latitude=LATITUDE, longitude=LONGITUDE, name=None):
    return client.post("/api/sites/resolve", json={
        "latitude_deg": latitude, "longitude_deg": longitude, "name": name})


def test_a_picked_point_becomes_a_site_with_its_own_terrain_and_weather(world):
    client, root, _ = world
    response = resolve(client)
    assert response.status_code == 200, response.text
    body = response.json()
    site = body["site"]
    assert body["created"] is True
    assert site["id"] == SITE_ID
    assert site["name"] == "Test Valley, Ladakh"
    assert site["elevation_m"] == SITE_ELEVATION
    assert site["time_zone_h"] == 5.5
    assert site["climate_objective"] == "heating_dominated"
    horizon = dict(zip(site["horizon"]["azimuths_deg"], site["horizon"]["blocked_altitude_deg"]))
    assert horizon[0.0] > 5.0
    assert horizon[180.0] == 0.0
    assert len(site["ground"]["monthly_c"]) == 12
    provenance = body["provenance"]
    assert provenance["weather_year"] >= 2020
    assert "ERA5" in provenance["weather_dataset"]
    assert provenance["heating_degree_days"] > provenance["cooling_degree_days"]
    assert provenance["grid_distance_km"] > 0.0
    assert any("not a typical meteorological year" in note for note in body["notes"])
    assert not any("India Model" in note for note in body["notes"])
    assert not any(item["code"] == "weather_pressure_mismatch" for item in body["warnings"])
    assert "Terrain Tiles" in provenance["elevation_source"]
    assert (root / "sites" / f"{SITE_ID}.json").is_file()
    assert (root / "sites" / "provenance" / f"{SITE_ID}.json").is_file()
    assert (root / "terrain" / f"{SITE_ID}.obj").is_file()
    assert list((root / "terrain" / "tiles").rglob("*.png"))
    assert list((root / "weather").glob(f"{SITE_ID}_era5_*.epw"))


def test_the_site_is_listed_and_reused_without_the_network(world):
    client, _, sites = world
    assert resolve(client).status_code == 200
    sites.fetch_json = offline_fetch
    try:
        again = resolve(client)
        assert again.status_code == 200
        assert again.json()["created"] is False
        assert again.json()["provenance"]["weather_year"] >= 2020
        detail = client.get(f"/api/sites/{SITE_ID}").json()
        assert detail["site"]["id"] == SITE_ID and detail["provenance"] is not None
    finally:
        sites.fetch_json = fake_fetch
    listed = [item["id"] for item in client.get("/api/meta").json()["sites"]]
    assert SITE_ID in listed and "leh" in listed


def test_an_unreachable_service_is_reported_and_nothing_is_written(world):
    client, root, sites = world
    sites.fetch_json = offline_fetch
    sites.fetch_tile_png = offline_tile
    try:
        response = resolve(client, latitude=-12.34567, longitude=-45.67891)
    finally:
        sites.fetch_json = fake_fetch
        sites.fetch_tile_png = fake_tile
    assert response.status_code == 503
    assert "could not be reached" in response.json()["detail"]
    assert "too far to stand in" in response.json()["detail"]
    assert not (root / "sites" / "s12.34567_w45.67891.json").exists()
    assert not list((root / "weather").glob("s12.34567_w45.67891*"))
    assert not (root / "terrain" / "s12.34567_w45.67891.obj").exists()


def test_weather_failure_after_the_terrain_leaves_no_partial_site(world):
    client, root, sites = world
    sites.fetch_json = offline_fetch
    try:
        response = resolve(client, latitude=LATITUDE + 0.2, longitude=LONGITUDE)
    finally:
        sites.fetch_json = fake_fetch
    assert response.status_code == 200
    stem = "n34.40000_e77.60000"
    assert not (root / "sites" / f"{stem}.json").exists()
    assert not (root / "terrain" / f"{stem}.obj").exists()
    assert not list((root / "weather").glob(f"{stem}*"))

    body = response.json()
    assert body["site"]["id"] != stem, "a nearby built site stands in for the point"
    assert body["created"] is False
    assert body["requested_latitude_deg"] == 34.4
    assert body["requested_longitude_deg"] == 77.6
    assert 0 < body["substituted_km"] < 50
    substitution = body["warnings"][0]
    assert substitution["code"] == "site_substituted"
    assert substitution["severity"] == "severe"
    assert "not to the point you clicked" in substitution["message"]


def test_out_of_range_coordinates_are_refused(world):
    client, _, _ = world
    assert resolve(client, latitude=95.0).status_code == 422
    assert resolve(client, longitude=-181.0).status_code == 422


def test_place_search_returns_usable_places_only(world):
    client, _, _ = world
    places = client.get("/api/places", params={"query": "Kargil"}).json()
    assert [place["name"] for place in places] == ["Kargil", "Kargil"]
    assert places[0]["region"] == "Ladakh"
    assert client.get("/api/places", params={"query": "K"}).status_code == 422


def test_place_search_falls_back_to_the_leading_words(world):
    client, _, _ = world
    places = client.get("/api/places", params={"query": "Leh Ladakh"}).json()
    assert places, "a place name followed by its region must still find the place"
    assert places[0]["name"] == "Leh"
    assert places[0]["country_code"] == "IN"


def test_place_search_puts_the_named_place_before_a_bigger_lookalike(world):
    client, _, _ = world
    places = client.get("/api/places", params={"query": "Leh"}).json()
    assert [place["name"] for place in places] == ["Leh", "Le Havre"]


def test_place_search_prefers_the_region_the_user_typed(world):
    client, _, _ = world
    places = client.get("/api/places", params={"query": "Kargil Ladakh"}).json()
    assert [place["region"] for place in places] == ["Ladakh", "Madhya Pradesh"]


def test_a_new_site_draws_its_own_terrain_not_the_leh_mesh(world):
    client, _, _ = world
    assert resolve(client).status_code == 200
    terrain = client.get("/api/terrain", params={"site_id": SITE_ID}).json()
    assert terrain["derived_from_horizon"] is False
    assert terrain["site_id"] == SITE_ID
    assert terrain["vertex_count"] == 51 * 51
    assert terrain["elevation_max_m"] == pytest.approx(900.0, abs=1.0)


def test_the_stored_leh_site_explains_itself(world):
    client, _, _ = world
    detail = client.get("/api/sites/leh").json()
    assert detail["provenance"] is None
    assert detail["notes"]
    assert client.get("/api/sites/nowhere").status_code == 404


def test_a_site_outside_india_carries_the_comfort_model_caveat(world):
    client, _, sites = world

    def abroad(url: str, params: dict) -> dict:
        if "nominatim" in url:
            return {"name": "Test Pass", "address": {"country_code": "np"}}
        return fake_fetch(url, params)

    sites.fetch_json = abroad
    try:
        body = resolve(client, latitude=LATITUDE, longitude=LONGITUDE + 0.5).json()
    finally:
        sites.fetch_json = fake_fetch
    assert any("India Model for Adaptive Comfort" in note for note in body["notes"])
