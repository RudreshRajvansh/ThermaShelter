from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .schemas import LiveObservation, LiveWeather

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 4.0
ELEVATION_TOLERANCE_M = 150.0

PROVIDER = (
    "Open-Meteo, which blends the national and global forecast models for the "
    "requested point. It is not the India Meteorological Department: IMD's public "
    "endpoints require the caller's IP to be whitelisted, so they cannot be reached "
    "from an offline demonstration machine.")

NOT_AN_INPUT = (
    "Current conditions at the site, for context only. The simulation does not read "
    "this and never will: it runs against the frozen Layer 0 weather year so that the "
    "same design gives the same answer every time, including with the network cable "
    "pulled out. Judge the shelter on the simulated year, not on today.")


def _url(latitude: float, longitude: float) -> str:
    query = urllib.parse.urlencode({
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "current": ",".join((
            "temperature_2m", "relative_humidity_2m", "apparent_temperature",
            "wind_speed_10m", "surface_pressure", "cloud_cover",
        )),
        "timezone": "Asia/Kolkata",
    })
    return f"{ENDPOINT}?{query}"


def fetch_live_weather(site_id: str, name: str, latitude: float, longitude: float,
                       elevation_m: float) -> LiveWeather:
    started = datetime.now(timezone.utc)
    try:
        request = urllib.request.Request(
            _url(latitude, longitude),
            headers={"Accept": "application/json", "User-Agent": "shelter-api"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
        return LiveWeather(
            site_id=site_id, available=False,
            detail=(f"No live observation could be fetched: {type(error).__name__}. "
                    "This changes nothing about the result on screen, which comes "
                    "from the stored weather year."),
            provider=PROVIDER, note=NOT_AN_INPUT)

    current = payload.get("current") or {}
    if not current:
        return LiveWeather(
            site_id=site_id, available=False,
            detail="The provider returned no current block.",
            provider=PROVIDER, note=NOT_AN_INPUT)

    reported = payload.get("elevation")
    mismatch = None
    if isinstance(reported, (int, float)):
        gap = abs(float(reported) - elevation_m)
        if gap > ELEVATION_TOLERANCE_M:
            mismatch = (
                f"The provider resolved this point to {float(reported):.0f} m against "
                f"the {elevation_m:.0f} m of the weather station, a {gap:.0f} m "
                "difference. At altitude that is a real temperature difference, so "
                "treat the reading as the valley rather than the site.")

    def number(key: str) -> float | None:
        value = current.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    return LiveWeather(
        site_id=site_id, available=True,
        observed_at=str(current.get("time") or started.isoformat()),
        fetched_at=started.isoformat(),
        location=name,
        provider_elevation_m=float(reported) if isinstance(reported, (int, float))
        else None,
        elevation_mismatch=mismatch,
        observation=LiveObservation(
            air_temperature_c=number("temperature_2m"),
            apparent_temperature_c=number("apparent_temperature"),
            relative_humidity_pct=number("relative_humidity_2m"),
            wind_speed_m_s=(
                number("wind_speed_10m") / 3.6
                if number("wind_speed_10m") is not None else None),
            surface_pressure_pa=(
                number("surface_pressure") * 100.0
                if number("surface_pressure") is not None else None),
            cloud_cover_pct=number("cloud_cover"),
        ),
        provider=PROVIDER,
        note=NOT_AN_INPUT)
