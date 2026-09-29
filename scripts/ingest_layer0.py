from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine" / "src"))
sys.path.insert(0, str(ROOT / "api" / "src"))

from shelter_engine.materials import implausible, material_from_row  # noqa: E402
from shelter_engine.weather import load_epw, standard_pressure_pa  # noqa: E402

ASHRAE = "ASHRAE Handbook of Fundamentals 2025, chapter 26, material property tables"
LAYER0 = "Layer 0 delivery, {file}"

SUPPLEMENT_NOTE = (
    "Not present in the Layer 0 delivery. Added so the reference designs and the "
    "Ladakh construction set can be built. Replace with a Layer 0 record when one "
    "exists.")
COST_NOTE = (
    "Indicative delivered cost, not a measured property and not supplied by Layer 0. "
    "The Layer 0 material source file states these must be replaced with locally "
    "sourced values. Treat every rupee figure derived from them as provisional.")

# id, name, k, rho, cp, colour, finish, local, transport, skill, k_low, k_high
SUPPLEMENT: list[tuple] = [
    ("rammed_earth", "Rammed earth", 1.00, 1950, 880, "#b5754a", "rough",
     True, 1.0, "semiskilled", 0.60, 1.30),
    ("mud_brick", "Sun dried mud brick", 0.75, 1700, 880, "#b08a5f", "rough",
     True, 1.0, "semiskilled", 0.55, 0.95),
    ("mud_plaster", "Mud plaster", 0.60, 1600, 880, "#b59a72", "rough",
     True, 1.0, "unskilled", None, None),
    ("cement_plaster", "Cement plaster", 0.72, 1860, 840, "#c8c4bb", "smooth",
     True, 1.0, "semiskilled", None, None),
    ("stone_granite", "Local stone masonry", 2.80, 2600, 820, "#6f6f6f", "rough",
     True, 1.0, "skilled", 2.30, 3.40),
    ("eps_thermocol", "Expanded polystyrene", 0.038, 20, 1400, "#f2f2ea", "smooth",
     False, 2.5, "unskilled", 0.033, 0.043),
    ("xps_board", "Extruded polystyrene board", 0.034, 32, 1400, "#e8eef2", "smooth",
     False, 2.5, "unskilled", 0.030, 0.038),
    ("rockwool", "Rockwool slab", 0.040, 100, 840, "#d8cfc0", "rough",
     False, 2.5, "unskilled", 0.035, 0.046),
    ("gravel_bed", "Compacted gravel bed", 0.90, 1800, 840, "#8f8a7e", "rough",
     True, 1.0, "unskilled", None, None),
]

COSTS_INR_M3: dict[str, float] = {
    "rammed_earth": 2500.0, "mud_brick": 2200.0, "mud_plaster": 900.0,
    "cement_plaster": 4800.0, "stone_granite": 3000.0, "limestone": 3200.0,
    "clay_brick": 5200.0, "brick_solid": 5200.0, "concrete_dense": 6500.0,
    "glass_soda_lime": 12000.0, "timber_softwood": 32000.0,
    "eps_thermocol": 3200.0, "xps_board": 5200.0, "rockwool": 5200.0,
    "gi_sheet": 90000.0, "gravel_bed": 1200.0,
}

ORNL_METAL_ROOFS = (
    "Miller, Desjarlais, Parker and Kriner, The thermal performance of painted and "
    "unpainted structural standing seam metal roof systems, Thermal Performance of the "
    "Exterior Envelopes of Buildings VIII, ORNL 2001: unpainted galvanised steel, "
    "24 gauge, field exposed")
ORNL_URL = ("https://web.ornl.gov/sci/buildings/conf-archive/2001%20B8%20papers/"
            "115_Kriner.pdf")
IS_12436 = ("IS 12436 : 1988 with amendment, Preformed rigid polyurethane (PUR) and "
            "polyisocyanurate (PIR) foams for thermal insulation, Bureau of Indian "
            "Standards, Table 1 and Appendix A")
IS_12436_URL = "https://law.resource.org/pub/in/bis/S02/is.12436.1988.pdf"
PLUSS_OM18 = ("Pluss Advanced Technologies, technical data sheet of savE OM18, "
              "Doc 166, version R0, 15 February 2022")
