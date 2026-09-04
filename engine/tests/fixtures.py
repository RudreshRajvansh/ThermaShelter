from __future__ import annotations

from datetime import date

from shelter_engine.materials import MaterialLibrary
from shelter_engine.schema import (Arrangement, ArrangementParams, ComfortModel,
                                   ConstructionSpec, DesignSpec, Facade,
                                   FixedComfortTarget, FuelModel,
                                   GlazingSystem, GroundTemperatures,
                                   HorizonProfile, LayerSpec, Material,
                                   MaterialAppearance, OccupancySpec, PCMSpec,
                                   PhaseChangeMaterial, PlanSpec, RunOptions,
                                   RunPeriod, ShutterSpec, SiteSpec, SourceRef,
                                   SourceStatus, UncertainRange, VentilationSpec,
                                   WindowSpec)

SYNTHETIC = SourceRef(
    citation="Synthetic development fixture, not a real property source",
    status=SourceStatus.PENDING,
    note="Test fixture only. Layer 0 supplies the real material database.")


def _material(identifier: str, name: str, k: float, rho: float, cp: float,
              cost: float, colour: str, local: bool = True,
              skill: str = "semiskilled",
              k_range: tuple[float, float] | None = None) -> Material:
    return Material(
        id=identifier, name=name, conductivity_w_mk=k, density_kg_m3=rho,
        specific_heat_j_kgk=cp, cost_inr_m3=cost, locally_available=local,
        transport_multiplier=1.0 if local else 2.5, mason_skill=skill,
        appearance=MaterialAppearance(colour=colour, finish="rough"),
        source=SYNTHETIC,
        conductivity_range=None if k_range is None
        else UncertainRange(low=k_range[0], high=k_range[1]))


def material_library() -> MaterialLibrary:
    materials = [
        _material("rammed_earth", "Rammed earth", 0.90, 1900.0, 900.0, 2500.0,
                  "#a9865b", k_range=(0.60, 1.30)),
        _material("mud_brick", "Sun dried mud brick", 0.75, 1700.0, 880.0, 2200.0,
                  "#b08a5f"),
        _material("brick_solid", "Solid burnt clay brick", 0.81, 1820.0, 880.0, 5200.0,
                  "#9c4a3a"),
        _material("stone_granite", "Local stone masonry", 2.00, 2600.0, 820.0, 3000.0,
                  "#6f6f6f", skill="skilled"),
        _material("concrete_dense", "Dense concrete", 1.74, 2400.0, 880.0, 6500.0,
                  "#8c8c8c", skill="skilled"),
        _material("cement_plaster", "Cement plaster", 0.72, 1860.0, 840.0, 4800.0,
                  "#c8c4bb"),
        _material("mud_plaster", "Mud plaster", 0.60, 1600.0, 880.0, 900.0, "#b59a72"),
        _material("eps_thermocol", "Expanded polystyrene", 0.038, 20.0, 1400.0, 3200.0,
                  "#f2f2ea", local=False),
        _material("rockwool", "Rockwool slab", 0.040, 100.0, 840.0, 5200.0, "#d8cfc0",
                  local=False),
        _material("timber_softwood", "Softwood boarding", 0.14, 500.0, 1600.0, 32000.0,
                  "#a3752f", local=False, skill="skilled"),
        _material("gi_sheet", "Galvanised corrugated steel sheet", 50.0, 7800.0, 480.0,
                  90000.0, "#b9c2c8", local=False),
        _material("gravel_bed", "Compacted gravel bed", 0.90, 1800.0, 840.0, 1200.0,
                  "#8f8a7e", skill="unskilled"),
    ]
    glazing = [
        GlazingSystem(id="single_clear_4mm", name="Single clear 4 mm",
                      u_value_w_m2k=5.80, shgc=0.82, visible_transmittance=0.88,
                      layers=1, cost_inr_m2=900.0,
                      appearance=MaterialAppearance(colour="#cfe3ef",
                                                    finish="transparent"),
                      source=SYNTHETIC),
        GlazingSystem(id="double_clear_6_12_6", name="Double clear 6-12-6",
                      u_value_w_m2k=2.70, shgc=0.70, visible_transmittance=0.78,
                      layers=2, cost_inr_m2=3200.0, locally_available=False,
                      appearance=MaterialAppearance(colour="#c6dfec",
                                                    finish="transparent"),
                      source=SYNTHETIC),
        GlazingSystem(id="double_lowe_argon", name="Double low-e argon filled",
                      u_value_w_m2k=1.60, shgc=0.58, visible_transmittance=0.71,
                      layers=2, cost_inr_m2=5400.0, locally_available=False,
                      appearance=MaterialAppearance(colour="#bcd9e8",
                                                    finish="transparent"),
                      source=SYNTHETIC),
    ]
    pcm = [
        PhaseChangeMaterial(id="pcm_rt25", name="Paraffin, 25 C melt",
                            melt_temperature_c=25.0, latent_heat_j_kg=170000.0,
                            conductivity_w_mk=0.20, density_kg_m3=880.0,
                            specific_heat_j_kgk=2000.0, melt_band_k=3.0,
                            cost_inr_m3=180000.0, cycle_capacity_retention=0.95,
                            appearance=MaterialAppearance(colour="#e8e2c8",
                                                          finish="smooth"),
                            source=SYNTHETIC),
    ]
    return MaterialLibrary(materials, glazing, pcm, origin="test fixture")


