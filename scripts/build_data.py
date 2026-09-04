from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine" / "tests"))

import fixtures  # noqa: E402
from synthetic_epw import write_synthetic_epw  # noqa: E402

from shelter_engine import Engine, MaterialLibrary  # noqa: E402
from shelter_engine.baseline import load_references  # noqa: E402
from shelter_engine.cfd import write_cfd_export  # noqa: E402
from shelter_engine.config import energyplus_available  # noqa: E402
from shelter_engine.schema import (Arrangement, ClimateObjective,  # noqa: E402
                                   RunOptions, RunPeriod, SiteSpec)

BANNER = (
    "SYNTHETIC DEVELOPMENT DATA. The weather years are generated from a sinusoid and a "
    "clear sky model. Every material property is a placeholder carrying a pending "
    "source. Layer 0 replaces all of it with a real EPW and a cited material database. "
    "Nothing produced from these files is a result and none of it may be shown to a "
    "jury.")

WINTER = RunPeriod(start=date(2019, 1, 1), end=date(2019, 1, 31), label="january")
SUMMER = RunPeriod(start=date(2019, 6, 1), end=date(2019, 6, 30), label="june")
SURFACE_WINTER = RunPeriod(start=date(2019, 1, 14), end=date(2019, 1, 16),
                           label="surface")
SURFACE_SUMMER = RunPeriod(start=date(2019, 6, 14), end=date(2019, 6, 16),
                           label="surface")