PLUSS_OM18_URL = ("https://pluss.co.in/wp-content/themes/pluss/assets/image/"
                  "Literature-PLUSS/Data-Sheets/PCM/Doc%20166_PCM_OM18_TDS.pdf")

SOURCED: list[dict] = [
    {"id": "gi_sheet", "name": "Galvanised corrugated steel sheet, weathered",
     "conductivity_w_mk": 50.0, "density_kg_m3": 7800.0, "specific_heat_j_kgk": 480.0,
     "thermal_absorptance": 0.35, "solar_absorptance": 0.77,
     "colour": "#9ea7ad", "finish": "smooth", "locally_available": 0,
     "transport_multiplier": 2.0, "mason_skill": "semiskilled",
     "conductivity_low": None, "conductivity_high": None,
     "source": ORNL_METAL_ROOFS, "source_status": "verified", "url": ORNL_URL,
     "source_note": ("Surface optics are the weathered values: the bare sheet went dull "
                     "within 30 days of exposure, after which ORNL measured a solar "
                     "reflectance of 0.23 and a thermal emittance of 0.35, the highest "
                     "emittance any weathered sample reached. A roof in Ladakh is "
                     "weathered for all but its first month. Conductivity, density and "
                     "specific heat are carbon steel values, which a 0.6 mm sheet makes "
                     "irrelevant to the result.")},
    {"id": "gi_sheet_new", "name": "Galvanised corrugated steel sheet, new",
     "conductivity_w_mk": 50.0, "density_kg_m3": 7800.0, "specific_heat_j_kgk": 480.0,
     "thermal_absorptance": 0.06, "solar_absorptance": 0.34,
     "colour": "#c8d1d6", "finish": "smooth", "locally_available": 0,
     "transport_multiplier": 2.0, "mason_skill": "semiskilled",
     "conductivity_low": None, "conductivity_high": None,
     "source": ORNL_METAL_ROOFS, "source_status": "verified", "url": ORNL_URL,
     "source_note": ("New mirror finish, labelled R66E06 by ORNL: solar reflectance "
                     "0.66 and thermal emittance 0.06. It holds for weeks, not "
                     "winters, so it is only right for a roof sheeted this season.")},
    {"id": "puf_panel", "name": "Rigid polyurethane foam panel core",
     "conductivity_w_mk": 0.023, "density_kg_m3": 34.0, "specific_heat_j_kgk": 1400.0,
     "thermal_absorptance": None, "solar_absorptance": None,
     "colour": "#efe7c9", "finish": "smooth", "locally_available": 0,
     "transport_multiplier": 2.5, "mason_skill": "unskilled",
     "conductivity_low": 0.017, "conductivity_high": 0.025,
     "source": IS_12436, "source_status": "verified", "url": IS_12436_URL,
     "source_note": ("0.023 W/mK is the standard's maximum at a 10 C mean, the "
                     "temperature a shelter wall sits near; the range 0.017 to 0.025 is "
                     "the ageing drift the standard reports. 34 kg/m3 is the middle of "
                     "the 32 to 36 kg/m3 it calls suitable for general insulation. The "
                     "standard gives no specific heat; 1400 J/kgK is the polyurethane "
                     "value tabulated in ISO 10456, and at this density it stores almost "
                     "nothing either way. Unpriced until the UT Ladakh schedule of rates "
                     "is entered.")},
    {"id": "water_mass", "name": "Water in sealed containers",
     "conductivity_w_mk": 0.58, "density_kg_m3": 1000.0, "specific_heat_j_kgk": 4190.0,
     "thermal_absorptance": None, "solar_absorptance": None,
     "colour": "#5f8fb4", "finish": "smooth", "locally_available": 1,
     "transport_multiplier": 1.0, "mason_skill": "unskilled",
     "conductivity_low": None, "conductivity_high": None,
     "source": "Properties of liquid water near 5 C, IAPWS formulation",
     "source_status": "verified", "url": "http://www.iapws.org/relguide/IAPWS95-2018.pdf",
     "source_note": ("Modelled as a still solid layer, which understates the heat it "
                     "moves by convection inside the container. It is not modelled as "
                     "freezing: a container in a room that drops below 0 C will freeze, "
                     "burst and release its latent heat, none of which this row "
                     "captures, so it only belongs in a room that stays above 0 C.")},
]