def wall_construction(mass_m: float = 0.40, insulation_m: float = 0.08,
                      mass_material: str = "rammed_earth") -> ConstructionSpec:
    return ConstructionSpec(name="massive_insulated", layers=(
        LayerSpec(material_id="cement_plaster", thickness_m=0.02),
        LayerSpec(material_id="eps_thermocol", thickness_m=insulation_m),
        LayerSpec(material_id=mass_material, thickness_m=mass_m),
        LayerSpec(material_id="mud_plaster", thickness_m=0.02),
    ))


def roof_construction(insulation_m: float = 0.12) -> ConstructionSpec:
    return ConstructionSpec(name="insulated_roof", layers=(
        LayerSpec(material_id="gi_sheet", thickness_m=0.0006),
        LayerSpec(material_id="eps_thermocol", thickness_m=insulation_m),
        LayerSpec(material_id="timber_softwood", thickness_m=0.025),
    ))


def floor_construction() -> ConstructionSpec:
    return ConstructionSpec(name="insulated_floor", layers=(
        LayerSpec(material_id="gravel_bed", thickness_m=0.15),
        LayerSpec(material_id="eps_thermocol", thickness_m=0.05),
        LayerSpec(material_id="concrete_dense", thickness_m=0.10),
    ))


def partition_construction() -> ConstructionSpec:
    return ConstructionSpec(name="mass_partition", layers=(
        LayerSpec(material_id="mud_plaster", thickness_m=0.02),
        LayerSpec(material_id="rammed_earth", thickness_m=0.30),
        LayerSpec(material_id="mud_plaster", thickness_m=0.02),
    ))


def design(arrangement: Arrangement = Arrangement.SINGLE_ROOM,
           **overrides) -> DesignSpec:
    windows = {
        Facade.SOUTH: WindowSpec(wwr=0.30, glazing_id="double_clear_6_12_6",
                                 sill_height_m=0.60,
                                 shutter=ShutterSpec(present=True,
                                                     thermal_resistance_m2k_w=0.40)),
        Facade.EAST: WindowSpec(wwr=0.05, glazing_id="double_clear_6_12_6"),
    }
    if arrangement is Arrangement.TROMBE_WALL:
        windows.pop(Facade.SOUTH)
    payload = dict(
        name="fixture_candidate",
        arrangement=arrangement,
        plan=PlanSpec(floor_area_m2=42.0, aspect_ratio=1.8, ceiling_height_m=2.7,
                      azimuth_deg=180.0),
        wall=wall_construction(),
        roof=roof_construction(),
        floor=floor_construction(),
        partition=partition_construction(),
        windows=windows,
        ventilation=VentilationSpec(infiltration_ach=0.5, minimum_fresh_air_ach=0.35,
                                    infiltration_range=UncertainRange(low=0.3,
                                                                      high=1.0)),
        occupancy=OccupancySpec(occupants=9, additional_moisture_g_h=400.0),
        pcm=PCMSpec(present=False),
        arrangement_params=ArrangementParams(trombe_glazing_id="single_clear_4mm",
                                             sunspace_glazing_id="single_clear_4mm"),
    )
    payload.update(overrides)
    return DesignSpec(**payload)


def site(epw_path: str = "leh.epw") -> SiteSpec:
    return SiteSpec(
        id="leh", name="Leh, Ladakh", latitude_deg=34.15, longitude_deg=77.57,
        elevation_m=3500.0, time_zone_h=5.5, epw_path=epw_path,
        horizon=HorizonProfile(
            azimuths_deg=tuple(float(i * 10) for i in range(36)),
            blocked_altitude_deg=tuple(
                8.0 + 6.0 * (1 if 90 <= i * 10 <= 270 else 0) for i in range(36)),
            source=SYNTHETIC),
        ground=GroundTemperatures(
            monthly_c=(-4.0, -3.0, 1.0, 6.0, 11.0, 15.0, 17.0, 17.0, 13.0, 7.0,
                       1.0, -3.0),
            source=SYNTHETIC),
        source=SYNTHETIC)


def comfort_model() -> ComfortModel:
    return ComfortModel(
        id="imac_test", name="Test adaptive model", intercept_c=12.83, slope=0.54,
        band_half_width_k=3.46,
        outdoor_applicability_c=UncertainRange(low=13.0, high=38.0),
        source=SYNTHETIC)


def fixed_target() -> FixedComfortTarget:
    return FixedComfortTarget(id="dihar_test", name="Test fixed target",
                              lower_bound_c=20.0, from_hour=18, until_hour=6,
                              source=SYNTHETIC)


def fuel_model() -> FuelModel:
    return FuelModel(fuel="diesel", unit="litre",
                     lower_heating_value_kwh_per_unit=10.0,
                     appliance_efficiency=0.75, price_inr_per_unit=95.0,
                     source=SYNTHETIC)


def winter_options(surface_days: int = 3) -> RunOptions:
    period = RunPeriod(start=date(2019, 1, 1), end=date(2019, 2, 28), label="winter")
    window = RunPeriod(start=date(2019, 1, 15),
                       end=date(2019, 1, 14 + surface_days), label="surface")
    return RunOptions(period=period, surface_output_period=window,
                      timestep_per_hour=6)
