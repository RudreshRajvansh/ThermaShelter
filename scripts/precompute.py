from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine" / "src"))
sys.path.insert(0, str(ROOT / "api" / "src"))

from shelter_api.store import Store  # noqa: E402
from shelter_engine import Engine, MaterialLibrary  # noqa: E402
from shelter_engine.baseline import (check_library_covers_references,  # noqa: E402
                                     load_references)
from shelter_engine.cfd import write_cfd_export  # noqa: E402
from shelter_engine.config import energyplus_available  # noqa: E402
from shelter_engine.programme import occupancy_for, plan_for  # noqa: E402
from shelter_engine.schema import (Arrangement, ArrangementParams,  # noqa: E402
                                   ClimateObjective, ConstructionSpec, DesignSpec,
                                   Facade, LayerSpec, PCMSpec, RunOptions,
                                   RunPeriod, ShelterUse, ShutterSpec, SiteSpec,
                                   UncertainRange, VentilationSpec, WindowSpec)


def wall(mass_m: float, insulation_m: float) -> ConstructionSpec:
    return ConstructionSpec(name="rammed_earth_insulated", layers=(
        LayerSpec(material_id="cement_plaster", thickness_m=0.020),
        LayerSpec(material_id="eps_thermocol", thickness_m=insulation_m),
        LayerSpec(material_id="rammed_earth", thickness_m=mass_m),
        LayerSpec(material_id="mud_plaster", thickness_m=0.020)))


def roof(insulation_m: float) -> ConstructionSpec:
    return ConstructionSpec(name="insulated_light_roof", layers=(
        LayerSpec(material_id="gi_sheet", thickness_m=0.0006),
        LayerSpec(material_id="rockwool", thickness_m=insulation_m),
        LayerSpec(material_id="timber_softwood", thickness_m=0.025)))


def floor() -> ConstructionSpec:
    return ConstructionSpec(name="insulated_slab_on_gravel", layers=(
        LayerSpec(material_id="gravel_bed", thickness_m=0.200),
        LayerSpec(material_id="xps_board", thickness_m=0.060),
        LayerSpec(material_id="concrete_dense", thickness_m=0.100)))


def partition() -> ConstructionSpec:
    return ConstructionSpec(name="mud_brick_partition", layers=(
        LayerSpec(material_id="mud_plaster", thickness_m=0.015),
        LayerSpec(material_id="mud_brick", thickness_m=0.150),
        LayerSpec(material_id="mud_plaster", thickness_m=0.015)))


def candidate(name: str, use: ShelterUse, arrangement: Arrangement,
              azimuth_deg: float = 180.0) -> DesignSpec:
    plan = plan_for(use, azimuth_deg=azimuth_deg)
    windows = {
        Facade.SOUTH: WindowSpec(
            wwr=0.32, glazing_id="double_lowe_argon", sill_height_m=0.60,
            overhang_depth_m=0.45,
            shutter=ShutterSpec(present=True, thermal_resistance_m2k_w=0.40)),
        Facade.EAST: WindowSpec(wwr=0.05, glazing_id="double_clear_6_12_6"),
        Facade.WEST: WindowSpec(wwr=0.05, glazing_id="double_clear_6_12_6"),
    }
    if arrangement is Arrangement.TROMBE_WALL:
        windows.pop(Facade.SOUTH)
    return DesignSpec(
        name=name, arrangement=arrangement, plan=plan,
        wall=wall(0.40, 0.10), roof=roof(0.16), floor=floor(),
        partition=partition(), windows=windows,
        ventilation=VentilationSpec(
            infiltration_ach=0.5, minimum_fresh_air_ach=0.35,
            infiltration_range=UncertainRange(low=0.30, high=1.00)),
        occupancy=occupancy_for(use), pcm=PCMSpec(present=False),
        arrangement_params=ArrangementParams(
            sunspace_glazing_id="single_clear_4mm",
            trombe_glazing_id="single_clear_4mm"))


def _c(value: float | None) -> str:
    return "    n/a" if value is None else f"{value:5.1f} C"


def _kwh(value: float | None) -> str:
    return "withheld" if value is None else f"{value:8.0f} kWh"


def periods(objective: ClimateObjective) -> tuple[RunOptions, str]:
    if objective is ClimateObjective.COOLING_DOMINATED:
        return RunOptions(
            period=RunPeriod(start=date(2019, 6, 1), end=date(2019, 6, 30),
                             label="june"),
            surface_output_period=RunPeriod(start=date(2019, 6, 14),
                                            end=date(2019, 6, 16), label="surface"),
            conditioned_run=True, surface_output=True), "june"
    return RunOptions(
        period=RunPeriod(start=date(2019, 1, 1), end=date(2019, 1, 31),
                         label="january"),
        surface_output_period=RunPeriod(start=date(2019, 1, 14),
                                        end=date(2019, 1, 16), label="surface"),
        conditioned_run=True, surface_output=True), "january"