SOURCED_PCM: list[dict] = [
    {"id": "om18", "name": "Pluss savE OM18", "melt_temperature_c": 18.0,
     "latent_heat_j_kg": 169000.0, "conductivity_w_mk": 0.18, "density_kg_m3": 880.0,
     "specific_heat_j_kgk": 2690.0, "melt_band_k": 2.0,
     "cost_inr_m3": 180000.0, "cycle_capacity_retention": 1.0,
     "colour": "#e6e1cf", "finish": "smooth",
     "source": (PLUSS_OM18 + ": melting 18 C and freezing 17 C, melting latent heat "
                "169 kJ/kg, liquid density 880 kg/m3, liquid specific heat 2.69 kJ/kgK, "
                "conductivity 0.175 to 0.182 W/mK. Flammable, flash point 150 C, so it "
                "must be encapsulated and kept away from a heater."),
     "source_status": "verified"},
]

# id, name, U, SHGC, VT, layers, cost/m2, local, colour
GLAZING: list[tuple] = [
    ("single_clear_4mm", "Single clear 4 mm", 5.80, 0.82, 0.88, 1, 900.0, True,
     "#cfe3ef"),
    ("double_clear_6_12_6", "Double clear 6-12-6 air", 2.70, 0.70, 0.78, 2, 3200.0,
     False, "#c6dfec"),
    ("double_lowe_argon", "Double low-e argon filled", 1.60, 0.58, 0.71, 2, 5400.0,
     False, "#bcd9e8"),
]
GLAZING_SOURCE = (
    "Centre of glass U-factor, solar heat gain coefficient and visible transmittance "
    "for standard vertical assemblies, ASHRAE Handbook of Fundamentals fenestration "
    "tables. Not supplied by Layer 0; substitute a manufacturer datasheet before use.")

PCM_COST_INR_M3 = 180000.0

MATERIAL_ID = {
    "Clay brick": "brick_solid",
    "Concrete": "concrete_dense",
    "Limestone": "limestone",
    "Glass (soda-lime)": "glass_soda_lime",
    "Eastern white pine": "timber_softwood",
}
MATERIAL_LOOK = {
    "brick_solid": ("#9c4a3a", "rough", True, 1.0, "semiskilled"),
    "concrete_dense": ("#8c8c8c", "rough", True, 1.0, "skilled"),
    "limestone": ("#a9a396", "rough", True, 1.0, "skilled"),
    "glass_soda_lime": ("#cfe3ef", "transparent", False, 2.0, "skilled"),
    "timber_softwood": ("#a3752f", "rough", False, 2.5, "skilled"),
}
# Values used when a Layer 0 row fails the physical plausibility check.
CORRECTIONS: dict[str, dict] = {
    "brick_solid": {
        "conductivity_w_mk": 0.90,
        "specific_heat_j_kgk": 800.0,
        "reason": ("The Layer 0 row gives k = 0.17 W/mK and cp = 330 to 440 J/kgK at "
                   "1980 kg/m3. Both fail a physical plausibility check: fired clay "
                   "brick at that density conducts near 0.9 W/mK and stores near "
                   "800 J/kgK. This is the baseline wall, so an implausibly low "
                   "conductivity would make current construction look insulated and "
                   "shrink every reported difference. ASHRAE values substituted; "
                   "confirm with Layer 0."),
    },
}

SCHEMA = """
CREATE TABLE materials (
    id TEXT PRIMARY KEY, name TEXT, conductivity_w_mk REAL, density_kg_m3 REAL,
    specific_heat_j_kgk REAL, thermal_absorptance REAL, solar_absorptance REAL,
    visible_absorptance REAL, roughness TEXT, cost_inr_m3 REAL,
    locally_available INTEGER, transport_multiplier REAL, mason_skill TEXT,
    colour TEXT, finish TEXT, conductivity_low REAL, conductivity_high REAL,
    source TEXT, source_status TEXT, source_note TEXT,
    cost_source TEXT, cost_source_status TEXT);
CREATE TABLE glazing_systems (
    id TEXT PRIMARY KEY, name TEXT, u_value_w_m2k REAL, shgc REAL,
    visible_transmittance REAL, layers INTEGER, frame_fraction REAL,
    cost_inr_m2 REAL, locally_available INTEGER, colour TEXT, finish TEXT,
    source TEXT, source_status TEXT, cost_source TEXT, cost_source_status TEXT);
CREATE TABLE phase_change_materials (
    id TEXT PRIMARY KEY, name TEXT, melt_temperature_c REAL, latent_heat_j_kg REAL,
    conductivity_w_mk REAL, density_kg_m3 REAL, specific_heat_j_kgk REAL,
    melt_band_k REAL, cost_inr_m3 REAL, cycle_capacity_retention REAL, colour TEXT,
    finish TEXT, source TEXT, source_status TEXT);
CREATE TABLE layer0_raw (
    table_name TEXT, row_json TEXT);
"""


