from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

from .errors import ResultsUnavailable
from .geometry import GeometryModel, ROOM
from .results import SimulationOutput
from .schema import (AirQualityMetrics, AirflowSummary, CondensationMetrics, DesignSpec, EnergyMetrics,
                     EngineWarning, Facade, FuelModel, HeatBalanceBreakdown,
                     Provenance, Severity, SourceRef, SourceStatus, SurfaceKind,
                     ThermalBridge, ZoneAirflow, ZoneCoupling)
from .weather import dew_point_c

JOULES_TO_KWH = 1.0 / 3_600_000.0
WATT_HOURS_TO_KWH = 1.0 / 1000.0

OPERATIVE_CANDIDATES = ("Zone Operative Temperature",)
AIR_TEMPERATURE_CANDIDATES = ("Zone Mean Air Temperature",)
RADIANT_CANDIDATES = ("Zone Mean Radiant Temperature",)
HUMIDITY_CANDIDATES = ("Zone Air Relative Humidity",)
OUTDOOR_CANDIDATES = ("Site Outdoor Air Drybulb Temperature",)
HEATING_CANDIDATES = ("Zone Ideal Loads Supply Air Total Heating Energy",)
COOLING_CANDIDATES = ("Zone Ideal Loads Supply Air Total Cooling Energy",)
HEATING_RATE_CANDIDATES = ("Zone Ideal Loads Supply Air Sensible Heating Rate",)
CONDUCTION_CANDIDATES = ("Surface Inside Face Conduction Heat Transfer Rate",)
INCIDENT_SOLAR_CANDIDATES = (
    "Surface Outside Face Incident Solar Radiation Rate per Area",)
WINDOW_LOSS_CANDIDATES = ("Surface Window Heat Loss Energy",)
WINDOW_GAIN_CANDIDATES = ("Surface Window Heat Gain Energy",)
ZONE_SOLAR_CANDIDATES = (
    "Zone Windows Total Transmitted Solar Radiation Energy",
    "Enclosure Windows Total Transmitted Solar Radiation Energy",
)


@dataclass(frozen=True)
class ZoneSeries:
    operative_c: pd.Series
    air_c: pd.Series
    radiant_c: pd.Series | None
    relative_humidity_pct: pd.Series | None
    outdoor_c: pd.Series

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.operative_c.index


