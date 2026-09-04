from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

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
from .errors import EngineError, SimulationFailed
from .geometry import GeometryModel, ROOM, build_geometry
from .hashing import stable_hash
from .idf import IdfBundle, build_idf
from .materials import MaterialLibrary
from .mesh import build_mesh
from .metrics import (ZoneSeries, condensation, energy_metrics, heat_balance,
                      solar_gain_by_facade, surface_frames, zone_series)
from .optimize import Metrics, optimise
from .recommend import RecommendationInputs, generate_recommendations
from .results import SimulationOutput, blocking
from .runner import simulate as run_simulation
from .solar import daylight_lost_to_terrain_h, sun_path, terrain_mesh
from .schema import (Assumption, BuildabilityMetrics, CFDExport, ClimateObjective,
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

DEFAULT_COMFORT_MODEL = "imac_2016_nv"
DEFAULT_FIXED_TARGET = "dihar_operational_target"
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
        payload = build_mesh(model, constructions, self.library)
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
                          __version__)
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
            design_day)
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
        cache_key = stable_hash("simulate", key, with_uncertainty,
                                with_reference, reference_key if with_reference else "")
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
            design_day)
        breakdown = heat_balance(products.output, products.model, design,
                                 products.series, self.thermal_bridges)
        condensation_metrics = condensation(products.output, products.model,
                                            products.series)
        cost = cost_breakdown(design, products.model, self.library)
        build = buildability(design, products.model, self.library)

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
            reference_result = self._reference_result(reference, options)
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
            hourly=tuple(hourly), surfaces=surfaces,
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
            frame = comfort_bounds_frame(series.outdoor_c, self.comfort_model)
        except EngineError:
            return None
        if frame.empty:
            return None
        return float(frame["lower_c"].mean())

    def _hourly_series(self, series: ZoneSeries) -> tuple[HourlySeries, ...]:
        stamps = tuple(value.isoformat() for value in series.operative_c.index)
        bounds = comfort_bounds_frame(series.outdoor_c, self.comfort_model)
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
                          options: RunOptions) -> SimulationResult | None:
        if reference is None:
            spec = self.references.default
        elif isinstance(reference, DesignSpec):
            spec = reference
        else:
            spec = self.references.get(reference)
        if not spec.is_reference:
            spec = spec.variant(is_reference=True)
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

    def _integrity_warnings(self) -> list[EngineWarning]:
        warnings: list[EngineWarning] = []
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
        if self.comfort_model.source.status is not SourceStatus.VERIFIED:
            warnings.append(EngineWarning(
                code="comfort_model_unverified", severity=Severity.WARNING,
                origin="engine",
                message=(f"The comfort model {self.comfort_model.id} is marked "
                         f"{self.comfort_model.source.status.value}. Its coefficients "
                         "must be checked against the paper before any comfort number "
                         "is published.")))
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
        reference_result = self._reference_result(reference, options)
        if reference_result is None:
            raise EngineError("a comparison needs a reference design")
        rows = [self._comparison_row(reference_result, reference_result)]
        warnings: list[EngineWarning] = []
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

    def optimise(self, spec: OptimisationSpec, options: RunOptions) -> ParetoResult:
        def evaluate(design: DesignSpec) -> Metrics:
            return self.metrics_for(design, options)
        return optimise(spec, evaluate, site_id=self.site.id,
                        cache=LRUCache(maxsize=8192))

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