def number(text: str) -> float:
    value = str(text).strip()
    if "-" in value and not value.startswith("-"):
        low, high = value.split("-", 1)
        return (float(low) + float(high)) / 2.0
    return float(value)


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = [row for row in csv.DictReader(handle)]
    header = set(rows[0]) if rows else set()
    return [row for row in rows
            if not all(str(row.get(key, "")).strip() == key for key in header)]


def soil_monthly(path: Path) -> tuple[float, ...] | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((index for index, line in enumerate(lines)
                  if line.startswith("PARAMETER,")), None)
    if start is None:
        return None
    totals = [0.0] * 12
    count = 0
    for line in lines[start + 1:]:
        parts = [item.strip() for item in line.split(",")]
        if len(parts) < 14 or parts[0] != "TSOIL1":
            continue
        try:
            values = [float(item) for item in parts[2:14]]
        except ValueError:
            continue
        if any(value <= -900 for value in values):
            continue
        totals = [total + value for total, value in zip(totals, values)]
        count += 1
    if count == 0:
        return None
    return tuple(round(total / count, 2) for total in totals)


def epw_ground_temperatures(path: Path) -> tuple[float, ...] | None:
    for line in path.read_text(encoding="latin-1").splitlines()[:8]:
        if not line.upper().startswith("GROUND TEMPERATURES"):
            continue
        parts = [item.strip() for item in line.split(",")]
        try:
            depths = int(parts[1])
        except (IndexError, ValueError):
            return None
        cursor = 2
        for _ in range(depths):
            block = parts[cursor:cursor + 16]
            if len(block) < 16:
                return None
            try:
                values = tuple(float(item) for item in block[4:16])
            except ValueError:
                cursor += 16
                continue
            return values
        return None
    return None


def horizon(path: Path) -> tuple[tuple[float, ...], tuple[float, ...]]:
    rows = read_csv(path)
    pairs = sorted(
        (float(row["azimuth_deg"]) % 360.0, float(row["max_blocked_angle_deg"]))
        for row in rows)
    return tuple(item[0] for item in pairs), tuple(item[1] for item in pairs)