def load_sites(data: Path) -> dict[str, SiteSpec]:
    out: dict[str, SiteSpec] = {}
    for path in sorted((data / "sites").glob("*.json")):
        site = SiteSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
        out[site.id] = site.model_copy(update={
            "epw_path": str((data / site.epw_path).resolve())})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the whole cycle once against whatever is in the data "
                    "directory, and leave the results in the run store so the "
                    "first screen a judge opens is already populated.")
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--quick", action="store_true",
                        help="one candidate per site and no uncertainty sweep")
    parser.add_argument("--uses", default="barrack,medical_post",
                        help="comma separated shelter uses to precompute")
    args = parser.parse_args()

    data = Path(args.data)
    if not (data / "materials.db").is_file():
        print(f"error: no material database in {data}. Run scripts/ingest_layer0.py "
              "first.", file=sys.stderr)
        return 2
    if not energyplus_available():
        print("error: EnergyPlus was not found. Set ENERGYPLUS_DIR to the directory "
              "holding energyplus and Energy+.idd. Nothing is precomputed without "
              "it, and no result is invented in its place.", file=sys.stderr)
        return 2

    library = MaterialLibrary.load(data / "materials.db")
    references = load_references()
    missing = check_library_covers_references(library, references)
    if missing:
        for warning in missing:
            print(f"  ! [{warning.code}] {warning.message}")
        return 2

    sites = load_sites(data)
    if not sites:
        print(f"error: no site descriptions in {data / 'sites'}", file=sys.stderr)
        return 2

    designs_dir = data / "designs"
    designs_dir.mkdir(parents=True, exist_ok=True)
    for reference in references.designs:
        (designs_dir / f"reference_{reference.name}.json").write_text(
            json.dumps(reference.model_dump(mode="json"), indent=2), encoding="utf-8")

    uses = [ShelterUse(item.strip()) for item in args.uses.split(",") if item.strip()]
    arrangements = ([Arrangement.SUNSPACE] if args.quick
                    else [Arrangement.SINGLE_ROOM, Arrangement.SUNSPACE,
                          Arrangement.TROMBE_WALL])

    store = Store(data / "runs.db")
    cfd_dir = data / "cfd"
    cfd_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    started = time.perf_counter()

    for identifier, site in sites.items():
        engine = Engine(site, library, fuel="diesel")
        options, label = periods(site.climate_objective)
        print(f"\n{site.name}  {label}  "
              f"{site.latitude_deg:.3f}, {site.longitude_deg:.3f}  "
              f"{site.elevation_m:.0f} m")

        queue: list[DesignSpec] = [references.default]
        for use in uses:
            for arrangement in arrangements:
                queue.append(candidate(f"{use.value}_{arrangement.value}", use,
                                       arrangement))

        canonical: set[Arrangement] = set()
        for design in queue:
            payload = json.dumps(design.model_dump(mode="json"), indent=2)
            (designs_dir / f"{design.name}.json").write_text(payload, encoding="utf-8")
            if design.arrangement not in canonical and design is not references.default:
                (designs_dir / f"{design.arrangement.value}.json").write_text(
                    payload, encoding="utf-8")
                canonical.add(design.arrangement)
            result = engine.simulate(design, options,
                                     with_uncertainty=not args.quick,
                                     with_reference=design is not references.default)
            store.save_run(result)
            total += 1
            comfort = result.comfort
            print(f"  {design.name:34s} "
                  f"min overnight "
                  f"{_c(comfort.minimum_overnight_operative_c)}   "
                  f"max daytime {_c(comfort.maximum_daytime_operative_c)}   "
                  f"below band {comfort.hours_below_band:6.0f} h   "
                  f"heating {_kwh(result.energy.heating_demand_kwh)}")
            for warning in result.warnings:
                if warning.severity.value in ("severe", "warning"):
                    print(f"      ! [{warning.code}] {warning.message[:120]}")

        if identifier == list(sites)[0]:
            for export in engine.cfd_export(queue[-1], options):
                path = write_cfd_export(export, cfd_dir)
                print(f"  fluid dynamics boundary conditions -> {path.name}")

    elapsed = time.perf_counter() - started
    print(f"\n{total} runs in {elapsed:.0f} s, stored in {data / 'runs.db'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
