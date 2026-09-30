from __future__ import annotations

import io
import json
import math
import unicodedata
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import numpy as np

from shelter_engine.errors import WeatherError
from shelter_engine.schema import SiteSpec, SourceRef, SourceStatus
from shelter_engine.site_data import (HORIZON_FARTHEST_M, HORIZON_SAMPLES, TILE_SIZE,
                                      DemMosaic, climate_objective, components_from_wind,
                                      great_circle_km, ground_temperatures,
                                      horizon_azimuths_deg, horizon_distances_m,
                                      horizon_from_mosaics, horizon_profile,
                                      resample_to_local_standard, terrain_obj, tile_range,
                                      wind_from_components, zoom_for)
from shelter_engine.weather import load_epw

from . import egress

TERRAIN_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ARCHIVE_URL = egress.endpoint("SHELTER_ARCHIVE_URL",
                              "https://archive-api.open-meteo.com/v1/archive")
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "EntropyZero-shelter-design/0.1"
TIMEOUT_S = 45.0
RETRIES = 4
MAX_BACKOFF_S = 30.0
NEAR_RADIUS_M = 6000.0
NEAR_ZOOM = 12
FAR_ZOOM = 9
MAX_TILES = 16
LOWEST_SITE_M = -100.0
MAX_MISSING_FRACTION = 0.01
MAX_SEARCH_TERMS = 3
BASE_C = 18.0
HOURLY = ("temperature_2m", "dew_point_2m", "relative_humidity_2m", "surface_pressure",
          "wind_speed_10m", "wind_direction_10m", "shortwave_radiation",
          "direct_normal_irradiance", "diffuse_radiation", "cloud_cover")

ELEVATION_SOURCE = ("AWS Terrain Tiles (Terrarium encoding), built from SRTM, GMTED and "
                    "other open elevation data")
WEATHER_PROVIDER = "Open-Meteo historical weather archive"
WEATHER_MODEL = "ecmwf_ifs"
WEATHER_MODEL_FIRST_YEAR = 2017
WEATHER_DATASET = ("ECMWF IFS 9 km analysis, downscaled to the site elevation by "
                   "Open-Meteo")
WEATHER_MODEL_NOTE = (
    "Chosen over ERA5 and ERA5-Land after checking all three against IMD's own record at "
    "Leh: over the winters both cover, the coldest night in ERA5 averaged 8.3 K and in "
    "ERA5-Land 11.3 K colder than the station observed, while IFS came within 1.0 K. "
    "The coarse grids see a smoothed plateau, not the valley a post sits in.")

Fetcher = Callable[[str, dict[str, Any]], Any]


class SiteUnavailable(Exception):
    pass


def _retry_after_s(error: urllib.error.HTTPError, attempt: int) -> float:
    header = error.headers.get("Retry-After") if error.headers else None
    try:
        requested = float(header) if header is not None else None
    except ValueError:
        requested = None
    return min(MAX_BACKOFF_S, requested if requested is not None else 2.0 ** attempt)


def _download(url: str, accept: str) -> bytes:
    if egress.offline():
        raise SiteUnavailable(egress.OFFLINE_MESSAGE)
    request = urllib.request.Request(url, headers={"Accept": accept,
                                                   "User-Agent": USER_AGENT})
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503) and attempt < RETRIES:
                time.sleep(_retry_after_s(error, attempt))
                continue
            reason = ("is rate limiting requests, try again in a minute"
                      if error.code == 429 else f"answered HTTP {error.code}")
            raise SiteUnavailable(f"{host} {reason}. Sites already built keep working "
                                  "offline.") from error
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise SiteUnavailable(
                f"{host} could not be reached ({type(error).__name__}). Picking a new "
                "point needs the internet once; sites already built keep working "
                "offline.") from error
    raise SiteUnavailable(f"{host} did not answer after {RETRIES + 1} attempts")


def fetch_json(url: str, params: dict[str, Any]) -> Any:
    query = urllib.parse.urlencode(params, safe=",")
    raw = _download(f"{url}?{query}", "application/json")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as error:
        raise SiteUnavailable(f"{urllib.parse.urlparse(url).netloc} returned unreadable "
                              "data") from error


def fetch_tile_png(zoom: int, x: int, y: int) -> bytes:
    return _download(TERRAIN_URL.format(z=zoom, x=x, y=y), "image/png")


def _fetcher(fetch: Fetcher | None) -> Fetcher:
    return fetch if fetch is not None else fetch_json


