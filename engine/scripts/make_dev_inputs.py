from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import fixtures
from synthetic_epw import write_synthetic_epw

from shelter_engine.schema import Arrangement

BANNER = ("SYNTHETIC DEVELOPMENT INPUTS. The weather year is generated from a "
          "sinusoid and a clear sky model, and every material property is a made up "
          "placeholder with a pending source. Layer 0 replaces all of this. Nothing "
          "produced from these files may be shown to anyone as a result.")


def write_material_database(path: Path) -> None:
    library = fixtures.material_library()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE materials (id TEXT PRIMARY KEY, name TEXT, "
        "conductivity_w_mk REAL, density_kg_m3 REAL, specific_heat_j_kgk REAL, "
        "thermal_absorptance REAL, solar_absorptance REAL, visible_absorptance REAL, "
        "roughness TEXT, cost_inr_m3 REAL, locally_available INTEGER, "
        "transport_multiplier REAL, mason_skill TEXT, colour TEXT, finish TEXT, "
        "conductivity_low REAL, conductivity_high REAL, source TEXT, "
        "source_status TEXT, source_note TEXT)")
    connection.execute(
        "CREATE TABLE glazing_systems (id TEXT PRIMARY KEY, name TEXT, "
        "u_value_w_m2k REAL, shgc REAL, visible_transmittance REAL, layers INTEGER, "
        "frame_fraction REAL, cost_inr_m2 REAL, locally_available INTEGER, "
        "colour TEXT, finish TEXT, source TEXT, source_status TEXT)")
    connection.execute(
        "CREATE TABLE phase_change_materials (id TEXT PRIMARY KEY, name TEXT, "
        "melt_temperature_c REAL, latent_heat_j_kg REAL, conductivity_w_mk REAL, "
        "density_kg_m3 REAL, specific_heat_j_kgk REAL, melt_band_k REAL, "
        "cost_inr_m3 REAL, cycle_capacity_retention REAL, colour TEXT, finish TEXT, "
        "source TEXT, source_status TEXT)")

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
             material.appearance.finish,
             span.low if span else None, span.high if span else None,
             material.source.citation, material.source.status.value, BANNER))
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate synthetic development inputs so the engine can be run "
                    "before Layer 0 delivers the real weather file and material "
                    "database. These are not results and must never be presented.")
    parser.add_argument("--out", default=str(ROOT / "build" / "dev"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    epw = write_synthetic_epw(out / "synthetic_leh.epw")
    write_material_database(out / "materials.db")

    site = fixtures.site(str(epw))
    (out / "site.json").write_text(
        json.dumps(site.model_dump(mode="json"), indent=2), encoding="utf-8")

    for arrangement in Arrangement:
        design = fixtures.design(arrangement).variant(
            name=f"candidate_{arrangement.value}")
        (out / f"{arrangement.value}.json").write_text(
            json.dumps(design.model_dump(mode="json"), indent=2), encoding="utf-8")

    (out / "README.txt").write_text(BANNER + "\n", encoding="utf-8")
    print(BANNER)
    print(f"\nwrote development inputs to {out}")
    for item in sorted(out.iterdir()):
        print(f"  {item.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
