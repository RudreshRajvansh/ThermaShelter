from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("engine/src", "api/src"):
    sys.path.insert(0, str(ROOT / package))

from shelter_api.sites import SiteUnavailable, build_site  # noqa: E402
from shelter_engine.site_data import great_circle_km  # noqa: E402

LADAKH = [
    ("Diskit, Nubra", 34.54540, 77.55600),
    ("Partapur", 34.85000, 77.25000),
    ("Khardung village", 34.28000, 77.60000),
    ("Tangtse", 34.03300, 78.16700),
    ("Spangmik, Pangong", 33.79000, 78.64000),
    ("Chushul", 33.58000, 78.64000),
    ("Nyoma", 33.18000, 78.65000),
    ("Hanle", 32.78000, 78.96000),
    ("Drass", 34.43000, 75.75000),
    ("Padum, Zanskar", 33.47000, 76.88000),
    ("Upshi", 33.82000, 77.81000),
    ("Tangtse Durbuk", 34.15000, 78.05000),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a spread of sites ahead of time so the tool has real "
                    "neighbours to fall back on when a point cannot be built.")
    parser.add_argument("--data", default=str(ROOT / "data"))
    args = parser.parse_args()

    data = Path(args.data)
    built = 0
    for name, latitude, longitude in LADAKH:
        started = time.perf_counter()
        try:
            site = build_site(latitude, longitude, name, data)
        except (SiteUnavailable, ValueError) as error:
            print(f"  ! {name:22s} {error}")
            continue
        elapsed = time.perf_counter() - started
        state = "already there" if not site.created else f"built in {elapsed:.0f} s"
        print(f"  {site.site.name:34s} {site.site.elevation_m:6.0f} m  {state}")
        built += 1

    spread = [(a[0], b[0], great_circle_km(a[1], a[2], b[1], b[2]))
              for index, a in enumerate(LADAKH) for b in LADAKH[index + 1:]]
    nearest = {}
    for first, second, distance in spread:
        nearest[first] = min(nearest.get(first, 1e9), distance)
        nearest[second] = min(nearest.get(second, 1e9), distance)
    if nearest:
        print(f"\n{built} of {len(LADAKH)} sites available, "
              f"nearest neighbour {min(nearest.values()):.0f} to "
              f"{max(nearest.values()):.0f} km apart")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