def site_id_for(latitude_deg: float, longitude_deg: float) -> str:
    return (f"{'n' if latitude_deg >= 0 else 's'}{abs(latitude_deg):.5f}_"
            f"{'e' if longitude_deg >= 0 else 'w'}{abs(longitude_deg):.5f}")


def coordinate_label(latitude_deg: float, longitude_deg: float) -> str:
    return (f"{abs(latitude_deg):.5f}° {'N' if latitude_deg >= 0 else 'S'}, "
            f"{abs(longitude_deg):.5f}° {'E' if longitude_deg >= 0 else 'W'}")


def _folded(text: str) -> str:
    stripped = "".join(character for character
                       in unicodedata.normalize("NFKD", str(text))
                       if not unicodedata.combining(character))
    return " ".join(stripped.casefold().replace(",", " ").split())


def search_terms(query: str) -> list[str]:
    typed = " ".join(str(query).split())
    words = _folded(query).split()
    if not words:
        return []
    terms = [typed]
    for cut in range(len(words), 0, -1):
        candidate = " ".join(words[:cut])
        if candidate.casefold() not in {term.casefold() for term in terms}:
            terms.append(candidate)
    return terms[:MAX_SEARCH_TERMS]


def _score(place: dict[str, Any], words: list[str]) -> tuple[int, int, float]:
    name = _folded(place["name"])
    whole = " ".join(words)
    exact = 2 if name == whole else 1 if name.startswith(words[0]) else 0
    context = _folded(" ".join(str(part) for part
                               in (place["region"], place["country"],
                                   place["country_code"] or "")))
    matched = sum(1 for word in words[1:] if word in context)
    return (exact, matched, float(place.get("population") or 0.0))


