from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
import pandas as pd

from . import __version__
from .baseline import (ReferenceCatalogue, check_library_covers_references,
                       compare_to_reference, load_references)
from .cache import Cache, LRUCache
from .cfd import build_cfd_export, select_hours
from .climate import summarise as summarise_climate
from .comfort import ComfortCatalogue, comfort_bounds_frame, evaluate_comfort, load_catalogue
from .config import SETTINGS, EngineSettings, EnergyPlusInstall, find_energyplus
from .constructions import ConstructionSet, build_constructions
from .costing import buildability, cost_breakdown, envelope_u_values, glazing_u_values
from .endurance import (CUTOFF_HOUR, FLOOR_C, EnduranceResult, coast_down, cutoff_for,
                        night_window, window_sum)
from .errors import EngineError, SimulationFailed
from .geometry import GeometryModel, ROOM, build_geometry
from .hashing import stable_hash
from .idf import IdfBundle, build_idf
from .materials import MaterialLibrary
from .mesh import build_mesh
from .metrics import (HEATING_CANDIDATES as HEATING_ENERGY_CANDIDATES, ZoneSeries,
                      air_quality, airflow_summary, condensation, energy_metrics,
                      heat_balance, solar_gain_by_facade, surface_frames,
                      zone_series)
from .optimize import Metrics, optimise
from .programme import layout_for
from .recommend import RecommendationInputs, generate_recommendations
from .results import SimulationOutput, blocking
from .runner import simulate as run_simulation
from .solar import daylight_lost_to_terrain_h, sun_path, terrain_mesh
from .schema import (Arrangement, Assumption, BuildabilityMetrics, CFDExport,
                     ClimateObjective,
                     ClimateSummary,
                     ComfortModel, ComfortMetrics, CondensationMetrics,
                     ComparisonRow, ComparisonTable, CostBreakdown, DesignSpec,
                     EnergyMetrics, EngineWarning, Facade, FixedComfortTarget,
                     FuelModel, GeometryPayload, HeatBalanceBreakdown,
                     HourlySeries, OptimisationSpec, ParetoResult, Provenance,
                     RunOptions, RunPeriod, Severity, SimulationResult, SiteSpec,
                     SourceRef, SourceStatus, SunPath, SurfaceResults,
                     SurfaceSeries, TerrainMesh, ThermalBridge, UncertaintyBand)
from .uncertainty import select_parameters, uncertainty_band
from .weather import WeatherFile, load_epw

DEFAULT_COMFORT_MODEL = "drdo_tot_band"
CO2_MARGINAL_FRACTION = 0.05
DEFAULT_FIXED_TARGET = "drdo_band_night"
UNCERTAINTY_METRICS = (
    ("minimum_overnight_operative_c", "C", True),
    ("degree_hours_total_kh", "Kh", True),
)

ENGINE_SOURCE = SourceRef(
    citation="EnergyPlus whole building heat balance, run through the shelter engine",
    status=SourceStatus.DERIVED)


@dataclass
class _RunProducts:
    output: SimulationOutput
    series: ZoneSeries
    model: GeometryModel
    constructions: ConstructionSet
    bundle: IdfBundle
    runtime_s: float