def write_materials(path: Path) -> None:
    library = fixtures.material_library()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE materials (
            id TEXT PRIMARY KEY, name TEXT, conductivity_w_mk REAL,
            density_kg_m3 REAL, specific_heat_j_kgk REAL, thermal_absorptance REAL,
            solar_absorptance REAL, visible_absorptance REAL, roughness TEXT,
            cost_inr_m3 REAL, locally_available INTEGER, transport_multiplier REAL,
            mason_skill TEXT, colour TEXT, finish TEXT, conductivity_low REAL,
            conductivity_high REAL, source TEXT, source_status TEXT, source_note TEXT);
        CREATE TABLE glazing_systems (
            id TEXT PRIMARY KEY, name TEXT, u_value_w_m2k REAL, shgc REAL,
            visible_transmittance REAL, layers INTEGER, frame_fraction REAL,
            cost_inr_m2 REAL, locally_available INTEGER, colour TEXT, finish TEXT,
            source TEXT, source_status TEXT);
        CREATE TABLE phase_change_materials (
            id TEXT PRIMARY KEY, name TEXT, melt_temperature_c REAL,
            latent_heat_j_kg REAL, conductivity_w_mk REAL, density_kg_m3 REAL,
            specific_heat_j_kgk REAL, melt_band_k REAL, cost_inr_m3 REAL,
            cycle_capacity_retention REAL, colour TEXT, finish TEXT, source TEXT,
            source_status TEXT);
    """)
    for material in library.materials:
        span = material.conductivity_range
        connection.execute(
            "INSERT INTO materials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (material.id, material.name, material.conductivity_w_mk,
             material.density_kg_m3, material.specific_heat_j_kgk,
             material.thermal_absorptance, material.solar_absorptance,
             material.visible_absorptance, material.roughness, material.cost_inr_m3,
             int(material.locally_available), material.transport_multiplier,
             material.mason_skill, material.appearance.colour,
             material.appearance.finish, span.low if span else None,
             span.high if span else None, material.source.citation,
             material.source.status.value, BANNER))
    for system in library.glazing_systems:
        connection.execute(
            "INSERT INTO glazing_systems VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (system.id, system.name, system.u_value_w_m2k, system.shgc,
             system.visible_transmittance, system.layers, system.frame_fraction,
             system.cost_inr_m2, int(system.locally_available),
             system.appearance.colour, system.appearance.finish,
             system.source.citation, system.source.status.value))
    for pcm in library.phase_change_materials:
        connection.execute(
            "INSERT INTO phase_change_materials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pcm.id, pcm.name, pcm.melt_temperature_c, pcm.latent_heat_j_kg,
             pcm.conductivity_w_mk, pcm.density_kg_m3, pcm.specific_heat_j_kgk,
             pcm.melt_band_k, pcm.cost_inr_m3, pcm.cycle_capacity_retention,
             pcm.appearance.colour, pcm.appearance.finish, pcm.source.citation,
             pcm.source.status.value))
    connection.commit()
    connection.close()


def build_sites(data: Path) -> dict[str, SiteSpec]:
    weather = data / "weather"
    weather.mkdir(parents=True, exist_ok=True)
    sites_dir = data / "sites"
    sites_dir.mkdir(parents=True, exist_ok=True)

    leh_epw = write_synthetic_epw(weather / "synthetic_leh.epw", latitude_deg=34.15,
                                  longitude_deg=77.57, time_zone_h=5.5,
                                  elevation_m=3500.0, annual_mean_c=6.0,
                                  annual_swing_c=14.0, diurnal_swing_c=14.0,
                                  city="SYNTHETIC-LEH")
    jaisalmer_epw = write_synthetic_epw(
        weather / "synthetic_jaisalmer.epw", latitude_deg=26.92, longitude_deg=70.92,
        time_zone_h=5.5, elevation_m=225.0, annual_mean_c=26.0, annual_swing_c=10.0,
        diurnal_swing_c=15.0, city="SYNTHETIC-JAISALMER")

    leh = fixtures.site(str(leh_epw.relative_to(data).as_posix()))
    jaisalmer = SiteSpec(
        id="jaisalmer", name="Jaisalmer, Rajasthan", latitude_deg=26.92,
        longitude_deg=70.92, elevation_m=225.0, time_zone_h=5.5,
        epw_path=str(jaisalmer_epw.relative_to(data).as_posix()),
        climate_objective=ClimateObjective.COOLING_DOMINATED, terrain="Country",
        ground=fixtures.site().ground.model_copy(update={
            "monthly_c": (18.0, 21.0, 26.0, 31.0, 34.0, 35.0, 33.0, 31.0, 30.0,
                          27.0, 22.0, 18.0)}),
        source=fixtures.SYNTHETIC)

    sites = {"leh": leh, "jaisalmer": jaisalmer}
    for identifier, site in sites.items():
        (sites_dir / f"{identifier}.json").write_text(
            json.dumps(site.model_dump(mode="json"), indent=2), encoding="utf-8")
    return sites


def write_designs(data: Path) -> None:
    designs = data / "designs"
    designs.mkdir(parents=True, exist_ok=True)
    for arrangement in Arrangement:
        design = fixtures.design(arrangement).variant(
            name=f"candidate_{arrangement.value}")
        (designs / f"{arrangement.value}.json").write_text(
            json.dumps(design.model_dump(mode="json"), indent=2), encoding="utf-8")
    for reference in load_references().designs:
        (designs / f"reference_{reference.name}.json").write_text(
            json.dumps(reference.model_dump(mode="json"), indent=2), encoding="utf-8")


def precompute(data: Path, sites: dict[str, SiteSpec], quick: bool) -> None:
    library = MaterialLibrary.load(data / "materials.db")
    cfd_dir = data / "cfd"
    cfd_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "api" / "src"))
    from shelter_api.store import Store

    store = Store(data / "runs.db")
    for identifier, site in sites.items():
        absolute = site.model_copy(update={
            "epw_path": str((data / site.epw_path).resolve())})
        engine = Engine(absolute, library, fuel="diesel")
        cooling = site.climate_objective is ClimateObjective.COOLING_DOMINATED
        options = RunOptions(
            period=SUMMER if cooling else WINTER,
            surface_output_period=SURFACE_SUMMER if cooling else SURFACE_WINTER,
            conditioned_run=True, surface_output=True)
        designs = [load_references().default,
                   fixtures.design(Arrangement.SINGLE_ROOM).variant(
                       name="candidate_single_room")]
        if not quick:
            designs.append(fixtures.design(Arrangement.SUNSPACE).variant(
                name="candidate_sunspace"))
        for design in designs:
            result = engine.simulate(design, options,
                                     with_uncertainty=not quick)
            store.save_run(result)
            print(f"  {identifier}: {design.name} "
                  f"min overnight "
                  f"{result.comfort.minimum_overnight_operative_c:.1f} C")
        if identifier == "leh":
            for export in engine.cfd_export(designs[-1], options):
                path = write_cfd_export(export, cfd_dir)
                print(f"  {identifier}: fluid dynamics boundary conditions "
                      f"{path.name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the shared data directory the API reads. Generates "
                    "synthetic development inputs and, unless told not to, "
                    "precomputes a few results so a cold start shows something "
                    "immediately.")
    parser.add_argument("--out", default=str(ROOT / "data"))
    parser.add_argument("--no-precompute", action="store_true")
    parser.add_argument("--quick", action="store_true",
                        help="fewer designs and no uncertainty sweep")
    args = parser.parse_args()

    data = Path(args.out)
    data.mkdir(parents=True, exist_ok=True)
    (data / "validation").mkdir(parents=True, exist_ok=True)

    print(BANNER, "\n")
    write_materials(data / "materials.db")
    sites = build_sites(data)
    write_designs(data)
    (data / "README.txt").write_text(BANNER + "\n", encoding="utf-8")
    print(f"wrote inputs to {data}")

    if args.no_precompute:
        print("\nskipped precompute")
        return 0
    if not energyplus_available():
        print("\nEnergyPlus not found, so nothing was precomputed. "
              "Set ENERGYPLUS_DIR and run again.")
        return 0
    print("\nprecomputing so a cold start has something to show:")
    precompute(data, sites, args.quick)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
