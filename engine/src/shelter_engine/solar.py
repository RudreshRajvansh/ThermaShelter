from __future__ import annotations

import math
from datetime import date
from typing import Sequence

from .schema import (HorizonProfile, SiteSpec, SolarNoon, SunPath, SunPosition,
                     SourceRef, SourceStatus, TerrainMesh)

SOLAR_SOURCE = SourceRef(
    citation="Solar declination from the Spencer Fourier series and the equation of "
             "time from Duffie and Beckman, Solar Engineering of Thermal Processes, "
             "chapter on solar geometry",
    status=SourceStatus.VERIFIED)
HORIZON_SOURCE = SourceRef(
    citation="Terrain skyline sampled by Layer 0 from elevation data around the site "
             "and stored as blocked altitude against compass direction",
    status=SourceStatus.PENDING)

TERRAIN_INNER_RADIUS_M = 45.0
TERRAIN_OUTER_RADIUS_M = 320.0
TERRAIN_SKIRT_M = 12.0


def day_of_year(day: date) -> int:
    return day.timetuple().tm_yday


def declination_deg(day: date) -> float:
    angle = 2.0 * math.pi * (day_of_year(day) - 1) / 365.0
    return math.degrees(
        0.006918
        - 0.399912 * math.cos(angle)
        + 0.070257 * math.sin(angle)
        - 0.006758 * math.cos(2 * angle)
        + 0.000907 * math.sin(2 * angle)
        - 0.002697 * math.cos(3 * angle)
        + 0.001480 * math.sin(3 * angle))


def equation_of_time_minutes(day: date) -> float:
    angle = 2.0 * math.pi * (day_of_year(day) - 1) / 365.0
    return 229.2 * (
        0.000075
        + 0.001868 * math.cos(angle)
        - 0.032077 * math.sin(angle)
        - 0.014615 * math.cos(2 * angle)
        - 0.040849 * math.sin(2 * angle))


def solar_time_hours(clock_hour: float, longitude_deg: float,
                     time_zone_h: float, day: date) -> float:
    standard_meridian = 15.0 * time_zone_h
    correction = 4.0 * (longitude_deg - standard_meridian) + equation_of_time_minutes(day)
    return clock_hour + correction / 60.0


def sun_position(latitude_deg: float, longitude_deg: float, time_zone_h: float,
                 day: date, clock_hour: float) -> tuple[float, float]:
    latitude = math.radians(latitude_deg)
    declination = math.radians(declination_deg(day))
    hour_angle = math.radians(
        15.0 * (solar_time_hours(clock_hour, longitude_deg, time_zone_h, day) - 12.0))
    sine = (math.sin(latitude) * math.sin(declination)
            + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle))
    altitude = math.asin(max(-1.0, min(1.0, sine)))
    numerator = math.sin(altitude) * math.sin(latitude) - math.sin(declination)
    denominator = math.cos(altitude) * math.cos(latitude)
    if abs(denominator) < 1e-9:
        return math.degrees(altitude), 180.0
    from_south = math.degrees(math.acos(max(-1.0, min(1.0, numerator / denominator))))
    azimuth = 180.0 + from_south if hour_angle > 0.0 else 180.0 - from_south
    return math.degrees(altitude), azimuth % 360.0


def horizon_altitude_deg(horizon: HorizonProfile | None, azimuth_deg: float) -> float:
    if horizon is None:
        return 0.0
    pairs = sorted(zip(horizon.azimuths_deg, horizon.blocked_altitude_deg))
    azimuth = azimuth_deg % 360.0
    for index, (sample_azimuth, _) in enumerate(pairs):
        if sample_azimuth >= azimuth:
            low_azimuth, low_value = pairs[index - 1]
            high_azimuth, high_value = pairs[index]
            if index == 0:
                low_azimuth, low_value = pairs[-1][0] - 360.0, pairs[-1][1]
            break
    else:
        low_azimuth, low_value = pairs[-1]
        high_azimuth, high_value = pairs[0][0] + 360.0, pairs[0][1]
    span = high_azimuth - low_azimuth
    if span <= 0.0:
        return low_value
    weight = (azimuth - low_azimuth) / span
    return low_value + (high_value - low_value) * weight