class Engine:
    def __init__(self, site: SiteSpec, library: MaterialLibrary, *,
                 comfort_model_id: str = DEFAULT_COMFORT_MODEL,
                 fixed_target_id: str | None = DEFAULT_FIXED_TARGET,
                 fuel: FuelModel | str | None = "diesel",
                 thermal_bridges: Sequence[ThermalBridge] = (),
                 references: ReferenceCatalogue | None = None,
                 catalogue: ComfortCatalogue | None = None,
                 cfd_directory: str | Path | None = None,
                 cache: Cache | None = None,
                 settings: EngineSettings | None = None,
                 install: EnergyPlusInstall | None = None,
                 weather: WeatherFile | None = None):
        self.site = site
        self.library = library
        self.settings = settings or SETTINGS
        self.catalogue = catalogue or load_catalogue()
        self.references = references or load_references()
        self.comfort_model: ComfortModel = self.catalogue.adaptive_model(comfort_model_id)
        self.fixed_target: FixedComfortTarget | None = (
            self.catalogue.fixed_target(fixed_target_id) if fixed_target_id else None)
        if isinstance(fuel, str):
            self.fuel: FuelModel | None = self.catalogue.fuel(fuel)
        else:
            self.fuel = fuel
        self.thermal_bridges = tuple(thermal_bridges)
        self.cfd_directory = Path(cfd_directory) if cfd_directory else None
        self._install = install
        self._cache: Cache = cache if cache is not None else LRUCache(maxsize=512)
        self._metric_cache: Cache = LRUCache(maxsize=4096)
        self.weather = weather or load_epw(site.epw_path, site)
        self._nominal_year = int(self.weather.frame.index[0].year)

    @property
    def install(self) -> EnergyPlusInstall:
        if self._install is None:
            self._install = find_energyplus()
        return self._install

    def design_hash(self, design: DesignSpec, options: RunOptions | None = None,
                    conditioned: bool = False) -> str:
        return stable_hash(design, self.site, options, conditioned,
                           self.comfort_model.id, __version__)

    def geometry(self, design: DesignSpec) -> GeometryPayload:
        key = stable_hash("geometry", design, self.site.horizon, __version__)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        constructions = build_constructions(design, self.library)
        model = build_geometry(design, constructions.names,
                               design_hash=stable_hash(design),
                               horizon=self.site.horizon)
        payload = build_mesh(model, constructions, self.library,
                             layout=layout_for(design.plan), use=design.plan.use)
        self._cache.set(key, payload)
        return payload

    def _prepare(self, design: DesignSpec) -> tuple[ConstructionSet, GeometryModel]:
        constructions = build_constructions(design, self.library)
        model = build_geometry(design, constructions.names,
                               design_hash=stable_hash(design),
                               horizon=self.site.horizon)
        return constructions, model

    def _run(self, design: DesignSpec, options: RunOptions,
             conditioned: bool) -> _RunProducts:
        constructions, model = self._prepare(design)
        label = f"{design.name}_{'conditioned' if conditioned else 'free'}"
        started = time.perf_counter()
        bundle = build_idf(design, self.site, model, constructions, options,
                           conditioned=conditioned, install=self.install)
        try:
            output, _ = run_simulation(bundle, self.site.epw_path, label=label,
                                       nominal_year=self._nominal_year,
                                       settings=self.settings, install=self.install)
        except SimulationFailed as failure:
            if not _is_ctf_failure(failure):
                raise
            bundle = build_idf(design, self.site, model, constructions, options,
                               conditioned=conditioned, install=self.install,
                               force_finite_difference=True)
            bundle.warnings.append(EngineWarning(
                code="ctf_fallback_finite_difference", severity=Severity.WARNING,
                origin="engine",
                message=("The conduction transfer function solver could not converge on "
                         "this construction, which happens when a very light or very "
                         "resistive layer sits next to a heavy one. The run was repeated "
                         "with the conduction finite difference solver, which handles it "
                         "but is slower. The result is from the finite difference "
                         "solver, not the default one.")))
            output, _ = run_simulation(bundle, self.site.epw_path,
                                       label=f"{label}_condfd",
                                       nominal_year=self._nominal_year,
                                       settings=self.settings, install=self.install)
        runtime = time.perf_counter() - started
        warnings: list[EngineWarning] = []
        series = zone_series(output, ROOM, warnings)
        bundle.warnings.extend(warnings)
        return _RunProducts(output=output, series=series, model=model,
                            constructions=constructions, bundle=bundle,
                            runtime_s=runtime)

    def metrics_for(self, design: DesignSpec,
                    options: RunOptions) -> Metrics:
        key = stable_hash("metrics", design, self.site, options, self.comfort_model.id,
                          self.library.fingerprint, __version__)
        cached = self._metric_cache.get(key)
        if cached is not None:
            return cached
        light = options.model_copy(update={"surface_output": False,
                                           "conditioned_run": False})
        products = self._run(design, light, conditioned=False)
        design_day = self.weather.coldest_day(light.period)
        comfort = evaluate_comfort(
            products.series.operative_c, products.series.outdoor_c,
            self.comfort_model, light.period, design.occupancy, self.fixed_target,
            design_day, self._outdoor_history)
        condensation_metrics = condensation(products.output, products.model,
                                            products.series)
        cost = cost_breakdown(design, products.model, self.library)
        metrics: Metrics = {
            "degree_hours_total_kh": comfort.degree_hours_total_kh,
            "degree_hours_below_kh": comfort.degree_hours_below_kh,
            "degree_hours_above_kh": comfort.degree_hours_above_kh,
            "hours_outside_band": comfort.hours_outside_band,
            "occupied_hours_outside_band": comfort.occupied_hours_outside_band,
            "total_cost_inr": cost.total_inr,
            "condensation_hours": condensation_metrics.hours,
            "wall_thickness_m": design.wall.thickness_m,
            "surface_to_volume": design.plan.surface_to_volume,
        }
        for name, value in (("worst_night_margin_k", comfort.worst_night_margin_k),
                            ("minimum_overnight_operative_c",
                             comfort.minimum_overnight_operative_c),
                            ("minimum_operative_c", comfort.minimum_operative_c),
                            ("minimum_on_design_day_c", comfort.minimum_on_design_day_c),
                            ("time_lag_h", comfort.time_lag_h)):
            if value is not None:
                metrics[name] = float(value)
        self._metric_cache.set(key, metrics)
        return metrics

    def simulate(self, design: DesignSpec, options: RunOptions, *,
                 reference: str | DesignSpec | None = None,
                 with_uncertainty: bool = True,
                 with_reference: bool = True) -> SimulationResult:
        key = self.design_hash(design, options, conditioned=options.conditioned_run)
        reference_key = reference.name if isinstance(reference, DesignSpec)             else (reference or self.references.default_name)
        cache_key = stable_hash("simulate", key, with_uncertainty, with_reference,
                                self.library.fingerprint,
                                reference_key if with_reference else "")
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

        products = self._run(design, options, conditioned=False)
        warnings = list(products.bundle.warnings) + list(products.output.warnings)
        warnings.extend(self.weather.warnings)
        assumptions = list(products.bundle.assumptions) + list(self.weather.assumptions)

        design_day = self.weather.coldest_day(options.period)
        comfort = evaluate_comfort(
            products.series.operative_c, products.series.outdoor_c,
            self.comfort_model, options.period, design.occupancy, self.fixed_target,
            design_day, self._outdoor_history)
        warnings.extend(self._applicability_warnings(comfort))
        breakdown = heat_balance(products.output, products.model, design,
                                 products.series, self.thermal_bridges)
        condensation_metrics = condensation(products.output, products.model,
                                            products.series)
        cost = cost_breakdown(design, products.model, self.library)
        build = buildability(design, products.model, self.library)
        breathing = air_quality(products.output, design, products.bundle.fresh_air,
                                products.bundle.ventilation_design_l_s,
                                products.bundle.infiltration_design_l_s,
                                options.heating_hours)
        warnings.extend(self._air_quality_warnings(breathing))

        energy = None
        if options.conditioned_run:
            conditioned = self._run(design, options, conditioned=True)
            energy = energy_metrics(conditioned.output,
                                    conditioned.bundle.conditioned_zone_names,
                                    self.fuel)
            warnings.extend(conditioned.output.warnings)
        if energy is None:
            energy = EnergyMetrics()

        hourly = self._hourly_series(products.series)
        surfaces = self._surface_results(products, options) \
            if options.surface_output else None

        bands: list[UncertaintyBand] = []
        if with_uncertainty:
            bands = self._uncertainty(design, options, comfort)

        reference_comparison = None
        if with_reference:
            reference_result = self._reference_result(reference, options,
                                                      design)
            if reference_result is not None:
                reference_comparison = compare_to_reference(
                    self._provisional(design, comfort, energy, cost, options),
                    reference_result)
                hourly = hourly + (self._reference_series(reference_result),)

        lower_bound = self._comfort_lower_bound(products.series)
        recommendations = generate_recommendations(RecommendationInputs(
            design=design, library=self.library, model=products.model,
            comfort=comfort, breakdown=breakdown, condensation=condensation_metrics,
            buildability=build, cost=cost,
            climate_objective=self.site.climate_objective,
            incident_solar_kwh_by_facade=solar_gain_by_facade(products.output,
                                                              products.model),
            comfort_lower_bound_c=lower_bound))

        assumptions.extend(self._standing_assumptions(design))
        assumptions.extend(cost.assumptions)
        warnings.extend(self._integrity_warnings())

        result = SimulationResult(
            design_hash=key, design_fingerprint=stable_hash(design),
            design=design, site_id=self.site.id,
            period=options.period,
            climate_objective=self.site.climate_objective,
            headline_metric=self._headline_metric(),
            comfort=comfort, energy=energy, breakdown=breakdown,
            condensation=condensation_metrics, cost=cost, buildability=build,
            air_quality=breathing,
            hourly=tuple(hourly),
            airflow=airflow_summary(products.output, products.model),
            surfaces=surfaces,
            reference=reference_comparison, uncertainty=tuple(bands),
            recommendations=recommendations,
            assumptions=tuple(_dedupe_assumptions(assumptions)),
            warnings=tuple(_dedupe_warnings(warnings)),
            engine_version=__version__,
            energyplus_version=self.install.version_string,
            runtime_s=products.runtime_s, provenance=Provenance.SIMULATED)
        self._cache.set(cache_key, result)
        return result

    def _provisional(self, design: DesignSpec, comfort: ComfortMetrics,
                     energy: EnergyMetrics, cost: CostBreakdown,
                     options: RunOptions) -> SimulationResult:
        return SimulationResult(
            design_hash=stable_hash(design), design_fingerprint=stable_hash(design),
            design=design, site_id=self.site.id,
            period=options.period, climate_objective=self.site.climate_objective,
            headline_metric=self._headline_metric(), comfort=comfort, energy=energy,
            breakdown=HeatBalanceBreakdown(), condensation=CondensationMetrics(),
            cost=cost,
            buildability=BuildabilityMetrics(
                wall_thickness_m=design.wall.thickness_m,
                surface_to_volume=design.plan.surface_to_volume))

    def _headline_metric(self) -> str:
        if self.site.climate_objective is ClimateObjective.COOLING_DOMINATED:
            return "maximum_daytime_operative_c"
        return "minimum_overnight_operative_c"

    def _comfort_lower_bound(self, series: ZoneSeries) -> float | None:
        try:
            frame = comfort_bounds_frame(series.outdoor_c, self.comfort_model,
                                         self._outdoor_history)
        except EngineError:
            return None
        if frame.empty:
            return None
        return float(frame["lower_c"].mean())

    def _hourly_series(self, series: ZoneSeries) -> tuple[HourlySeries, ...]:
        stamps = tuple(value.isoformat() for value in series.operative_c.index)
        bounds = comfort_bounds_frame(series.outdoor_c, self.comfort_model,
                                      self._outdoor_history)
        aligned = bounds.reindex(series.operative_c.index).ffill().bfill()
        output = [
            HourlySeries(name="operative_temperature_c", unit="C", timestamps=stamps,
                         values=tuple(_finite(series.operative_c))),
            HourlySeries(name="air_temperature_c", unit="C", timestamps=stamps,
                         values=tuple(_finite(
                             series.air_c.reindex(series.operative_c.index)))),
            HourlySeries(name="outdoor_temperature_c", unit="C", timestamps=stamps,
                         values=tuple(_finite(
                             series.outdoor_c.reindex(series.operative_c.index)))),
            HourlySeries(name="comfort_lower_bound_c", unit="C", timestamps=stamps,
                         values=tuple(_finite(aligned["lower_c"])),
                         provenance=Provenance.LITERATURE),
            HourlySeries(name="comfort_upper_bound_c", unit="C", timestamps=stamps,
                         values=tuple(_finite(aligned["upper_c"])),
                         provenance=Provenance.LITERATURE),
        ]
        if series.relative_humidity_pct is not None:
            output.append(HourlySeries(
                name="indoor_relative_humidity_pct", unit="percent", timestamps=stamps,
                values=tuple(_finite(series.relative_humidity_pct.reindex(
                    series.operative_c.index)))))
        return tuple(output)

    def _reference_series(self, reference: SimulationResult) -> HourlySeries:
        for series in reference.hourly:
            if series.name == "operative_temperature_c":
                return series.model_copy(update={
                    "name": "reference_operative_temperature_c"})
        raise EngineError("the reference result carries no operative temperature series")

    def _surface_results(self, products: _RunProducts,
                         options: RunOptions) -> SurfaceResults | None:
        window = options.effective_surface_period
        names = [polygon.name for polygon in products.model.polygons]
        frames = surface_frames(products.output, names)
        if not frames:
            return None
        start = pd.Timestamp(date(self._nominal_year, window.start.month,
                                  window.start.day))
        end = pd.Timestamp(date(self._nominal_year, window.end.month,
                                window.end.day)) + pd.Timedelta(1, unit="D")
        clipped = {name: frame.loc[(frame.index >= start) & (frame.index < end)]
                   for name, frame in frames.items()}
        clipped = {name: frame for name, frame in clipped.items() if not frame.empty}
        if not clipped:
            return None
        index = next(iter(clipped.values())).index
        stamps = tuple(value.isoformat() for value in index)
        lookup = {polygon.name: polygon for polygon in products.model.polygons}
        series: list[SurfaceSeries] = []
        for name in names:
            polygon = lookup[name]
            payload: dict[str, tuple] = {}
            for variable, attribute in (
                    ("Surface Inside Face Temperature", "inside_face_temperature_c"),
                    ("Surface Outside Face Temperature", "outside_face_temperature_c"),
                    ("Surface Inside Face Conduction Heat Transfer Rate", "conduction_w"),
                    ("Surface Outside Face Incident Solar Radiation Rate per Area",
                     "incident_solar_w_m2")):
                frame = clipped.get(variable)
                if frame is None or name not in frame.columns:
                    continue
                payload[attribute] = tuple(_finite(frame[name].reindex(index)))
            if not payload:
                continue
            series.append(SurfaceSeries(surface=name, zone=polygon.zone,
                                        kind=polygon.kind, **payload))
        if not series:
            return None
        return SurfaceResults(timestamps=stamps, period=window,
                              surfaces=tuple(series))

    def _uncertainty(self, design: DesignSpec, options: RunOptions,
                     comfort) -> list[UncertaintyBand]:
        parameters = select_parameters(design, self.library)
        if not parameters:
            return []
        light = options.model_copy(update={"surface_output": False,
                                           "conditioned_run": False})
        bands: list[UncertaintyBand] = []
        for metric, unit, _ in UNCERTAINTY_METRICS:
            nominal = getattr(comfort, metric, None)
            if nominal is None:
                continue

            def evaluate(variant: DesignSpec, library: MaterialLibrary,
                         _metric: str = metric) -> float:
                probe = Engine(self.site, library,
                               comfort_model_id=self.comfort_model.id,
                               fixed_target_id=self.fixed_target.id
                               if self.fixed_target else None,
                               fuel=self.fuel, thermal_bridges=self.thermal_bridges,
                               references=self.references, catalogue=self.catalogue,
                               cache=self._cache, settings=self.settings,
                               install=self.install, weather=self.weather)
                probe._metric_cache = self._metric_cache
                metrics = probe.metrics_for(variant, light)
                value = metrics.get(_metric)
                if value is None:
                    raise EngineError(
                        f"the uncertainty run produced no {_metric}")
                return float(value)

            bands.append(uncertainty_band(
                evaluate, design, self.library, parameters, metric, unit,
                nominal_value=float(nominal)))
        return bands

    def _reference_result(self, reference: str | DesignSpec | None,
                          options: RunOptions,
                          design: DesignSpec | None = None
                          ) -> SimulationResult | None:
        if reference is None:
            spec = self.references.default
        elif isinstance(reference, DesignSpec):
            spec = reference
        else:
            spec = self.references.get(reference)
        if not spec.is_reference:
            spec = spec.variant(is_reference=True)
        if design is not None:
            spec = spec.variant(plan=design.plan, occupancy=design.occupancy,
                                arrangement=Arrangement.SINGLE_ROOM,
                                arrangement_params=spec.arrangement_params)
        return self.simulate(spec, options, with_uncertainty=False,
                             with_reference=False)

    def _standing_assumptions(self, design: DesignSpec) -> list[Assumption]:
        u_values = envelope_u_values(design, self.library)
        assumptions = [
            Assumption(name="comfort_model", value=self.comfort_model.name,
                       source=self.comfort_model.source,
                       provenance=Provenance.LITERATURE),
            Assumption(name="weather_file", value=str(self.weather.path.name),
                       source=self.site.source or ENGINE_SOURCE,
                       provenance=Provenance.MEASURED),
            Assumption(name="site_elevation_m", value=self.site.elevation_m, unit="m",
                       source=self.site.source or ENGINE_SOURCE,
                       provenance=Provenance.MEASURED),
            Assumption(name="infiltration_ach",
                       value=design.ventilation.infiltration_ach,
                       unit="air changes per hour", source=ENGINE_SOURCE,
                       provenance=Provenance.ASSUMED),
        ]
        for element, value in u_values.items():
            assumptions.append(Assumption(
                name=f"{element}_u_value", value=round(value, 3), unit="W/m2K",
                source=ENGINE_SOURCE, provenance=Provenance.DERIVED))
        for facade, value in glazing_u_values(design, self.library).items():
            assumptions.append(Assumption(
                name=f"glazing_u_value_{facade}", value=value, unit="W/m2K",
                source=self.library.glazing(
                    design.windows[Facade(facade)].glazing_id).source,
                provenance=Provenance.LITERATURE))
        if self.fuel is not None:
            assumptions.append(Assumption(
                name="fuel_model", value=f"{self.fuel.fuel} at "
                f"{self.fuel.lower_heating_value_kwh_per_unit} kWh per "
                f"{self.fuel.unit} and {self.fuel.appliance_efficiency:.0%} appliance "
                "efficiency", source=self.fuel.source,
                provenance=Provenance.LITERATURE))
        for bridge in self.thermal_bridges:
            assumptions.append(Assumption(
                name=f"thermal_bridge_{bridge.id}", value=bridge.psi_w_mk,
                unit="W/mK", source=bridge.source, provenance=bridge.provenance))
        return assumptions

    @property
    def _outdoor_history(self):
        return self.weather.frame["dry_bulb_c"]

    def endurance(self, design: DesignSpec, night_of: date, period: RunPeriod,
                  floor_c: float = FLOOR_C,
                  cutoff_hour: int = CUTOFF_HOUR) -> EnduranceResult:
        cutoff = cutoff_for(night_of, cutoff_hour)
        base = RunOptions(period=period, surface_output=False, conditioned_run=True)
        cut = base.model_copy(update={"heating_cutoff": cutoff})
        key = stable_hash("endurance", design, self.site, base, cutoff, floor_c,
                          self.library.fingerprint, __version__)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        heated = self._run(design, base, conditioned=True)
        coasting = self._run(design, cut, conditioned=True)
        nominal = self._in_nominal_year(night_of)
        reached, hours, start, lowest, lowest_at, span = coast_down(
            coasting.series.operative_c, cutoff_for(nominal, cutoff_hour), floor_c)
        lowest_at = lowest_at.replace(year=lowest_at.year + night_of.year - nominal.year) \
            if night_of.month != 2 or night_of.day != 29 else lowest_at
        night_start, night_end = night_window(nominal)
        joules = pd.Series(dtype=float)
        for name in HEATING_ENERGY_CANDIDATES:
            wide = heated.output.wide(name)
            if not wide.empty:
                joules = wide.sum(axis=1)
                break
        night_kwh = window_sum(joules, night_start, night_end) / 3_600_000.0
        result = EnduranceResult(
            night_of=night_of, cutoff=cutoff, floor_c=floor_c, reached_floor=reached,
            hours_above_floor=hours, operative_at_cutoff_c=start,
            minimum_after_cutoff_c=lowest, minimum_at=lowest_at,
            hours_simulated_after_cutoff=span, night_heating_kwh=night_kwh,
            night_fuel_units=self.fuel.units_for(night_kwh) if self.fuel else None,
            fuel_unit_name=self.fuel.unit if self.fuel else None,
            fuel=self.fuel.fuel if self.fuel else None)
        self._cache.set(key, result)
        return result

    def _in_nominal_year(self, day: date) -> date:
        if day.month == 2 and day.day == 29:
            return date(self._nominal_year, 2, 28)
        return day.replace(year=self._nominal_year)

    def _air_quality_warnings(self, breathing) -> list[EngineWarning]:
        if breathing is None or breathing.night_hours_above_limit <= 0.0:
            return []
        peak = breathing.peak_night_co2_ppm or breathing.co2_limit_ppm
        marginal = peak <= breathing.co2_limit_ppm * (1.0 + CO2_MARGINAL_FRACTION)
        return [EngineWarning(
            code="night_co2_above_limit",
            severity=Severity.WARNING if marginal else Severity.SEVERE, origin="engine",
            count=int(breathing.night_hours_above_limit),
            message=(f"CO2 in the room passed {breathing.co2_limit_ppm:.0f} ppm for "
                     f"{breathing.night_hours_above_limit:.0f} night hours, peaking at "
                     f"{breathing.peak_night_co2_ppm:.0f} ppm. At "
                     f"{breathing.site_pressure_kpa:.1f} kPa each occupant needs "
                     f"{breathing.fresh_air_l_s_per_person:.1f} L/s of fresh air to hold "
                     "the limit, which this design does not supply."))]

    def _applicability_warnings(self, comfort) -> list[EngineWarning]:
        model = self.comfort_model
        if comfort.hours_outside_applicability <= 0.0 or model.is_fixed_band:
            return []
        span = model.outdoor_applicability_c
        days = model.running_mean_days
        mean = f"{days} day" if days else "exponentially weighted"
        return [EngineWarning(
            code="comfort_model_outside_applicability", severity=Severity.SEVERE,
            origin="engine", count=int(comfort.hours_outside_applicability),
            message=(f"{model.name} is only defined for a {mean} outdoor running mean "
                     f"between {span.low:g} and {span.high:g} C. Here it ran from "
                     f"{comfort.running_mean_min_c:.1f} to "
                     f"{comfort.running_mean_max_c:.1f} C, so for "
                     f"{comfort.hours_outside_applicability:.0f} hours the band was held "
                     "at the edge of its range rather than computed. Those hours are "
                     "judged against a band the model does not define; read them against "
                     "the DRDO 17 plus or minus 3 C band instead."))]

    def _integrity_warnings(self) -> list[EngineWarning]:
        warnings: list[EngineWarning] = []
        suspect = self.library.implausible()
        if suspect:
            detail = "; ".join(
                f"{name}: {' and '.join(problems)}"
                for name, problems in sorted(suspect.items()))
            warnings.append(EngineWarning(
                code="material_properties_implausible", severity=Severity.SEVERE,
                origin="materials", count=len(suspect),
                message=("These material properties fail a physical plausibility check "
                         "and will silently corrupt every result built on them. "
                         f"{detail}. Confirm them against the ASHRAE Handbook of "
                         "Fundamentals before any reported number depends on them.")))
        pending = self.library.unsourced()
        if pending:
            warnings.append(EngineWarning(
                code="materials_unsourced", severity=Severity.WARNING,
                origin="materials", count=len(pending),
                message=("These material records do not yet carry a verified source: "
                         f"{', '.join(sorted(pending)[:12])}"
                         + ("..." if len(pending) > 12 else "")
                         + ". Every number on screen has to be traceable, so they must "
                           "be cited before a reported result.")))
        unpriced = self.library.unpriced()
        if unpriced:
            warnings.append(EngineWarning(
                code="capital_cost_unsourced", severity=Severity.WARNING,
                origin="materials", count=len(unpriced),
                message=("Every capital cost on screen is built from delivered prices "
                         "that carry no verified source: "
                         f"{', '.join(sorted(unpriced)[:12])}"
                         + ("..." if len(unpriced) > 12 else "")
                         + ". The rupee totals are therefore indicative only. They are "
                           "the right shape, because the volumes come from the real "
                           "geometry, but the rates are placeholders and a comparison "
                           "between two designs is only as sound as the relative prices. "
                           "Replace them with a Layer 0 procurement schedule before any "
                           "money figure is quoted.")))
        if self.comfort_model.source.status is not SourceStatus.VERIFIED:
            warnings.append(EngineWarning(
                code="comfort_model_unverified", severity=Severity.WARNING,
                origin="engine",
                message=(f"The comfort model {self.comfort_model.id} is marked "
                         f"{self.comfort_model.source.status.value}. Its coefficients "
                         "must be checked against the paper before any comfort number "
                         "is published.")))
        twins = [model.id for model in self.catalogue.adaptive
                 if model.id != self.comfort_model.id
                 and model.intercept_c == self.comfort_model.intercept_c
                 and model.slope == self.comfort_model.slope
                 and model.band_half_width_k == self.comfort_model.band_half_width_k]
        if twins:
            warnings.append(EngineWarning(
                code="comfort_model_duplicates_another", severity=Severity.WARNING,
                origin="engine", count=len(twins),
                message=(f"The comfort model {self.comfort_model.id} carries exactly "
                         f"the same coefficients as {', '.join(sorted(twins))}, so "
                         "choosing between them cannot change a single comfort number. "
                         "One of them is a placeholder standing in until its paper is "
                         "read. Treat the choice as having no effect until the "
                         "coefficients differ.")))
        uncited = self.references.uncited()
        if uncited:
            warnings.append(EngineWarning(
                code="reference_designs_uncited", severity=Severity.WARNING,
                origin="engine", count=len(uncited),
                message=("Reference designs without a verified citation: "
                         f"{', '.join(uncited)}. Every reported difference is measured "
                         "against one of these, so the citation is not optional.")))
        if self.site.horizon is None:
            warnings.append(EngineWarning(
                code="no_horizon_profile", severity=Severity.WARNING, origin="engine",
                message=("No terrain horizon profile was supplied, so the simulation "
                         "assumes a flat horizon in every direction. In a valley that "
                         "overstates daily solar gain.")))
        if self.fuel is not None and self.fuel.price_inr_per_unit <= 0.0:
            warnings.append(EngineWarning(
                code="fuel_price_unset", severity=Severity.WARNING, origin="engine",
                message=("The fuel price is zero, so any rupee figure is zero by "
                         "construction. Set a procurement price before showing money.")))
        return warnings

    def surface_results(self, design: DesignSpec, options: RunOptions,
                        period: RunPeriod | None = None) -> SurfaceResults | None:
        scoped = options if period is None else options.model_copy(
            update={"surface_output": True, "surface_output_period": period})
        products = self._run(design, scoped, conditioned=False)
        return self._surface_results(products, scoped)

    def compare(self, designs: Sequence[DesignSpec], options: RunOptions,
                reference: str | DesignSpec | None = None) -> ComparisonTable:
        if not designs:
            raise EngineError("a comparison needs at least one design")
        reference_result = self._reference_result(reference, options, designs[0])
        if reference_result is None:
            raise EngineError("a comparison needs a reference design")
        rows = [self._comparison_row(reference_result, reference_result)]
        warnings: list[EngineWarning] = []
        footprints = {round(design.plan.floor_area_m2, 2) for design in designs}
        if len(footprints) > 1:
            warnings.append(EngineWarning(
                code="comparison_mixes_floor_areas", severity=Severity.SEVERE,
                origin="engine", count=len(footprints),
                message=("These designs do not all have the same floor area "
                         f"({', '.join(f'{value:g}' for value in sorted(footprints))} "
                         "square metres), and the reference is matched to the first "
                         "one. Energy and cost totals scale with size, so the rows "
                         "are not comparable to one another on those columns. Compare "
                         "temperatures, or give the designs the same plan.")))
        for design in designs:
            result = self.simulate(design, options, reference=reference,
                                   with_uncertainty=True)
            rows.append(self._comparison_row(result, reference_result))
            warnings.extend(item for item in result.warnings
                            if item.severity in (Severity.SEVERE, Severity.FATAL))
        return ComparisonTable(
            reference_hash=reference_result.design_hash,
            headline_metric=self._headline_metric(),
            climate_objective=self.site.climate_objective,
            rows=tuple(rows), warnings=tuple(_dedupe_warnings(warnings)))

    def _comparison_row(self, result: SimulationResult,
                        reference: SimulationResult) -> ComparisonRow:
        band = None
        for item in result.uncertainty:
            if item.metric == "minimum_overnight_operative_c":
                band = (item.low, item.high)
        deltas: dict[str, float] = {}
        if result.design_hash != reference.design_hash:
            for name, candidate, base in (
                    ("minimum_overnight_operative_c",
                     result.comfort.minimum_overnight_operative_c,
                     reference.comfort.minimum_overnight_operative_c),
                    ("degree_hours_total_kh", result.comfort.degree_hours_total_kh,
                     reference.comfort.degree_hours_total_kh),
                    ("heating_demand_kwh", result.energy.heating_demand_kwh,
                     reference.energy.heating_demand_kwh),
                    ("total_cost_inr", result.cost.total_inr,
                     reference.cost.total_inr)):
                if candidate is not None and base is not None:
                    deltas[name] = float(candidate) - float(base)
        return ComparisonRow(
            design_hash=result.design_hash, name=result.design.name,
            is_reference=result.design.is_reference,
            minimum_overnight_operative_c=result.comfort.minimum_overnight_operative_c,
            minimum_overnight_band=band,
            hours_below_band=result.comfort.hours_below_band,
            degree_hours_total_kh=result.comfort.degree_hours_total_kh,
            heating_demand_kwh=result.energy.heating_demand_kwh,
            fuel_units=result.energy.fuel_units,
            total_cost_inr=result.cost.total_inr,
            wall_thickness_m=result.buildability.wall_thickness_m,
            locally_available=result.cost.non_local_material_fraction < 0.5,
            mason_skill=result.cost.max_mason_skill,
            condensation_hours=result.condensation.hours,
            delta_vs_reference=deltas)

    def optimise(self, spec: OptimisationSpec, options: RunOptions,
                 on_stage: Callable[[ParetoResult], None] | None = None
                 ) -> ParetoResult:
        def evaluate(design: DesignSpec) -> Metrics:
            return self.metrics_for(design, options)
        return optimise(spec, evaluate, site_id=self.site.id,
                        cache=LRUCache(maxsize=8192), on_stage=on_stage)

    def cfd_export(self, design: DesignSpec, options: RunOptions
                   ) -> tuple[CFDExport, ...]:
        scoped = options.model_copy(update={"surface_output": True})
        products = self._run(design, scoped, conditioned=False)
        hours = select_hours(products.output, products.series)
        exports = []
        for label, moment in hours.items():
            exports.append(build_cfd_export(
                design, products.model, products.output, products.series, moment,
                label, stable_hash(design), self.library))
        return tuple(exports)

    def climate(self, window_days: int = 7) -> ClimateSummary:
        return summarise_climate(self.site, self.weather, window_days)

    def sun_path(self, day: date, step_minutes: int = 20) -> SunPath:
        return sun_path(self.site, day, step_minutes)

    def daylight_lost_to_terrain_h(self, day: date) -> float:
        return daylight_lost_to_terrain_h(self.sun_path(day))

    def terrain(self) -> TerrainMesh:
        return terrain_mesh(self.site)

    def self_check(self) -> list[EngineWarning]:
        warnings: list[EngineWarning] = list(self.weather.warnings)
        missing = check_library_covers_references(self.library, self.references)
        if missing:
            warnings.append(EngineWarning(
                code="reference_materials_missing", severity=Severity.SEVERE,
                origin="materials", count=len(missing),
                message=("The material library does not cover the reference designs. "
                         f"Missing: {', '.join(missing)}. Layer 0 must add these "
                         "records before any baseline comparison can run.")))
        if self.site.ground is None:
            warnings.append(EngineWarning(
                code="ground_temperature_missing", severity=Severity.SEVERE,
                origin="engine",
                message=("No monthly ground temperatures are attached to the site. "
                         "EnergyPlus defaults assume a temperate climate and floor "
                         "losses will be materially wrong in Ladakh.")))
        warnings.extend(self._integrity_warnings())
        try:
            self.install
        except Exception as error:
            warnings.append(EngineWarning(
                code="energyplus_missing", severity=Severity.FATAL, origin="engine",
                message=str(error)))
        return _dedupe_warnings(warnings)

    def blocking_warnings(self, result: SimulationResult) -> list[EngineWarning]:
        return blocking(list(result.warnings))


