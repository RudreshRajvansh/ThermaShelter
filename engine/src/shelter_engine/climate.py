from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .schema import (ClimateMonth, ClimateObjective, ClimateSummary,
                     ClimateWindow, SiteSpec, SourceRef, SourceStatus)
from .solar import sun_path
from .weather import (WeatherFile, air_density_kg_m3, standard_pressure_pa)

WH_TO_KWH = 1.0 / 1000.0
BASE_C = 18.0

EPW_SOURCE = SourceRef(
    citation="Hourly values read straight from the EnergyPlus weather file supplied by "
             "Layer 0; nothing here is modelled",
    status=SourceStatus.MEASURED if False else SourceStatus.DERIVED)


def _monthly(frame: pd.DataFrame, site: SiteSpec) -> tuple[ClimateMonth, ...]:
    months: list[ClimateMonth] = []
    grouped = frame.groupby(frame.index.month)
    daily_mean = frame["dry_bulb_c"].resample("1D").mean()
    daily_swing = (frame["dry_bulb_c"].resample("1D").max()
                   - frame["dry_bulb_c"].resample("1D").min())
    for month in range(1, 13):
        if month not in grouped.groups:
            continue
        block = grouped.get_group(month)
        days = daily_mean[daily_mean.index.month == month]
        swings = daily_swing[daily_swing.index.month == month]
        ground = None
        if site.ground is not None:
            ground = site.ground.monthly_c[month - 1]
        months.append(ClimateMonth(
            month=month,
            mean_dry_bulb_c=round(float(block["dry_bulb_c"].mean()), 2),
            min_dry_bulb_c=round(float(block["dry_bulb_c"].min()), 2),
            max_dry_bulb_c=round(float(block["dry_bulb_c"].max()), 2),
            mean_daily_swing_k=round(float(swings.mean()), 2),
            global_horizontal_kwh_m2=round(
                float(block["global_horizontal_wh_m2"].sum()) * WH_TO_KWH, 1),
            direct_normal_kwh_m2=round(
                float(block["direct_normal_wh_m2"].sum()) * WH_TO_KWH, 1),
            mean_wind_m_s=round(float(block["wind_speed_m_s"].mean()), 2),
            mean_relative_humidity_pct=round(
                float(block["relative_humidity_pct"].mean()), 1),
            heating_degree_days=round(
                float(np.clip(BASE_C - days.to_numpy(dtype=float), 0.0, None).sum()), 1),
            cooling_degree_days=round(
                float(np.clip(days.to_numpy(dtype=float) - BASE_C, 0.0, None).sum()), 1),
            ground_temperature_c=None if ground is None else round(float(ground), 2)))
    return tuple(months)


def _window(frame: pd.DataFrame, centre: date, days: int) -> ClimateWindow:
    half = max(1, days // 2)
    start = pd.Timestamp(centre) - pd.Timedelta(half, unit="D")
    end = pd.Timestamp(centre) + pd.Timedelta(days - half, unit="D")
    block = frame.loc[(frame.index >= start) & (frame.index < end)]
    if block.empty:
        block = frame.iloc[: days * 24]

    def series(column: str, scale: float = 1.0) -> tuple[float, ...]:
        return tuple(round(float(value) * scale, 2)
                     for value in block[column].to_numpy(dtype=float))

    return ClimateWindow(
        label=f"{days} days around {centre.isoformat()}",
        timestamps=tuple(stamp.isoformat() for stamp in block.index),
        dry_bulb_c=series("dry_bulb_c"),
        relative_humidity_pct=series("relative_humidity_pct"),
        global_horizontal_w_m2=series("global_horizontal_wh_m2"),
        direct_normal_w_m2=series("direct_normal_wh_m2"),
        diffuse_horizontal_w_m2=series("diffuse_horizontal_wh_m2"),
        wind_speed_m_s=series("wind_speed_m_s"))


def summarise(site: SiteSpec, weather: WeatherFile,
              window_days: int = 7) -> ClimateSummary:
    frame = weather.frame
    dry_bulb = frame["dry_bulb_c"]
    cooling = site.climate_objective is ClimateObjective.COOLING_DOMINATED
    design_day = weather.hottest_day() if cooling else weather.coldest_day()

    observed_pressure = float(frame["pressure_pa"].mean())
    expected_pressure = standard_pressure_pa(site.elevation_m)
    density = air_density_kg_m3(observed_pressure, float(dry_bulb.mean()))
    sea_level_density = air_density_kg_m3(101325.0, float(dry_bulb.mean()))

    path = sun_path(site, design_day, step_minutes=15)
    design_frame = frame.loc[frame.index.date == design_day]

    return ClimateSummary(
        site_id=site.id,
        name=site.name,
        latitude_deg=site.latitude_deg,
        longitude_deg=site.longitude_deg,
        elevation_m=site.elevation_m,
        time_zone_h=site.time_zone_h,
        climate_objective=site.climate_objective,
        weather_file=weather.path.name,
        weather_station=weather.header.city,
        weather_source=weather.header.source,
        hours=int(len(frame)),
        annual_mean_c=round(float(dry_bulb.mean()), 2),
        annual_min_c=round(float(dry_bulb.min()), 2),
        annual_max_c=round(float(dry_bulb.max()), 2),
        heating_degree_days=round(weather.heating_degree_days(BASE_C), 0),
        cooling_degree_days=round(weather.cooling_degree_days(BASE_C), 0),
        annual_global_horizontal_kwh_m2=round(
            float(frame["global_horizontal_wh_m2"].sum()) * WH_TO_KWH, 0),
        annual_direct_normal_kwh_m2=round(
            float(frame["direct_normal_wh_m2"].sum()) * WH_TO_KWH, 0),
        mean_wind_m_s=round(float(frame["wind_speed_m_s"].mean()), 2),
        design_day=design_day.isoformat(),
        design_day_min_c=round(float(design_frame["dry_bulb_c"].min()), 2),
        design_day_max_c=round(float(design_frame["dry_bulb_c"].max()), 2),
        mean_station_pressure_pa=round(observed_pressure, 0),
        expected_station_pressure_pa=round(expected_pressure, 0),
        air_density_kg_m3=round(density, 3),
        sea_level_air_density_kg_m3=round(sea_level_density, 3),
        months=_monthly(frame, site),
        design_day_window=_window(frame, design_day, window_days),
        sun_path=path,
        has_horizon=site.horizon is not None,
        has_ground_temperatures=site.ground is not None,
        horizon_azimuths_deg=(tuple(site.horizon.azimuths_deg)
                              if site.horizon else ()),
        horizon_blocked_deg=(tuple(site.horizon.blocked_altitude_deg)
                             if site.horizon else ()),
        warnings=tuple(weather.warnings),
        assumptions=tuple(weather.assumptions),
        source=site.source or EPW_SOURCE)