def search_places(query: str, count: int = 8, fetch: Fetcher | None = None
                  ) -> list[dict[str, Any]]:
    terms = search_terms(query)
    if not terms:
        return []
    words = _folded(query).split()
    fetcher = _fetcher(fetch)
    places: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()

    for term in terms:
        payload = fetcher(GEOCODING_URL, {"name": term, "count": count,
                                          "language": "en", "format": "json"})
        results = payload.get("results") if isinstance(payload, dict) else None
        for item in results or []:
            try:
                latitude = float(item["latitude"])
                longitude = float(item["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            key = (round(latitude, 4), round(longitude, 4))
            if key in seen:
                continue
            seen.add(key)
            elevation = item.get("elevation")
            population = item.get("population")
            places.append({
                "name": str(item.get("name") or coordinate_label(latitude, longitude)),
                "region": ", ".join(str(part) for part
                                    in (item.get("admin2"), item.get("admin1")) if part),
                "country": str(item.get("country") or ""),
                "country_code": str(item["country_code"]).upper()
                if item.get("country_code") else None,
                "latitude_deg": latitude, "longitude_deg": longitude,
                "elevation_m": float(elevation) if isinstance(elevation, (int, float))
                else None,
                "population": float(population) if isinstance(population, (int, float))
                else None,
            })
        if len(places) >= count:
            break

    places.sort(key=lambda place: _score(place, words), reverse=True)
    for place in places:
        place.pop("population", None)
    return places[:count]


def decode_terrarium(data: bytes) -> np.ndarray:
    from PIL import Image, UnidentifiedImageError

    try:
        image = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=float)
    except (UnidentifiedImageError, OSError) as error:
        raise SiteUnavailable("an elevation tile could not be decoded") from error
    if image.shape != (TILE_SIZE, TILE_SIZE, 3):
        raise SiteUnavailable(f"an elevation tile has shape {image.shape}, expected "
                              f"{TILE_SIZE} by {TILE_SIZE} pixels")
    return image[..., 0] * 256.0 + image[..., 1] + image[..., 2] / 256.0 - 32768.0


def load_mosaic(latitude_deg: float, longitude_deg: float, radius_m: float,
                preferred_zoom: int, cache_dir: Path) -> DemMosaic:
    try:
        zoom = zoom_for(latitude_deg, longitude_deg, radius_m, preferred_zoom, MAX_TILES)
        x0, x1, y0, y1 = tile_range(latitude_deg, longitude_deg, radius_m, zoom)
    except WeatherError as error:
        raise SiteUnavailable(str(error)) from error
    count = 2 ** zoom
    heights = np.empty(((y1 - y0 + 1) * TILE_SIZE, (x1 - x0 + 1) * TILE_SIZE))
    for row, tile_y in enumerate(range(y0, y1 + 1)):
        for column, tile_x in enumerate(range(x0, x1 + 1)):
            wrapped = tile_x % count
            path = cache_dir / str(zoom) / str(wrapped) / f"{tile_y}.png"
            if path.is_file():
                data = path.read_bytes()
            else:
                data = fetch_tile_png(zoom, wrapped, tile_y)
                decode_terrarium(data)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            heights[row * TILE_SIZE:(row + 1) * TILE_SIZE,
                    column * TILE_SIZE:(column + 1) * TILE_SIZE] = decode_terrarium(data)
    return DemMosaic(zoom=zoom, tile_x0=x0, tile_y0=y0, heights_m=heights)


@lru_cache(maxsize=1)
def _time_zone_finder():
    from timezonefinder import TimezoneFinder
    return TimezoneFinder()


def standard_offset_h(latitude_deg: float, longitude_deg: float,
                      year: int) -> tuple[float, str | None]:
    from zoneinfo import ZoneInfo

    zone = _time_zone_finder().timezone_at(lat=latitude_deg, lng=longitude_deg)
    if zone is None:
        return float(round(longitude_deg / 15.0)), None
    moment = datetime(year, 1, 15, 12, tzinfo=ZoneInfo(zone))
    offset = moment.utcoffset()
    if offset is None:
        return float(round(longitude_deg / 15.0)), None
    standard = offset - (moment.dst() or timedelta(0))
    return standard.total_seconds() / 3600.0, zone


def reverse_place(latitude_deg: float, longitude_deg: float,
                  fetch: Fetcher | None = None) -> tuple[str | None, str | None]:
    try:
        payload = _fetcher(fetch)(REVERSE_URL, {
            "format": "jsonv2", "lat": egress.grid(latitude_deg),
            "lon": egress.grid(longitude_deg),
            "zoom": 14, "accept-language": "en"})
    except SiteUnavailable:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    address = payload.get("address") if isinstance(payload.get("address"), dict) else {}
    local = payload.get("name") or next(
        (address.get(key) for key in ("village", "town", "city", "hamlet", "suburb",
                                      "county", "state_district") if address.get(key)), None)
    parts = [local, address.get("state_district") or address.get("county"),
             address.get("state")]
    label = ", ".join(dict.fromkeys(str(part) for part in parts if part))
    code = address.get("country_code")
    return (label or None), (str(code).upper() if code else None)


def _column(hourly: dict[str, Any], name: str) -> np.ndarray:
    raw = hourly.get(name)
    if not isinstance(raw, list):
        raise SiteUnavailable(f"the weather archive returned no {name}")
    array = np.array([float(value) if isinstance(value, (int, float)) else np.nan
                      for value in raw], dtype=float)
    missing = ~np.isfinite(array)
    if missing.mean() > MAX_MISSING_FRACTION:
        raise SiteUnavailable(
            f"the weather archive is missing {missing.mean():.1%} of {name} for this point")
    if missing.any():
        index = np.arange(len(array))
        array[missing] = np.interp(index[missing], index[~missing], array[~missing])
    return array


def fetch_weather_year(latitude_deg: float, longitude_deg: float, elevation_m: float,
                       year: int, utc_offset_h: float,
                       fetch: Fetcher | None = None) -> dict[str, Any]:
    payload = _fetcher(fetch)(ARCHIVE_URL, {
        "latitude": egress.grid(latitude_deg), "longitude": egress.grid(longitude_deg),
        "elevation": f"{elevation_m:.0f}", "start_date": f"{year - 1}-12-31",
        "end_date": f"{year + 1}-01-01", "hourly": ",".join(HOURLY),
        "wind_speed_unit": "ms", "timezone": "GMT", "models": WEATHER_MODEL})
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise SiteUnavailable("the weather archive returned no hourly series")
    try:
        times = np.array(hourly["time"], dtype="datetime64[s]")
    except ValueError as error:
        raise SiteUnavailable(f"the weather archive timestamps are unreadable: {error}") \
            from error
    columns = {name: _column(hourly, name) for name in HOURLY}
    east, north = components_from_wind(columns["wind_speed_10m"],
                                       columns["wind_direction_10m"])
    try:
        local = resample_to_local_standard(times, {
            "dry_bulb_c": columns["temperature_2m"],
            "dew_point_c": columns["dew_point_2m"],
            "relative_humidity_pct": columns["relative_humidity_2m"],
            "pressure_pa": columns["surface_pressure"] * 100.0,
            "east_m_s": east, "north_m_s": north,
            "global_horizontal_wh_m2": columns["shortwave_radiation"],
            "direct_normal_wh_m2": columns["direct_normal_irradiance"],
            "diffuse_horizontal_wh_m2": columns["diffuse_radiation"],
            "cloud_pct": columns["cloud_cover"],
        }, year, utc_offset_h)
    except WeatherError as error:
        raise SiteUnavailable(str(error)) from error
    speed, direction = wind_from_components(local.pop("east_m_s"), local.pop("north_m_s"))
    local["wind_speed_m_s"] = speed
    local["wind_direction_deg"] = direction
    local["relative_humidity_pct"] = np.clip(local["relative_humidity_pct"], 0.0, 100.0)
    local["dew_point_c"] = np.minimum(local["dew_point_c"], local["dry_bulb_c"])
    for name in ("global_horizontal_wh_m2", "direct_normal_wh_m2",
                 "diffuse_horizontal_wh_m2"):
        local[name] = np.clip(local[name], 0.0, None)
    local["sky_cover_tenths"] = np.clip(np.round(local.pop("cloud_pct") / 10.0), 0.0, 10.0)
    grid = {key: payload.get(key) for key in ("latitude", "longitude", "elevation")}
    return {"columns": local, "grid": grid}


def write_epw(path: Path, name: str, country_code: str | None, latitude_deg: float,
              longitude_deg: float, utc_offset_h: float, elevation_m: float, year: int,
              columns: dict[str, np.ndarray], comments: tuple[str, str]) -> Path:
    from ladybug.epw import EPW
    from ladybug.location import Location

    epw = EPW.from_missing_values(is_leap_year=False)
    epw.location = Location(
        city=name.replace(",", " ")[:60] or "Picked site", state="-",
        country=(country_code or "-")[:3], latitude=round(latitude_deg, 5),
        longitude=round(longitude_deg, 5), time_zone=utc_offset_h,
        elevation=round(elevation_m, 1), station_id="ERA5",
        source=f"Open-Meteo archive {year}")

    def assign(collection, values: np.ndarray, digits: int) -> None:
        collection.values = [round(float(value), digits) for value in values]

    assign(epw.dry_bulb_temperature, columns["dry_bulb_c"], 1)
    assign(epw.dew_point_temperature, columns["dew_point_c"], 1)
    assign(epw.relative_humidity, columns["relative_humidity_pct"], 0)
    assign(epw.atmospheric_station_pressure, columns["pressure_pa"], 0)
    assign(epw.global_horizontal_radiation, columns["global_horizontal_wh_m2"], 0)
    assign(epw.direct_normal_radiation, columns["direct_normal_wh_m2"], 0)
    assign(epw.diffuse_horizontal_radiation, columns["diffuse_horizontal_wh_m2"], 0)
    assign(epw.wind_speed, columns["wind_speed_m_s"], 1)
    assign(epw.wind_direction, columns["wind_direction_deg"], 0)
    assign(epw.total_sky_cover, columns["sky_cover_tenths"], 0)
    assign(epw.opaque_sky_cover, columns["sky_cover_tenths"], 0)
    epw.comments_1 = comments[0][:300]
    epw.comments_2 = comments[1][:300]
    path.parent.mkdir(parents=True, exist_ok=True)
    epw.save(str(path))
    return path


@dataclass(frozen=True)
class BuiltSite:
    site: SiteSpec
    provenance: dict[str, Any]
    notes: tuple[str, ...]
    created: bool


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(identifier: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(identifier, threading.Lock())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def provenance_path(data_dir: Path, identifier: str) -> Path:
    return Path(data_dir) / "sites" / "provenance" / f"{identifier}.json"


def load_provenance(data_dir: Path, identifier: str) -> tuple[dict[str, Any] | None,
                                                               tuple[str, ...]]:
    path = provenance_path(data_dir, identifier)
    if not path.is_file():
        return None, ()
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, ()
    return stored.get("provenance"), tuple(stored.get("notes", ()))


def build_site(latitude_deg: float, longitude_deg: float, name: str | None,
               data_dir: Path, fetch: Fetcher | None = None,
               today: date | None = None) -> BuiltSite:
    if not (math.isfinite(latitude_deg) and -90.0 <= latitude_deg <= 90.0
            and math.isfinite(longitude_deg) and -180.0 <= longitude_deg <= 180.0):
        raise ValueError("latitude must lie in [-90, 90] and longitude in [-180, 180]")
    latitude_deg = round(latitude_deg, 5)
    longitude_deg = round(longitude_deg, 5)
    identifier = site_id_for(latitude_deg, longitude_deg)
    data_dir = Path(data_dir)
    site_path = data_dir / "sites" / f"{identifier}.json"
    terrain_path = data_dir / "terrain" / f"{identifier}.obj"

    with _lock_for(identifier):
        if site_path.is_file():
            site = SiteSpec.model_validate(json.loads(site_path.read_text(encoding="utf-8")))
            provenance, notes = load_provenance(data_dir, identifier)
            return BuiltSite(site=site, provenance=provenance or {}, notes=notes,
                             created=False)

        year = (today or date.today()).year - 1
        cache = data_dir / "terrain" / "tiles"
        near = load_mosaic(latitude_deg, longitude_deg, NEAR_RADIUS_M, NEAR_ZOOM, cache)
        far = load_mosaic(latitude_deg, longitude_deg, HORIZON_FARTHEST_M, FAR_ZOOM, cache)
        site_elevation = float(near.sample(latitude_deg, longitude_deg))
        if not math.isfinite(site_elevation):
            raise SiteUnavailable("the elevation tiles have no value at this point")
        if site_elevation < LOWEST_SITE_M:
            raise SiteUnavailable(f"this point lies {-site_elevation:.0f} m under water in "
                                  "the elevation model, so no shelter can stand there")
        if site_elevation > 9000.0:
            raise SiteUnavailable(f"the elevation model gives {site_elevation:.0f} m here, "
                                  "above the range a shelter can be simulated at")
        distances = horizon_distances_m()
        azimuths = horizon_azimuths_deg()
        try:
            blocked = horizon_from_mosaics(latitude_deg, longitude_deg, site_elevation, near,
                                           far, NEAR_RADIUS_M, azimuths, distances)
            mesh = terrain_obj(latitude_deg, longitude_deg, site_elevation, near)
        except WeatherError as error:
            raise SiteUnavailable(str(error)) from error

        offset_h, zone = standard_offset_h(latitude_deg, longitude_deg, year)
        place, country = reverse_place(latitude_deg, longitude_deg, fetch)
        label = (name or "").strip() or place or coordinate_label(latitude_deg,
                                                                  longitude_deg)
        weather = fetch_weather_year(latitude_deg, longitude_deg, site_elevation, year,
                                     offset_h, fetch)

        epw_name = f"{identifier}_era5_{year}.epw"
        epw_path = data_dir / "weather" / epw_name
        try:
            write_epw(epw_path, label, country, latitude_deg, longitude_deg, offset_h,
                      site_elevation, year, weather["columns"], (
                          f"One real year ({year}) of {WEATHER_DATASET}, resampled to local "
                          "standard time. Not a typical meteorological year.",
                          f"Built on demand for {coordinate_label(latitude_deg, longitude_deg)} "
                          f"at {site_elevation:.0f} m by the shelter design tool."))
            loaded = load_epw(epw_path)
            heating = float(loaded.heating_degree_days(BASE_C))
            cooling = float(loaded.cooling_degree_days(BASE_C))
            monthly = loaded.frame["dry_bulb_c"].groupby(loaded.frame.index.month).mean()
            if len(monthly) != 12:
                raise SiteUnavailable("the weather year does not cover all twelve months")
            objective = climate_objective(heating, cooling)
            horizon_citation = (
                f"36 direction horizon from {ELEVATION_SOURCE}: zoom {near.zoom} tiles within "
                f"{NEAR_RADIUS_M / 1000:g} km and zoom {far.zoom} tiles out to "
                f"{HORIZON_FARTHEST_M / 1000:g} km, {HORIZON_SAMPLES} samples per direction, "
                "with earth curvature and standard refraction")
            horizon = horizon_profile(site_elevation, None, distances, SourceRef(
                citation=horizon_citation, status=SourceStatus.DERIVED), blocked_deg=blocked)
            ground = ground_temperatures(tuple(float(value) for value in monthly), SourceRef(
                citation=("Monthly ground temperature at 0.5 m from the monthly air means "
                          "of the weather year, Kusuda and Achenbach 1965, typical soil "
                          "diffusivity 0.05 m2 per day"),
                status=SourceStatus.DERIVED,
                note="Derived, not measured. A site survey replaces it."))
            _write_text(terrain_path, mesh)
        except (WeatherError, SiteUnavailable, ValueError):
            epw_path.unlink(missing_ok=True)
            terrain_path.unlink(missing_ok=True)
            raise

        grid_lat = weather["grid"].get("latitude")
        grid_lon = weather["grid"].get("longitude")
        distance_km = (great_circle_km(latitude_deg, longitude_deg, float(grid_lat),
                                       float(grid_lon))
                       if isinstance(grid_lat, (int, float))
                       and isinstance(grid_lon, (int, float)) else None)
        site = SiteSpec(
            id=identifier, name=label, latitude_deg=latitude_deg,
            longitude_deg=longitude_deg, elevation_m=round(site_elevation, 1),
            time_zone_h=offset_h, epw_path=f"weather/{epw_name}",
            climate_objective=objective, terrain="Country", horizon=horizon,
            ground=ground,
            source=SourceRef(
                citation=(f"Site built on demand for "
                          f"{coordinate_label(latitude_deg, longitude_deg)}: elevation, "
                          f"horizon and terrain from {ELEVATION_SOURCE}, weather year {year} "
                          f"from {WEATHER_DATASET}"),
                status=SourceStatus.DERIVED,
                note=("Reanalysis weather for one real year, not a station typical "
                      "meteorological year.")))
        provenance = {
            "elevation_source": f"{ELEVATION_SOURCE}, zoom {near.zoom}",
            "horizon_source": horizon_citation,
            "horizon_directions": len(azimuths),
            "horizon_max_distance_km": round(HORIZON_FARTHEST_M / 1000.0, 1),
            "weather_provider": WEATHER_PROVIDER,
            "weather_dataset": WEATHER_DATASET,
            "weather_model": WEATHER_MODEL,
            "weather_year": year,
            "grid_latitude_deg": float(grid_lat) if isinstance(grid_lat, (int, float)) else None,
            "grid_longitude_deg": float(grid_lon) if isinstance(grid_lon, (int, float)) else None,
            "grid_distance_km": round(distance_km, 2) if distance_km is not None else None,
            "time_zone": zone,
            "utc_offset_h": offset_h,
            "heating_degree_days": round(heating, 0),
            "cooling_degree_days": round(cooling, 0),
            "annual_mean_c": round(float(monthly.mean()), 2),
            "coldest_month_mean_c": round(float(monthly.min()), 2),
            "warmest_month_mean_c": round(float(monthly.max()), 2),
            "place_name_source": "OpenStreetMap Nominatim" if place else None,
            "country_code": country,
        }
        notes = [
            (f"Weather is one real year, {year}, of the ECMWF IFS 9 km analysis for this "
             "point, downscaled to the site elevation. It is not a typical meteorological "
             "year from a ground station, so an unusually cold or warm winter is simulated "
             "as it happened."),
            WEATHER_MODEL_NOTE,
            (f"Elevation, the 36 direction horizon and the 10 km terrain square come from "
             f"{ELEVATION_SOURCE}. Near the site the tiles resolve roughly 30 to 90 m; "
             f"ridges beyond {HORIZON_FARTHEST_M / 1000:.0f} km are not seen."),
            ("Ground temperatures at 0.5 m are derived from the monthly air temperature, "
             "not measured."),
        ]
        if country is not None and country != "IN":
            notes.append(
                "Results lead with DRDO's 17 plus or minus 3 C band for a shelter in cold "
                "conditions. Outside India it is applied as it stands.")
        if zone is None:
            notes.append("No time zone covers this point, so the nautical offset from "
                         "longitude is used.")
        _write_text(site_path, json.dumps(site.model_dump(mode="json", exclude_none=True),
                                          indent=2))
        _write_text(provenance_path(data_dir, identifier),
                    json.dumps({"provenance": provenance, "notes": notes}, indent=2))
        return BuiltSite(site=site, provenance=provenance, notes=tuple(notes), created=True)


POWER_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"
POWER_FIRST_YEAR = 2011
DESIGN_NIGHT_RUN_DAYS = 7


def _last_complete_winter_end(today: date) -> date:
    year = today.year if today.month >= 7 else today.year - 1
    return date(year, 6, 30)


def fetch_temperature_history(latitude_deg: float, longitude_deg: float,
                              elevation_m: float, utc_offset_h: float, end: date,
                              fetch: Fetcher | None = None):
    import pandas as pd

    payload = _fetcher(fetch)(ARCHIVE_URL, {
        "latitude": egress.grid(latitude_deg), "longitude": egress.grid(longitude_deg),
        "elevation": f"{elevation_m:.0f}",
        "start_date": f"{WEATHER_MODEL_FIRST_YEAR}-01-01",
        "end_date": end.isoformat(), "hourly": "temperature_2m",
        "timezone": "GMT", "models": WEATHER_MODEL})
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise SiteUnavailable("the weather archive returned no temperature history")
    values = pd.to_numeric(pd.Series(hourly.get("temperature_2m", [])), errors="coerce")
    stamps = pd.to_datetime(hourly["time"]) + pd.Timedelta(hours=utc_offset_h)
    if len(values) != len(stamps):
        raise SiteUnavailable("the temperature history has mismatched times and values")
    series = pd.Series(values.to_numpy(dtype=float), index=stamps).dropna()
    if series.empty:
        raise SiteUnavailable("the temperature history holds no values")
    return series


def fetch_solar_reference(latitude_deg: float, longitude_deg: float, last_year: int,
                          fetch: Fetcher | None = None) -> dict[int, float]:
    from shelter_engine.referee import monthly_mean

    payload = _fetcher(fetch)(POWER_URL, {
        "parameters": "ALLSKY_SFC_SW_DWN", "community": "RE",
        "longitude": egress.grid(longitude_deg), "latitude": egress.grid(latitude_deg),
        "start": str(POWER_FIRST_YEAR), "end": str(last_year), "format": "JSON"})
    try:
        values = payload["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
    except (KeyError, TypeError) as error:
        raise SiteUnavailable("NASA POWER returned no surface solar series") from error
    monthly = monthly_mean(values)
    if len(monthly) != 12:
        raise SiteUnavailable("NASA POWER did not cover all twelve months")
    return monthly


def _cache(data_dir: Path, folder: str, identifier: str) -> Path:
    return Path(data_dir) / folder / f"{identifier}.json"


def load_stations(data_dir: Path) -> list[dict[str, Any]]:
    from shelter_engine.referee import load_station

    folder = Path(data_dir) / "reference"
    if not folder.is_dir():
        return []
    stations = []
    for path in sorted(folder.glob("imd_*.json")):
        try:
            stations.append(load_station(path))
        except WeatherError:
            continue
    return stations


def observed_winter_minima(station: dict[str, Any]) -> dict[int, float]:
    months = station.get("months", {})
    by_month = {int(key): value.get("lowest_minimum_by_year_c", {})
                for key, value in months.items()}
    years = {int(year) for table in by_month.values() for year in table}
    out: dict[int, float] = {}
    for winter in sorted(years | {year + 1 for year in years}):
        values = [by_month.get(12, {}).get(str(winter - 1)),
                  by_month.get(1, {}).get(str(winter)),
                  by_month.get(2, {}).get(str(winter))]
        if all(value is not None for value in values):
            out[winter] = float(min(values))
    return out


def build_design_night(site: SiteSpec, data_dir: Path, fetch: Fetcher | None = None,
                       today: date | None = None) -> dict[str, Any]:
    from shelter_engine.design_night import design_night
    from shelter_engine.referee import station_for

    data_dir = Path(data_dir)
    target = _cache(data_dir, "design_nights", site.id)
    if target.is_file():
        return json.loads(target.read_text(encoding="utf-8"))
    with _lock_for(f"design_night:{site.id}"):
        if target.is_file():
            return json.loads(target.read_text(encoding="utf-8"))
        end = _last_complete_winter_end(today or date.today())
        history = fetch_temperature_history(site.latitude_deg, site.longitude_deg,
                                            site.elevation_m, site.time_zone_h, end, fetch)
        station, distance = station_for(site.latitude_deg, site.longitude_deg,
                                        load_stations(data_dir))
        observed = observed_winter_minima(station) if station else None
        try:
            night = design_night(history, source=WEATHER_DATASET, observed=observed)
        except WeatherError as error:
            raise SiteUnavailable(str(error)) from error
        year = night.night_of.year
        weather = fetch_weather_year(site.latitude_deg, site.longitude_deg,
                                     site.elevation_m, year, site.time_zone_h, fetch)
        epw_name = f"{site.id}_design_{year}.epw"
        write_epw(data_dir / "weather" / epw_name, site.name, None, site.latitude_deg,
                  site.longitude_deg, site.time_zone_h, site.elevation_m, year,
                  weather["columns"], (
                      f"One real year ({year}) of {WEATHER_DATASET}, holding the 1 in 10 "
                      f"design night of {night.night_of.isoformat()}.",
                      f"Built for {site.name} by the shelter design tool."))
        start = max(date(year, 1, 1),
                    night.night_of - timedelta(days=DESIGN_NIGHT_RUN_DAYS - 1))
        end_day = min(date(year, 12, 31), night.night_of + timedelta(days=1))
        payload = {
            "site_id": site.id, "night_of": night.night_of.isoformat(),
            "minimum_c": round(night.minimum_c, 2),
            "return_level_c": round(night.return_level_c, 2),
            "return_period_years": night.return_period_years,
            "gumbel_location_c": round(night.gumbel_location_c, 3),
            "gumbel_scale_k": round(night.gumbel_scale_k, 3),
            "winters": [{"winter": item.winter, "night_of": item.night_of.isoformat(),
                         "minimum_c": round(item.minimum_c, 2),
                         "observed_c": item.observed_c} for item in night.winters],
            "source": night.source, "source_model": WEATHER_MODEL,
            "source_note": WEATHER_MODEL_NOTE,
            "station": station["station"] if station else None,
            "station_distance_km": round(distance, 2) if distance is not None else None,
            "epw_path": f"weather/{epw_name}",
            "run_start": start.isoformat(), "run_end": end_day.isoformat(),
            "notes": list(night.notes),
        }
        _write_text(target, json.dumps(payload, indent=2))
        return payload


def build_siting_map(site: SiteSpec, data_dir: Path, half_width_m: float,
                     step_m: float):
    from shelter_engine.schema import SitingMap
    from shelter_engine.siting import siting_map

    data_dir = Path(data_dir)
    target = data_dir / "siting" / f"{site.id}_{half_width_m:.0f}_{step_m:.0f}.json"
    if target.is_file():
        return SitingMap.model_validate_json(target.read_text(encoding="utf-8"))
    with _lock_for(f"siting:{site.id}"):
        if target.is_file():
            return SitingMap.model_validate_json(target.read_text(encoding="utf-8"))
        cache = data_dir / "terrain" / "tiles"
        near = load_mosaic(site.latitude_deg, site.longitude_deg, NEAR_RADIUS_M, NEAR_ZOOM,
                           cache)
        far = load_mosaic(site.latitude_deg, site.longitude_deg, HORIZON_FARTHEST_M,
                          FAR_ZOOM, cache)
        try:
            result = siting_map(site, near, far, NEAR_RADIUS_M, half_width_m, step_m)
        except WeatherError as error:
            raise SiteUnavailable(str(error)) from error
        _write_text(target, result.model_dump_json(indent=1))
        return result


def build_referee(site: SiteSpec, data_dir: Path, fetch: Fetcher | None = None,
                  today: date | None = None) -> dict[str, Any]:
    from shelter_engine.referee import NASA_POWER_CITATION, NASA_POWER_NAME, referee

    data_dir = Path(data_dir)
    solar_path = _cache(data_dir, "solar_reference", site.id)
    solar: dict[int, float] | None = None
    solar_note = None
    if solar_path.is_file():
        stored = json.loads(solar_path.read_text(encoding="utf-8"))
        solar = {int(key): float(value) for key, value in stored["monthly"].items()}
    else:
        last = (today or date.today()).year - 1
        try:
            solar = fetch_solar_reference(site.latitude_deg, site.longitude_deg, last,
                                          fetch)
            _write_text(solar_path, json.dumps({
                "site_id": site.id, "source": NASA_POWER_CITATION, "url": POWER_URL,
                "years": f"{POWER_FIRST_YEAR}-{last}", "unit": "kWh/m2/day",
                "monthly": solar}, indent=2))
        except SiteUnavailable as error:
            solar_note = f"The satellite solar reference could not be fetched: {error}"
    weather = load_epw(site.epw_path, site)
    report = referee(weather.frame, site.latitude_deg, site.longitude_deg,
                     load_stations(data_dir), solar, NASA_POWER_CITATION,
                     NASA_POWER_NAME)

    def checks(items):
        return [{"month": item.month, "file": round(item.file_value, 3),
                 "reference": round(item.reference_value, 3),
                 "difference": round(item.difference, 3),
                 "ratio": round(item.ratio, 4) if item.ratio else None} for item in items]

    return {
        "site_id": site.id, "weather_file": Path(site.epw_path).name,
        "station": report.station, "station_distance_km": report.station_distance_km,
        "night_minimum": checks(report.night_minimum),
        "day_maximum": checks(report.day_maximum),
        "hours_below_record_low": report.hours_below_record_low,
        "coldest_hour_c": report.coldest_hour_c, "record_low_c": report.record_low_c,
        "record_low_date": report.record_low_date,
        "solar": checks(report.solar),
        "annual_solar_file_kwh_m2": report.annual_solar_file_kwh_m2,
        "annual_solar_reference_kwh_m2": report.annual_solar_reference_kwh_m2,
        "solar_source": report.solar_source, "solar_factors": report.solar_factors,
        "flags": list(report.flags),
        "notes": list(report.notes) + ([solar_note] if solar_note else []),
    }