CTF_FAILURE_MARKERS = ("CTF calculation convergence",
                       "InitConductionTransferFunctions")


def _is_ctf_failure(failure: SimulationFailed) -> bool:
    haystack = " ".join([str(failure), *failure.severe, *failure.fatal])
    return any(marker in haystack for marker in CTF_FAILURE_MARKERS)


def _finite(series: pd.Series) -> list[float | None]:
    values: list[float | None] = []
    for value in series.to_numpy(dtype=float):
        values.append(None if not np.isfinite(value) else round(float(value), 4))
    return values


def _dedupe_warnings(items: Iterable[EngineWarning]) -> list[EngineWarning]:
    seen: dict[tuple[str, str, str], EngineWarning] = {}
    order: list[tuple[str, str, str]] = []
    for item in items:
        key = (item.code, item.severity.value, item.message)
        if key in seen:
            seen[key] = seen[key].model_copy(
                update={"count": seen[key].count + item.count})
        else:
            seen[key] = item
            order.append(key)
    return [seen[key] for key in order]


def _dedupe_assumptions(items: Iterable[Assumption]) -> list[Assumption]:
    seen: dict[str, Assumption] = {}
    order: list[str] = []
    for item in items:
        if item.name in seen:
            continue
        seen[item.name] = item
        order.append(item.name)
    return [seen[name] for name in order]
