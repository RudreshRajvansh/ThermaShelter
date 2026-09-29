from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("engine/src", "api/src"):
    sys.path.insert(0, str(ROOT / package))

import numpy as np

from shelter_api.sites import (ELEVATION_SOURCE, FAR_ZOOM, NEAR_RADIUS_M, NEAR_ZOOM,
                               load_mosaic)
from shelter_engine.schema import SiteSpec, SourceRef, SourceStatus
from shelter_engine.site_data import (HORIZON_FARTHEST_M, HORIZON_SAMPLES,
                                      destination_points,
                                      great_circle_km, horizon_azimuths_deg,
                                      horizon_distances_m, horizon_from_mosaics,
                                      horizon_profile, terrain_obj)

LAYER0_LATITUDE = 34.16
LAYER0_LONGITUDE = 77.58


def sample_grid(latitude_deg, longitude_deg, azimuths, distances, near, far):
    points = destination_points(latitude_deg, longitude_deg, azimuths, distances)
    close = np.broadcast_to(np.asarray(distances)[None, :] <= NEAR_RADIUS_M,
                            points.shape[:2])
    return np.where(close, near.sample(points[..., 0], points[..., 1]),
                    far.sample(points[..., 0], points[..., 1])), points


def blocking_distance_m(elevations, distances, site_elevation_m):
    from shelter_engine.site_data import EARTH_RADIUS_M, REFRACTION_COEFFICIENT

    effective = EARTH_RADIUS_M / (1.0 - REFRACTION_COEFFICIENT)
    drop = np.asarray(distances) ** 2 / (2.0 * effective)
    rise = elevations - site_elevation_m - drop[None, :]
    angles = np.degrees(np.arctan2(rise, np.asarray(distances)[None, :]))
    return np.asarray(distances)[np.argmax(angles, axis=1)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--site", default="leh")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data)
    site_path = data_dir / "sites" / f"{args.site}.json"
    record = json.loads(site_path.read_text(encoding="utf-8"))
    site = SiteSpec.model_validate(record)

    cache = data_dir / "terrain" / "tiles"
    near = load_mosaic(site.latitude_deg, site.longitude_deg, NEAR_RADIUS_M, NEAR_ZOOM,
                       cache)
    far = load_mosaic(site.latitude_deg, site.longitude_deg, HORIZON_FARTHEST_M, FAR_ZOOM,
                      cache)

    dem_elevation = float(near.sample(site.latitude_deg, site.longitude_deg))
    if not math.isfinite(dem_elevation):
        raise SystemExit("the elevation tiles have no value at the site")

    azimuths = horizon_azimuths_deg()
    distances = horizon_distances_m()
    elevations, _ = sample_grid(site.latitude_deg, site.longitude_deg, azimuths, distances,
                                near, far)
    blocked = horizon_from_mosaics(site.latitude_deg, site.longitude_deg, dem_elevation,
                                   near, far, NEAR_RADIUS_M, azimuths, distances)
    reach = blocking_distance_m(elevations, distances, dem_elevation)

    layer0_near = load_mosaic(LAYER0_LATITUDE, LAYER0_LONGITUDE, NEAR_RADIUS_M, NEAR_ZOOM,
                              cache)
    layer0_far = load_mosaic(LAYER0_LATITUDE, LAYER0_LONGITUDE, HORIZON_FARTHEST_M,
                             FAR_ZOOM, cache)
    layer0_elevation = float(layer0_near.sample(LAYER0_LATITUDE, LAYER0_LONGITUDE))
    layer0_blocked = horizon_from_mosaics(LAYER0_LATITUDE, LAYER0_LONGITUDE,
                                          layer0_elevation, layer0_near, layer0_far,
                                          NEAR_RADIUS_M, azimuths, distances)

    stored = np.asarray(site.horizon.blocked_altitude_deg, dtype=float)
    print(f"site {site.latitude_deg:.5f} N {site.longitude_deg:.5f} E, "
          f"published elevation {site.elevation_m:.1f} m, tiles give {dem_elevation:.1f} m")
    print(f"layer 0 profile point {LAYER0_LATITUDE} N {LAYER0_LONGITUDE} E, "
          f"tiles give {layer0_elevation:.1f} m")
    print()
    print(f"{'az':>5} {'stored':>8} {'site':>8} {'diff':>7} {'l0 point':>9} "
          f"{'l0 diff':>8} {'reach km':>9}")
    for index, azimuth in enumerate(azimuths):
        print(f"{azimuth:5.0f} {stored[index]:8.3f} {blocked[index]:8.3f} "
              f"{blocked[index] - stored[index]:+7.3f} {layer0_blocked[index]:9.3f} "
              f"{layer0_blocked[index] - stored[index]:+8.3f} {reach[index] / 1000:9.2f}")
    print()
    print(f"against the stored table: site point mean |diff| "
          f"{np.abs(blocked - stored).mean():.2f} deg, worst "
          f"{np.abs(blocked - stored).max():.2f} deg; layer 0 point mean |diff| "
          f"{np.abs(layer0_blocked - stored).mean():.2f} deg, worst "
          f"{np.abs(layer0_blocked - stored).max():.2f} deg")
    print(f"mean blocked altitude: stored {stored.mean():.2f}, site {blocked.mean():.2f}, "
          f"layer 0 point {layer0_blocked.mean():.2f} deg")

    if args.dry_run:
        return 0

    offset_km = great_circle_km(site.latitude_deg, site.longitude_deg, LAYER0_LATITUDE,
                                LAYER0_LONGITUDE)
    citation = (f"36 direction horizon computed at the site coordinates "
                f"{site.latitude_deg:.5f} N {site.longitude_deg:.5f} E from "
                f"{ELEVATION_SOURCE}: zoom {near.zoom} tiles within "
                f"{NEAR_RADIUS_M / 1000:g} km and zoom {far.zoom} tiles out to "
                f"{HORIZON_FARTHEST_M / 1000:g} km, {HORIZON_SAMPLES} samples per "
                f"direction, with earth curvature and standard refraction")
    note = (f"Replaces the Layer 0 horizon_36dir.csv table, which its own source.txt "
            f"records as computed at {LAYER0_LATITUDE} N {LAYER0_LONGITUDE} E, about "
            f"{offset_km:.1f} km from this site. That table blocked the sun to "
            f"{stored.mean():.1f} degrees on average against {blocked.mean():.1f} here, "
            f"and by up to {np.abs(blocked - stored).max():.1f} degrees in one direction. "
            f"The elevation model reads {dem_elevation:.0f} m at the site against the "
            f"{site.elevation_m:.0f} m the site record carries from Layer 0; the horizon "
            f"angles are computed from the model's own value so ground and skyline agree.")
    profile = horizon_profile(dem_elevation, None, distances,
                              SourceRef(citation=citation, status=SourceStatus.DERIVED,
                                        note=note), blocked_deg=blocked)
    record["horizon"] = json.loads(profile.model_dump_json())
    site_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    mesh_path = data_dir / "terrain" / f"{site.id}.obj"
    mesh_path.write_text(terrain_obj(site.latitude_deg, site.longitude_deg, dem_elevation,
                                     near), encoding="utf-8")
    print(f"\nwrote {site_path} and {mesh_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