def sun_path(site: SiteSpec, day: date, step_minutes: int = 20) -> SunPath:
    if 1440 % step_minutes != 0:
        raise ValueError("step_minutes must divide 1440")
    positions: list[SunPosition] = []
    steps = 1440 // step_minutes
    for index in range(steps + 1):
        clock_hour = index * step_minutes / 60.0
        altitude, azimuth = sun_position(site.latitude_deg, site.longitude_deg,
                                         site.time_zone_h, day, clock_hour % 24.0)
        blocked = horizon_altitude_deg(site.horizon, azimuth)
        positions.append(SunPosition(
            hour=round(clock_hour, 4),
            altitude_deg=round(altitude, 4),
            azimuth_deg=round(azimuth, 4),
            horizon_altitude_deg=round(blocked, 4),
            above_horizon=altitude > 0.0,
            visible=altitude > blocked))

    above = [item for item in positions if item.above_horizon and item.hour < 24.0]
    visible = [item for item in positions if item.visible and item.hour < 24.0]
    peak = max(positions, key=lambda item: item.altitude_deg)
    step_hours = step_minutes / 60.0
    daylight = len(above) * step_hours
    visible_daylight = len(visible) * step_hours
    return SunPath(
        site_id=site.id,
        date=day.isoformat(),
        latitude_deg=site.latitude_deg,
        longitude_deg=site.longitude_deg,
        declination_deg=round(declination_deg(day), 4),
        equation_of_time_minutes=round(equation_of_time_minutes(day), 3),
        positions=tuple(positions),
        astronomical_sunrise_hour=(round(above[0].hour, 3) if above else None),
        astronomical_sunset_hour=(round(above[-1].hour, 3) if above else None),
        visible_sunrise_hour=(round(visible[0].hour, 3) if visible else None),
        visible_sunset_hour=(round(visible[-1].hour, 3) if visible else None),
        noon=SolarNoon(hour=round(peak.hour, 3),
                       altitude_deg=round(peak.altitude_deg, 3),
                       azimuth_deg=round(peak.azimuth_deg, 3)),
        has_horizon=site.horizon is not None,
        daylight_hours=round(daylight, 3),
        visible_daylight_hours=round(visible_daylight, 3),
        daylight_lost_to_terrain_h=round(max(0.0, daylight - visible_daylight), 3),
        source=SOLAR_SOURCE)


def daylight_lost_to_terrain_h(path: SunPath) -> float:
    return path.daylight_lost_to_terrain_h


def terrain_mesh(site: SiteSpec, inner_radius_m: float = TERRAIN_INNER_RADIUS_M,
                 outer_radius_m: float = TERRAIN_OUTER_RADIUS_M) -> TerrainMesh:
    horizon = site.horizon
    if horizon is None:
        return TerrainMesh(site_id=site.id, positions=(), indices=(),
                           inner_radius_m=inner_radius_m,
                           outer_radius_m=outer_radius_m, ridge_max_m=0.0,
                           derived_from_horizon=False, source=HORIZON_SOURCE,
                           note=("No terrain horizon profile is attached to this site, "
                                 "so there is no skyline to draw. The simulation is "
                                 "assuming a flat horizon in every direction."))

    samples = sorted(zip(horizon.azimuths_deg, horizon.blocked_altitude_deg))
    azimuths = [item[0] for item in samples] + [samples[0][0] + 360.0]
    altitudes = [item[1] for item in samples] + [samples[0][1]]

    positions: list[float] = []
    indices: list[int] = []
    ridge_max = 0.0

    def point(azimuth_deg: float, radius: float, height: float) -> int:
        angle = math.radians(azimuth_deg)
        index = len(positions) // 3
        positions.extend((radius * math.sin(angle), radius * math.cos(angle), height))
        return index

    for step in range(len(azimuths) - 1):
        left_azimuth, right_azimuth = azimuths[step], azimuths[step + 1]
        left_height = outer_radius_m * math.tan(math.radians(altitudes[step]))
        right_height = outer_radius_m * math.tan(math.radians(altitudes[step + 1]))
        ridge_max = max(ridge_max, left_height, right_height)

        inner_left = point(left_azimuth, inner_radius_m, 0.0)
        inner_right = point(right_azimuth, inner_radius_m, 0.0)
        ridge_left = point(left_azimuth, outer_radius_m, left_height)
        ridge_right = point(right_azimuth, outer_radius_m, right_height)
        skirt_left = point(left_azimuth, outer_radius_m, left_height - TERRAIN_SKIRT_M)
        skirt_right = point(right_azimuth, outer_radius_m, right_height - TERRAIN_SKIRT_M)

        indices.extend((inner_left, inner_right, ridge_right))
        indices.extend((inner_left, ridge_right, ridge_left))
        indices.extend((ridge_left, ridge_right, skirt_right))
        indices.extend((ridge_left, skirt_right, skirt_left))

    return TerrainMesh(
        site_id=site.id, positions=tuple(round(value, 3) for value in positions),
        indices=tuple(indices), inner_radius_m=inner_radius_m,
        outer_radius_m=outer_radius_m, ridge_max_m=round(ridge_max, 2),
        derived_from_horizon=True, source=horizon.source,
        note=("A skyline drawn from the terrain horizon table, one ridge height per "
              "sampled compass direction. It is the same table the simulation uses to "
              "shade the building, so what blocks the sun on screen is what blocked it "
              "in the run."))


def blocked_hours(site: SiteSpec, day: date,
                  step_minutes: int = 20) -> Sequence[SunPosition]:
    path = sun_path(site, day, step_minutes)
    return tuple(item for item in path.positions
                 if item.above_horizon and not item.visible)