def build_materials(raw: list[dict], report: list[str]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for row in raw:
        name = str(row["material"]).strip()
        identifier = MATERIAL_ID.get(name)
        if identifier is None:
            report.append(f"  ! unmapped Layer 0 material {name!r}, skipped")
            continue
        colour, finish, local, transport, skill = MATERIAL_LOOK[identifier]
        record = {
            "id": identifier, "name": name,
            "conductivity_w_mk": number(row["k_W_mK"]),
            "density_kg_m3": number(row["density_kg_m3"]),
            "specific_heat_j_kgk": number(row["Cp_J_kgK"]),
            "colour": colour, "finish": finish,
            "locally_available": int(local), "transport_multiplier": transport,
            "mason_skill": skill,
            "cost_inr_m3": COSTS_INR_M3.get(identifier, 0.0),
            "source": f"{row['source']} (Layer 0 materials.csv)",
            "source_status": "verified",
            "source_note": COST_NOTE,
            "conductivity_low": None, "conductivity_high": None,
            "from_layer0": True,
        }
        probe = material_from_row(record)
        problems = implausible(probe)
        if problems:
            fix = CORRECTIONS.get(identifier)
            if fix is None:
                report.append(
                    f"  !! {identifier} fails the plausibility check and has no "
                    f"correction: {'; '.join(problems)}")
            else:
                report.append(f"  !! {identifier} corrected. {fix['reason']}")
                report.append(
                    f"     Layer 0 gave k={record['conductivity_w_mk']} "
                    f"cp={record['specific_heat_j_kgk']}; using "
                    f"k={fix['conductivity_w_mk']} cp={fix['specific_heat_j_kgk']}")
                record["conductivity_w_mk"] = fix["conductivity_w_mk"]
                record["specific_heat_j_kgk"] = fix["specific_heat_j_kgk"]
                record["source"] = ASHRAE
                record["source_status"] = "pending"
                record["source_note"] = fix["reason"]
        out.append(record)
        seen.add(identifier)

    for record in SOURCED:
        if record["id"] in seen:
            continue
        out.append({**record, "cost_inr_m3": COSTS_INR_M3.get(record["id"], 0.0),
                    "from_layer0": False})
        seen.add(record["id"])

    for (identifier, name, k, rho, cp, colour, finish, local, transport, skill,
         k_low, k_high) in SUPPLEMENT:
        if identifier in seen:
            continue
        out.append({
            "id": identifier, "name": name, "conductivity_w_mk": k,
            "density_kg_m3": rho, "specific_heat_j_kgk": cp,
            "colour": colour, "finish": finish,
            "locally_available": int(local), "transport_multiplier": transport,
            "mason_skill": skill,
            "cost_inr_m3": COSTS_INR_M3.get(identifier, 0.0),
            "source": ASHRAE, "source_status": "pending",
            "source_note": SUPPLEMENT_NOTE + " " + COST_NOTE,
            "conductivity_low": k_low, "conductivity_high": k_high,
            "from_layer0": False,
        })
    return out


def build_pcm(raw: list[dict]) -> list[dict]:
    out = []
    for row in raw:
        product = str(row["pcm_product"]).strip()
        if not product or product == "pcm_product":
            continue
        out.append({
            "id": product.lower(), "name": f"Rubitherm {product}",
            "melt_temperature_c": number(row["melting_peak_C"]),
            "latent_heat_j_kg": number(row["heat_storage_kJ_kg"]) * 1000.0,
            "conductivity_w_mk": number(row["k_W_mK"]),
            "density_kg_m3": number(row["density_solid_kg_m3"]),
            "specific_heat_j_kgk": number(row["specific_heat_kJ_kgK"]) * 1000.0,
            "melt_band_k": 4.0, "cost_inr_m3": PCM_COST_INR_M3,
            "cycle_capacity_retention": 1.0,
            "colour": "#e8e2c8", "finish": "smooth",
            "source": f"{row['source']} (Layer 0 pcm.csv)",
            "source_status": "verified",
        })
    known = {record["id"] for record in out}
    out.extend(record for record in SOURCED_PCM if record["id"] not in known)
    return out


def write_database(path: Path, materials: list[dict], pcm: list[dict],
                   raw_materials: list[dict], raw_pcm: list[dict]) -> None:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    for record in materials:
        connection.execute(
            "INSERT INTO materials (id, name, conductivity_w_mk, density_kg_m3, "
            "specific_heat_j_kgk, thermal_absorptance, solar_absorptance, roughness, "
            "cost_inr_m3, locally_available, "
            "transport_multiplier, mason_skill, colour, finish, conductivity_low, "
            "conductivity_high, source, source_status, source_note, "
            "cost_source, cost_source_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record["id"], record["name"], record["conductivity_w_mk"],
             record["density_kg_m3"], record["specific_heat_j_kgk"],
             record.get("thermal_absorptance"), record.get("solar_absorptance"),
             "MediumRough",
             record["cost_inr_m3"], record["locally_available"],
             record["transport_multiplier"], record["mason_skill"], record["colour"],
             record["finish"], record["conductivity_low"], record["conductivity_high"],
             record["source"], record["source_status"], record["source_note"],
             COST_NOTE, "pending"))
    for (identifier, name, u, shgc, vt, layers, cost, local, colour) in GLAZING:
        connection.execute(
            "INSERT INTO glazing_systems VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (identifier, name, u, shgc, vt, layers, 0.15, cost, int(local), colour,
             "transparent", GLAZING_SOURCE, "pending", COST_NOTE, "pending"))
    for record in pcm:
        connection.execute(
            "INSERT INTO phase_change_materials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record["id"], record["name"], record["melt_temperature_c"],
             record["latent_heat_j_kg"], record["conductivity_w_mk"],
             record["density_kg_m3"], record["specific_heat_j_kgk"],
             record["melt_band_k"], record["cost_inr_m3"],
             record["cycle_capacity_retention"], record["colour"], record["finish"],
             record["source"], record["source_status"]))
    for row in raw_materials:
        connection.execute("INSERT INTO layer0_raw VALUES (?,?)",
                           ("materials", json.dumps(row)))
    for row in raw_pcm:
        connection.execute("INSERT INTO layer0_raw VALUES (?,?)",
                           ("pcm", json.dumps(row)))
    connection.commit()
    connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest the Layer 0 delivery into the shared data directory the "
                    "API reads. Everything written here is traceable to a file in "
                    "the delivery or to a documented substitution.")
    parser.add_argument("--source", required=True,
                        help="the extracted layer_0 directory")
    parser.add_argument("--out", default=str(ROOT / "data"))
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_dir():
        print(f"error: {source} is not a directory", file=sys.stderr)
        return 2
    data = Path(args.out)
    report: list[str] = []

    epw_files = sorted(source.glob("weather/*.epw"))
    if not epw_files:
        print("error: no EPW in the delivery", file=sys.stderr)
        return 2
    epw_source = epw_files[0]
    weather_dir = data / "weather"
    weather_dir.mkdir(parents=True, exist_ok=True)
    epw = weather_dir / epw_source.name
    shutil.copy2(epw_source, epw)
    weather = load_epw(epw)
    header = weather.header
    print(f"weather: {epw.name}")
    print(f"  {header.city} {header.country}  {header.latitude_deg}, "
          f"{header.longitude_deg}  {header.elevation_m} m  tz {header.time_zone_h}")
    print(f"  {weather.hours} rows, mean {weather.annual_mean_c():.2f} C, "
          f"HDD18 {weather.heating_degree_days():.0f}, "
          f"CDD18 {weather.cooling_degree_days():.0f}")
    observed = float(weather.frame["pressure_pa"].mean())
    expected = standard_pressure_pa(header.elevation_m)
    print(f"  station pressure {observed:.0f} Pa against {expected:.0f} Pa expected "
          f"at {header.elevation_m:.0f} m, {abs(observed - expected) / expected * 100:.2f} "
          "percent apart")
    for item in weather.warnings:
        print(f"  ! [{item.code}] {item.message}")

    raw_materials = read_csv(source / "materials" / "materials.csv")
    raw_pcm = read_csv(source / "pcm" / "pcm.csv")
    print(f"\nmaterials: {len(raw_materials)} rows from Layer 0")
    materials = build_materials(raw_materials, report)
    pcm = build_pcm(raw_pcm)
    for line in report:
        print(line)
    supplied = {record["id"] for record in materials if record["from_layer0"]}
    print(f"  {len(supplied)} from Layer 0, "
          f"{len(materials) - len(supplied)} added to complete the set")
    sourced_pcm = {record["id"] for record in SOURCED_PCM}
    layer0_pcm = [record for record in pcm if record["id"] not in sourced_pcm]
    print(f"phase change materials: {len(layer0_pcm)} from Layer 0, "
          f"{len(pcm) - len(layer0_pcm)} added from manufacturer datasheets")
    print(f"glazing systems: {len(GLAZING)} added, none supplied by Layer 0")

    write_database(data / "materials.db", materials, pcm, raw_materials, raw_pcm)

    azimuths, blocked = horizon(source / "terrain" / "horizon_36dir.csv")
    print(f"\nterrain: {len(azimuths)} horizon directions, "
          f"blocked {min(blocked):.1f} to {max(blocked):.1f} degrees")
    obj = source / "terrain" / "terrain_mesh.obj"
    terrain_dir = data / "terrain"
    terrain_dir.mkdir(parents=True, exist_ok=True)
    if (terrain_dir / "leh.obj").is_file():
        print("  kept leh.obj, computed at the site; Layer 0's terrain_mesh.obj was "
              "computed about 3 km away and is not copied")
    elif obj.is_file():
        shutil.copy2(obj, terrain_dir / "terrain_mesh.obj")
        print(f"  copied {obj.name}")

    epw_ground = epw_ground_temperatures(epw)
    soil = soil_monthly(
        source / "ground_temperature" / "soil_temperature_TSOIL1_2015_2024.csv")
    if epw_ground:
        ground = epw_ground
        ground_source = (
            "Undisturbed earth temperature at 0.5 m from the EPW GROUND TEMPERATURES "
            "header, calculated from this weather file")
        ground_note = (
            "NASA POWER TSOIL1 for 2015 to 2024 is also in the Layer 0 delivery and "
            "gives "
            + (", ".join(f"{value:.1f}" for value in soil) if soil else "no usable rows")
            + " C. It was not used because it is a MERRA-2 reanalysis cell whose "
              "grid elevation is 4533 m against the 3256 m of the site, so it does "
              "not describe this ground. The EPW header values are derived from the "
              "same weather year the simulation runs on, so they stay consistent "
              "with it.")
    elif soil:
        ground = soil
        ground_source = ("NASA POWER TSOIL1 monthly soil temperature, 2015 to 2024 "
                         "mean (Layer 0 ground_temperature)")
        ground_note = "The EPW carried no ground temperature header."
    else:
        ground = None
        ground_source = ground_note = ""
    if ground:
        print(f"\nground temperatures at 0.5 m: "
              f"{', '.join(f'{value:.1f}' for value in ground)} C")

    site = {
        "id": "leh",
        "name": f"{header.city}, Ladakh",
        "latitude_deg": header.latitude_deg,
        "longitude_deg": header.longitude_deg,
        "elevation_m": header.elevation_m,
        "time_zone_h": header.time_zone_h,
        "epw_path": str(epw.relative_to(data).as_posix()),
        "climate_objective": "heating_dominated",
        "terrain": "Country",
        "horizon": {
            "azimuths_deg": list(azimuths),
            "blocked_altitude_deg": list(blocked),
            "source": {
                "citation": ("36 direction horizon profile generated from USGS SRTM "
                             "1 arc-second elevation data (Layer 0 terrain)"),
                "status": "verified",
            },
        },
        "source": {
            "citation": (f"EPW {epw.name} from Climate.OneBuilding.org, "
                         f"source tag {header.source}"),
            "status": "verified",
            "note": ("Weather, terrain and ground temperatures all come from the "
                     "Layer 0 delivery."),
        },
    }
    if ground:
        site["ground"] = {
            "monthly_c": list(ground),
            "depth_m": 0.5,
            "source": {"citation": ground_source, "status": "verified",
                       "note": ground_note},
        }

    sites = data / "sites"
    sites.mkdir(parents=True, exist_ok=True)
    target = sites / "leh.json"
    if target.is_file():
        existing = json.loads(target.read_text(encoding="utf-8"))
        kept_horizon = existing.get("horizon") or {}
        citation = str((kept_horizon.get("source") or {}).get("citation", ""))
        if "computed at the site coordinates" in citation:
            site["horizon"] = kept_horizon
            print("  kept the horizon already computed at the site coordinates; "
                  "Layer 0's table was computed about 3 km away")
    target.write_text(json.dumps(site, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "generated_from": str(source),
        "weather_file": epw.name,
        "weather_source": header.source,
        "materials_from_layer0": sorted(supplied),
        "materials_added": sorted({record["id"] for record in materials}
                                  - supplied),
        "glazing_added": [item[0] for item in GLAZING],
        "pcm_from_layer0": [record["id"] for record in pcm
                            if record["id"] not in {item["id"] for item in SOURCED_PCM}],
        "pcm_added": [item["id"] for item in SOURCED_PCM],
        "corrections": {
            key: value["reason"] for key, value in CORRECTIONS.items()
            if key in supplied
        },
        "costs": COST_NOTE,
        "absorptances": (
            "Layer 0 supplied no thermal, solar or visible absorptances. Every "
            "material carries the schema defaults of 0.9 thermal and 0.7 solar. "
            "Surface colour changes solar gain on an exposed wall by a large "
            "fraction, so a painted absorber or a whitewashed roof needs its own "
            "measured value before it is reported."),
        "ground_temperature_source": ground_source,
        "ground_temperature_note": ground_note,
    }
    (data / "layer0_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nwrote {data / 'materials.db'}, {sites / 'leh.json'} and "
          f"{data / 'layer0_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