def _hourly(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.resample("1h").mean().dropna()


def zone_series(output: SimulationOutput, zone: str = ROOM,
                warnings: list[EngineWarning] | None = None) -> ZoneSeries:
    air = output.first_available(AIR_TEMPERATURE_CANDIDATES, key=zone)
    if air is None:
        raise ResultsUnavailable(
            f"no zone air temperature was reported for {zone}; available keys are "
            f"{output.keys_for(AIR_TEMPERATURE_CANDIDATES[0])}")
    air_series = _hourly(air[1])

    operative = output.first_available(OPERATIVE_CANDIDATES, key=zone)
    radiant = output.first_available(RADIANT_CANDIDATES, key=zone)
    radiant_series = _hourly(radiant[1]) if radiant is not None else None
    if operative is not None:
        operative_series = _hourly(operative[1])
    elif radiant_series is not None:
        operative_series = 0.5 * (air_series + radiant_series.reindex(air_series.index))
        operative_series = operative_series.dropna()
        if warnings is not None:
            warnings.append(EngineWarning(
                code="operative_temperature_derived", severity=Severity.WARNING,
                origin="engine",
                message=("EnergyPlus did not report Zone Operative Temperature, so it "
                         "was taken as the mean of air and mean radiant temperature. "
                         "That is the same definition EnergyPlus uses at normal indoor "
                         "air speeds, but the value is derived rather than reported.")))
    else:
        raise ResultsUnavailable(
            f"neither operative nor mean radiant temperature was reported for {zone}")

    humidity = output.first_available(HUMIDITY_CANDIDATES, key=zone)
    outdoor = output.first_available(OUTDOOR_CANDIDATES)
    if outdoor is None:
        raise ResultsUnavailable("no outdoor dry bulb temperature was reported")
    return ZoneSeries(
        operative_c=operative_series, air_c=air_series, radiant_c=radiant_series,
        relative_humidity_pct=_hourly(humidity[1]) if humidity is not None else None,
        outdoor_c=_hourly(outdoor[1]))


def _sum_kwh(output: SimulationOutput, name: str, keys: Sequence[str]) -> float:
    if not keys:
        return 0.0
    wide = output.wide(name)
    if wide.empty:
        return 0.0
    columns = [key for key in keys if key in wide.columns]
    if not columns:
        return 0.0
    return float(wide[columns].sum().sum()) * JOULES_TO_KWH


def _conduction_kwh(output: SimulationOutput, keys: Sequence[str]) -> float:
    if not keys:
        return 0.0
    wide = output.wide(CONDUCTION_CANDIDATES[0])
    if wide.empty:
        return 0.0
    columns = [key for key in keys if key in wide.columns]
    if not columns:
        return 0.0
    return float(wide[columns].sum().sum()) * WATT_HOURS_TO_KWH


def thermal_bridge_length_m(bridge: ThermalBridge, model: GeometryModel,
                            design: DesignSpec) -> float:
    if bridge.length_from == "explicit":
        return bridge.length_m
    plan = design.plan
    if bridge.length_from in ("wall_floor_perimeter", "wall_roof_perimeter"):
        return 2.0 * (plan.width_m + plan.depth_m)
    if bridge.length_from == "wall_corners":
        return 4.0 * plan.ceiling_height_m
    if bridge.length_from == "window_perimeter":
        total = 0.0
        for window in model.windows:
            if window.outside_boundary != "Outdoors":
                continue
            area = window.area_m2
            total += 2.0 * (math.sqrt(area * 1.6) + math.sqrt(area / 1.6))
        return total
    return bridge.length_m


def thermal_bridge_kwh(bridges: Sequence[ThermalBridge], model: GeometryModel,
                       design: DesignSpec, indoor_c: pd.Series,
                       outdoor_c: pd.Series) -> float:
    if not bridges:
        return 0.0
    common = indoor_c.index.intersection(outdoor_c.index)
    if len(common) == 0:
        return 0.0
    difference = (indoor_c.loc[common] - outdoor_c.loc[common]).to_numpy(dtype=float)
    total_psi_length = sum(bridge.psi_w_mk
                           * thermal_bridge_length_m(bridge, model, design)
                           for bridge in bridges)
    return float(np.sum(np.clip(difference, 0.0, None))
                 * total_psi_length) * WATT_HOURS_TO_KWH


def heat_balance(output: SimulationOutput, model: GeometryModel,
                 design: DesignSpec, series: ZoneSeries,
                 bridges: Sequence[ThermalBridge] = (),
                 zone: str = ROOM) -> HeatBalanceBreakdown:
    owned = [polygon for polygon in model.polygons if polygon.zone == zone]
    walls = [p.name for p in owned if p.kind is SurfaceKind.WALL
             and p.outside_boundary == "Outdoors"]
    roofs = [p.name for p in owned if p.kind is SurfaceKind.ROOF]
    floors = [p.name for p in owned if p.kind is SurfaceKind.FLOOR]
    partitions = [p.name for p in owned if p.kind is SurfaceKind.PARTITION
                  or (p.kind is SurfaceKind.WALL and p.outside_boundary == "Surface")]
    windows = [p.name for p in owned if p.kind is SurfaceKind.WINDOW]

    window_loss = _sum_kwh(output, WINDOW_LOSS_CANDIDATES[0], windows)
    window_gain = _sum_kwh(output, WINDOW_GAIN_CANDIDATES[0], windows)
    window_conduction = window_gain - window_loss
    if window_loss == 0.0 and window_gain == 0.0:
        window_conduction = _conduction_kwh(output, windows)

    infiltration = (_sum_kwh(output, "Zone Infiltration Sensible Heat Gain Energy", [zone])
                    - _sum_kwh(output, "Zone Infiltration Sensible Heat Loss Energy",
                               [zone]))
    ventilation = (_sum_kwh(output, "Zone Ventilation Sensible Heat Gain Energy", [zone])
                   - _sum_kwh(output, "Zone Ventilation Sensible Heat Loss Energy",
                              [zone]))
    interzone_air = (_sum_kwh(output, "Zone Mixing Sensible Heat Gain Energy", [zone])
                     - _sum_kwh(output, "Zone Mixing Sensible Heat Loss Energy", [zone]))
    interzone = _conduction_kwh(output, partitions) + interzone_air

    bridge_kwh = thermal_bridge_kwh(bridges, model, design, series.air_c,
                                    series.outdoor_c)

    return HeatBalanceBreakdown(
        walls_kwh=_conduction_kwh(output, walls),
        roof_kwh=_conduction_kwh(output, roofs),
        floor_kwh=_conduction_kwh(output, floors),
        windows_conduction_kwh=window_conduction,
        windows_solar_gain_kwh=sum(
            _sum_kwh(output, name, [zone]) for name in ZONE_SOLAR_CANDIDATES),
        infiltration_kwh=infiltration,
        ventilation_kwh=ventilation,
        interzone_kwh=interzone,
        internal_gains_kwh=_sum_kwh(output, "Zone Total Internal Total Heating Energy",
                                    [zone]),
        thermal_bridge_kwh=bridge_kwh,
        thermal_bridge_provenance=(bridges[0].provenance if bridges
                                   else Provenance.DERIVED),
    )


def condensation(output: SimulationOutput, model: GeometryModel,
                 series: ZoneSeries, zone: str = ROOM) -> CondensationMetrics:
    if series.relative_humidity_pct is None:
        return CondensationMetrics()
    windows = [polygon.name for polygon in model.polygons
               if polygon.zone == zone and polygon.kind is SurfaceKind.WINDOW]
    if not windows:
        return CondensationMetrics()
    inside = output.wide("Surface Inside Face Temperature")
    if inside.empty:
        return CondensationMetrics()
    columns = [name for name in windows if name in inside.columns]
    if not columns:
        return CondensationMetrics()
    inside = inside[columns].resample("1h").mean()
    common = inside.index.intersection(series.air_c.index)
    common = common.intersection(series.relative_humidity_pct.index)
    if len(common) == 0:
        return CondensationMetrics()
    inside = inside.loc[common]
    dew = pd.Series(
        dew_point_c(series.air_c.loc[common].to_numpy(dtype=float),
                    series.relative_humidity_pct.loc[common].to_numpy(dtype=float)),
        index=common)
    margins = inside.sub(dew, axis=0)
    below = margins < 0.0
    hours = float(below.to_numpy().any(axis=1).sum())
    worst_surface = None
    worst_margin = None
    if bool(below.to_numpy().any()):
        minima = margins.min()
        worst_surface = str(minima.idxmin())
        worst_margin = float(minima.min())
    return CondensationMetrics(
        hours=hours, worst_surface=worst_surface, worst_margin_k=worst_margin,
        minimum_inner_glazing_c=float(inside.to_numpy().min()),
        peak_indoor_rh_percent=float(series.relative_humidity_pct.max()))


def energy_metrics(output: SimulationOutput, zones: Sequence[str],
                   fuel: FuelModel | None) -> EnergyMetrics:
    heating = 0.0
    cooling = 0.0
    peak = None
    found = False
    for name in HEATING_CANDIDATES:
        wide = output.wide(name)
        if wide.empty:
            continue
        heating += float(wide.sum().sum()) * JOULES_TO_KWH
        found = True
    for name in COOLING_CANDIDATES:
        wide = output.wide(name)
        if wide.empty:
            continue
        cooling += float(wide.sum().sum()) * JOULES_TO_KWH
    for name in HEATING_RATE_CANDIDATES:
        wide = output.wide(name)
        if wide.empty:
            continue
        peak = float(wide.sum(axis=1).max())
    if not found:
        return EnergyMetrics()
    if fuel is None:
        return EnergyMetrics(heating_demand_kwh=heating, cooling_demand_kwh=cooling,
                             peak_heating_w=peak)
    return EnergyMetrics(
        heating_demand_kwh=heating, cooling_demand_kwh=cooling, peak_heating_w=peak,
        fuel=fuel.fuel, fuel_units=fuel.units_for(heating), fuel_unit_name=fuel.unit,
        fuel_cost_inr=fuel.cost_inr_for(heating))


def surface_frames(output: SimulationOutput, names: Sequence[str]
                   ) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for variable in ("Surface Inside Face Temperature",
                     "Surface Outside Face Temperature",
                     CONDUCTION_CANDIDATES[0], INCIDENT_SOLAR_CANDIDATES[0]):
        wide = output.wide(variable)
        if wide.empty:
            continue
        columns = [name for name in names if name in wide.columns]
        if not columns:
            continue
        frames[variable] = wide[columns].resample("1h").mean()
    return frames


def solar_gain_by_facade(output: SimulationOutput,
                         model: GeometryModel) -> dict[Facade, float]:
    wide = output.wide(INCIDENT_SOLAR_CANDIDATES[0])
    if wide.empty:
        return {}
    totals: dict[Facade, float] = {}
    for polygon in model.polygons:
        if polygon.facade is None or polygon.outside_boundary != "Outdoors":
            continue
        if polygon.name not in wide.columns:
            continue
        energy = float(wide[polygon.name].sum()) * polygon.area_m2 * WATT_HOURS_TO_KWH
        totals[polygon.facade] = totals.get(polygon.facade, 0.0) + energy
    return totals


def airflow_summary(output, model) -> AirflowSummary | None:
    volumes = {zone.name.upper(): zone.volume_m3 for zone in model.zones}
    if not volumes:
        return None

    def mean_peak(name: str, key: str) -> tuple[float, float]:
        series = output.series(name, key, required=False)
        if series is None:
            return 0.0, 0.0
        clean = series.dropna()
        if clean.empty:
            return 0.0, 0.0
        return float(clean.mean()), float(clean.max())

    zones: list[ZoneAirflow] = []
    for name, volume in sorted(volumes.items()):
        ach_mean, ach_peak = mean_peak("Zone Infiltration Air Change Rate", name)
        flow_mean, _ = mean_peak(
            "Zone Infiltration Standard Density Volume Flow Rate", name)
        zones.append(ZoneAirflow(
            zone=name, volume_m3=volume,
            infiltration_ach_mean=max(ach_mean, 0.0),
            infiltration_ach_peak=max(ach_peak, 0.0),
            infiltration_m3_s_mean=max(flow_mean, 0.0)))

    couplings: list[ZoneCoupling] = []
    receiving = "ROOM"
    if receiving in volumes:
        flow_mean, flow_peak = mean_peak(
            "Zone Mixing Current Density Volume Flow Rate", receiving)
        mass_mean, _ = mean_peak("Zone Mixing Mass Flow Rate", receiving)
        partners = [name for name in volumes if name != receiving]
        if partners and (flow_mean > 0.0 or flow_peak > 0.0 or mass_mean > 0.0):
            couplings.append(ZoneCoupling(
                from_zone=partners[0], to_zone=receiving,
                volume_flow_m3_s_mean=max(flow_mean, 0.0),
                volume_flow_m3_s_peak=max(flow_peak, 0.0),
                mass_flow_kg_s_mean=max(mass_mean, 0.0)))

    if not zones and not couplings:
        return None
    return AirflowSummary(
        zones=tuple(zones), couplings=tuple(couplings),
        source=SourceRef(
            citation="Zone air change rate, standard density volume flow and cross "
                     "mixing flow reported by EnergyPlus for this run",
            status=SourceStatus.DERIVED))


CO2_CANDIDATES = ("Zone Air CO2 Concentration",)
VENTILATION_LOSS = "Zone Ventilation Sensible Heat Loss Energy"


def co2_series(output: SimulationOutput, zone: str = ROOM) -> pd.Series | None:
    found = output.first_available(CO2_CANDIDATES, key=zone)
    if found is None:
        return None
    return _hourly(found[1])


def air_quality(output: SimulationOutput, design: DesignSpec, fresh_air,
                ventilation_l_s: float, infiltration_l_s: float,
                night_hours: Sequence[int]) -> AirQualityMetrics | None:
    if fresh_air is None:
        return None
    limit = design.ventilation.co2_limit_ppm
    per_person = design.ventilation.fresh_air_l_s_per_person \
        or fresh_air.fresh_air_l_s_per_person
    series = co2_series(output)
    peak = mean = None
    night_above = above = 0.0
    if series is not None and not series.empty:
        night = np.isin(series.index.hour.to_numpy(), np.asarray(tuple(night_hours),
                                                                 dtype=int))
        values = series.to_numpy(dtype=float)
        above = float(np.sum(values > limit))
        if night.any():
            peak = float(values[night].max())
            mean = float(values[night].mean())
            night_above = float(np.sum(values[night] > limit))
    return AirQualityMetrics(
        co2_limit_ppm=limit, outdoor_co2_ppm=fresh_air.outdoor_ppm,
        site_pressure_kpa=fresh_air.site_pressure_kpa,
        fresh_air_l_s_per_person=per_person,
        sea_level_fresh_air_l_s_per_person=fresh_air.sea_level_fresh_air_l_s_per_person,
        fresh_air_sized_from_limit=design.ventilation.fresh_air_l_s_per_person is None,
        ventilation_design_l_s=ventilation_l_s, infiltration_design_l_s=infiltration_l_s,
        peak_night_co2_ppm=peak, mean_night_co2_ppm=mean,
        night_hours_above_limit=night_above, hours_above_limit=above,
        ventilation_heat_loss_kwh=_sum_kwh(output, VENTILATION_LOSS, (ROOM,)))
