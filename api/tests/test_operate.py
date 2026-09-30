from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

pytestmark = pytest.mark.skipif(not (DATA / "materials.db").is_file(),
                                reason="run scripts/build_data.py first")


def hourly(start: datetime, hours: int, temperature: float = -15.0) -> dict:
    times = [(start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M")
             for index in range(hours)]
    return {"hourly": {
        "time": times, "temperature_2m": [temperature] * hours,
        "dew_point_2m": [temperature - 8.0] * hours,
        "relative_humidity_2m": [50.0] * hours, "surface_pressure": [680.0] * hours,
        "wind_speed_10m": [2.0] * hours, "wind_direction_10m": [200.0] * hours,
        "shortwave_radiation": [0.0] * hours, "direct_normal_irradiance": [0.0] * hours,
        "diffuse_radiation": [0.0] * hours, "cloud_cover": [30.0] * hours}}


def test_forecast_is_cached_and_the_cache_ages_out(tmp_path):
    from shelter_api import operate, sites

    now = datetime(2026, 12, 14, 18, 0)
    good = operate.forecast_for("P1", 34.1, 77.5, 3500.0, tmp_path, now,
                                fetch=lambda url, params: hourly(datetime(2026, 12, 6), 240))
    assert good.source == "forecast" and len(good.utc_times) == 240
    assert good.columns["pressure_pa"][0] == pytest.approx(68000.0)

    def offline(url, params):
        raise sites.SiteUnavailable("no link")

    cached = operate.forecast_for("P1", 34.1, 77.5, 3500.0, tmp_path, now + timedelta(hours=5),
                                  fetch=offline)
    assert cached.source == "cached forecast" and cached.fetched_at == now
    assert operate.forecast_for("P1", 34.1, 77.5, 3500.0, tmp_path, now + timedelta(days=4),
                                fetch=offline) is None
    assert operate.forecast_for("P2", 34.1, 77.5, 3500.0, tmp_path, now, fetch=offline) is None


def test_the_night_and_the_forecast_reach():
    from shelter_api import operate
    from shelter_engine.errors import WeatherError

    assert operate.night_of_for(datetime(2026, 12, 14, 18, 5)) == date(2026, 12, 14)
    assert operate.night_of_for(datetime(2026, 12, 15, 3, 0)) == date(2026, 12, 14)
    times = np.arange(np.datetime64("2026-12-06T00:00"), np.datetime64("2026-12-17T00:00"),
                      np.timedelta64(1, "h")).astype("datetime64[s]")
    forecast = operate.Forecast(times, {}, datetime(2026, 12, 14), "forecast")
    assert operate._last_covered(forecast, 5.5, date(2026, 12, 14)) == date(2026, 12, 15)
    short = operate.Forecast(times[:200], {}, datetime(2026, 12, 14), "forecast")
    with pytest.raises(WeatherError):
        operate._last_covered(short, 5.5, date(2026, 12, 14))


def test_calibration_scales_only_the_heavy_layers_and_sets_the_leakage():
    import sys
    sys.path.insert(0, str(ROOT / "engine" / "tests"))
    import fixtures
    from shelter_api import operate

    library = fixtures.material_library()
    design = fixtures.design()
    scaled = operate.mass_scaled(library, design, 1.5)
    for layer in design.wall.layers:
        before = library.material(layer.material_id).density_kg_m3
        after = scaled.material(layer.material_id).density_kg_m3
        assert after == pytest.approx(before * 1.5 if before >= 800.0 else before)
    assert operate.mass_scaled(library, design, 1.0) is library
    tuned = operate.calibrated_design(design, {"infiltration_ach": 1.7})
    assert tuned.ventilation.infiltration_ach == 1.7
    assert operate.calibrated_design(design, None) is design


def test_the_scheduler_waits_until_the_next_run_time():
    from shelter_api.operate import Scheduler

    scheduler = Scheduler("18:00", lambda: None)
    assert scheduler.seconds_until_next(datetime(2026, 12, 14, 17, 0)) == 3600.0
    assert scheduler.seconds_until_next(datetime(2026, 12, 14, 18, 0)) == 24 * 3600.0
    with pytest.raises(ValueError):
        Scheduler("25:00", lambda: None)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["SHELTER_DATA_DIR"] = str(DATA)
    os.environ["SHELTER_DB"] = str(tmp_path_factory.mktemp("operate") / "runs.db")
    from shelter_api.config import get_settings
    from shelter_api.deps import get_context
    get_settings.cache_clear()
    get_context.cache_clear()
    from shelter_api.main import create_app
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()
    get_context.cache_clear()


def post_body(**changes) -> dict:
    design = json.loads((DATA / "designs" / "barrack_single_room.json").read_text(
        encoding="utf-8"))
    body = {"id": "P-17", "name": "Post 17", "site_id": "leh", "latitude_deg": 34.136,
            "longitude_deg": 77.547, "design": design, "soldiers": 20, "fuel": "kerosene",
            "stock_units": 12.0}
    body.update(changes)
    return body


def test_posts_are_registered_listed_and_removed(client):
    assert client.put("/api/posts/P-17", json=post_body()).status_code == 200
    assert client.put("/api/posts/P-18", json=post_body()).status_code == 422
    assert client.put("/api/posts/P-19", json=post_body(id="P-19",
                                                          site_id="atlantis")).status_code == 404
    assert client.put("/api/posts/bad id", json=post_body()).status_code in (404, 422)
    listed = client.get("/api/posts").json()
    assert [item["post"]["id"] for item in listed] == ["P-17"] and listed[0]["status"] is None
    assert client.get("/api/posts/P-17/card").status_code == 404
    assert client.post("/api/posts/NOPE/card").status_code == 404
    assert client.delete("/api/posts/P-17").status_code == 204
    assert client.delete("/api/posts/P-17").status_code == 404


def test_an_offline_post_still_gets_a_card_on_climatology(client, monkeypatch):
    from shelter_api import operate, sites

    def offline(*args, **kwargs):
        raise sites.SiteUnavailable("no link")

    monkeypatch.setattr(operate, "fetch_forecast", offline)
    client.put("/api/posts/P-20", json=post_body(id="P-20", stock_units=0.5))
    response = client.post("/api/posts/P-20/card?lang=hi")
    if response.status_code == 503:
        pytest.skip("EnergyPlus is not available")
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["forecast_source"] == "climatology" and card["lang"] == "hi"
    assert card["notes"] and "typical weather" in card["notes"][0]
    assert client.get("/api/posts/P-20/card?lang=hi").json()["issued_at"] == card["issued_at"]
    everything = client.post("/api/posts/cards?lang=hi").json()
    assert everything[0]["post_id"] == "P-20" and everything[0]["card"]
    assert client.get("/api/posts?lang=hi").json()[0]["status"] == card["status"]


def test_calibration_recovers_the_leakage_that_made_the_logger(client, monkeypatch, tmp_path):
    from shelter_api import operate
    from shelter_api.deps import get_context

    context = get_context()
    if not context.ready:
        pytest.skip("EnergyPlus is not available")
    weather = hourly(datetime(2026, 9, 9), 24 * 7, temperature=2.0)["hourly"]
    for index in range(len(weather["time"])):
        hour = index % 24
        weather["temperature_2m"][index] = 2.0 + 8.0 * np.sin(np.pi * (hour - 6) / 12.0)
        weather["shortwave_radiation"][index] = max(0.0, 700.0 * np.sin(np.pi * (hour - 1) / 12.0))
        weather["direct_normal_irradiance"][index] = weather["shortwave_radiation"][index]
    monkeypatch.setattr(operate, "fetch_window", lambda *args, **kwargs: weather)
    body = post_body(id="P-30", design=post_body()["design"])
    client.put("/api/posts/P-30", json=body)
    site = context.site("leh")
    kwargs = {"comfort_model_id": context.engine("leh").comfort_model.id, "fuel": "kerosene",
              "references": context.references, "catalogue": context.catalogue}
    evaluate = operate.calibration_model(body, site, context.library, tmp_path,
                                         date(2026, 9, 10), date(2026, 9, 13), weather, kwargs)
    truth = evaluate(1.6, 1.0)
    lines = ["timestamp,temperature_c"] + [f"{stamp:%Y-%m-%d %H:%M},{value:.3f}"
                                           for stamp, value in truth.items()]
    response = client.post("/api/posts/P-30/calibration", content="\n".join(lines) + "\n",
                           headers={"Content-Type": "text/csv"})
    assert response.status_code == 200, response.text
    fitted = response.json()
    assert fitted["rmse_after_k"] < fitted["rmse_before_k"]
    assert fitted["rmse_after_k"] < 0.25
    assert fitted["infiltration_ach"] == pytest.approx(1.6, abs=0.3)
    saved = client.get("/api/posts").json()
    assert next(item for item in saved if item["post"]["id"] == "P-30")["post"]["calibration"]
    assert client.post("/api/posts/P-30/calibration", content="").status_code == 422
    assert client.post("/api/posts/NOPE/calibration", content="x").status_code == 404
    future = "timestamp,temperature_c\n" + "\n".join(
        f"2030-01-01 {hour:02d}:00,5" for hour in range(24)) + "\n2030-01-02 00:00,5\n"
    assert client.post("/api/posts/P-30/calibration", content=future).status_code == 422


def test_outside_calls_carry_only_the_grid_and_stop_when_offline(tmp_path, monkeypatch):
    from shelter_api import egress, live, operate, sites

    captured = {}

    def capture(url, params):
        captured.update(params)
        return hourly(datetime(2026, 12, 6), 240)

    operate.forecast_for("P9", 34.13647, 77.54712, 3500.0, tmp_path, datetime(2026, 12, 14),
                         fetch=capture)
    assert captured["latitude"] == "34.1" and captured["longitude"] == "77.5"
    assert egress.grid(-12.349) == "-12.3" and egress.grid(0.04) == "0.0"
    monkeypatch.setenv("SHELTER_OFFLINE", "1")
    with pytest.raises(sites.SiteUnavailable, match="SHELTER_OFFLINE"):
        sites.fetch_json("https://example.org/x", {})
    weather = live.fetch_live_weather("leh", "Leh", 34.1, 77.5, 3500.0)
    assert weather.available is False and "SHELTER_OFFLINE" in weather.detail
    assert operate.forecast_for("P10", 34.1, 77.5, 3500.0, tmp_path, datetime(2026, 12, 14)) \
        is None
    monkeypatch.setenv("SHELTER_FORECAST_URL", "http://forecast.local/v1/forecast")
    assert egress.endpoint("SHELTER_FORECAST_URL", "x") == "http://forecast.local/v1/forecast"
    monkeypatch.delenv("SHELTER_OFFLINE")
    assert not egress.offline()
