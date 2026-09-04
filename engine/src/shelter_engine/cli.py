from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .baseline import check_library_covers_references, load_references
from .comfort import load_catalogue
from .config import energyplus_available, find_energyplus
from .constructions import build_constructions
from .engine import Engine
from .errors import EngineError
from .geometry import build_geometry
from .idf import build_idf
from .materials import MaterialLibrary
from .schema import DesignSpec, RunOptions, RunPeriod, Severity, SiteSpec
from .uncertainty import describe_band


def _load_site(path: Path) -> SiteSpec:
    return SiteSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _load_design(path: Path) -> DesignSpec:
    return DesignSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _period(start: str, end: str, label: str = "run") -> RunPeriod:
    return RunPeriod(start=date.fromisoformat(start), end=date.fromisoformat(end),
                     label=label)


def _engine(args) -> Engine:
    site = _load_site(Path(args.site))
    library = MaterialLibrary.load(args.materials)
    return Engine(site, library, comfort_model_id=args.comfort_model,
                  fuel=args.fuel or None)


def _print_warnings(warnings) -> int:
    blocking = 0
    for item in warnings:
        marker = {"info": "  .", "warning": "  !", "severe": " !!",
                  "fatal": "!!!"}[item.severity.value]
        if item.severity in (Severity.SEVERE, Severity.FATAL):
            blocking += 1
        count = f" (x{item.count})" if item.count > 1 else ""
        print(f"{marker} [{item.code}]{count} {item.message}")
    return blocking


def command_check(args) -> int:
    print("EnergyPlus:")
    if energyplus_available():
        install = find_energyplus()
        print(f"  found {install.version_string} at {install.root}")
    else:
        print("  NOT FOUND. Set ENERGYPLUS_DIR or add energyplus to PATH.")
        return 2

    catalogue = load_catalogue()
    references = load_references()
    print(f"\nComfort models: {[model.id for model in catalogue.adaptive]}")
    print(f"Fuel models:    {[model.fuel for model in catalogue.fuels]}")
    print(f"References:     {list(references.names())}")
    uncited = references.uncited()
    if uncited:
        print(f"  ! reference designs without a verified citation: {list(uncited)}")

    if not args.materials:
        print("\nNo material database given, so coverage was not checked.")
        return 0
    library = MaterialLibrary.load(args.materials)
    print(f"\nMaterial library: {len(library.materials)} materials, "
          f"{len(library.glazing_systems)} glazing systems, "
          f"{len(library.phase_change_materials)} phase change materials "
          f"from {library.origin}")
    missing = check_library_covers_references(library, references)
    if missing:
        print(f"  !! the reference designs need records that are missing: {list(missing)}")
    pending = library.unsourced()
    if pending:
        print(f"  ! records without a verified source: {list(pending)}")
    return 2 if missing else 0


def command_geometry(args) -> int:
    site = _load_site(Path(args.site))
    library = MaterialLibrary.load(args.materials)
    design = _load_design(Path(args.design))
    constructions = build_constructions(design, library)
    model = build_geometry(design, constructions.names, horizon=site.horizon)
    from .mesh import build_mesh
    payload = build_mesh(model, constructions, library)
    output = Path(args.out) if args.out else None
    text = json.dumps(payload.model_dump(mode="json"), indent=2)
    if output:
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output} with {len(payload.surfaces)} surfaces")
    else:
        print(text)
    return 0


def command_idf(args) -> int:
    site = _load_site(Path(args.site))
    library = MaterialLibrary.load(args.materials)
    design = _load_design(Path(args.design))
    constructions = build_constructions(design, library)
    model = build_geometry(design, constructions.names, horizon=site.horizon)
    options = RunOptions(period=_period(args.start, args.end))
    bundle = build_idf(design, site, model, constructions, options,
                       conditioned=args.conditioned)
    target = Path(args.out)
    bundle.write(target)
    print(f"wrote {target}")
    _print_warnings(bundle.warnings)
    return 0


def command_run(args) -> int:
    engine = _engine(args)
    design = _load_design(Path(args.design))
    options = RunOptions(period=_period(args.start, args.end, "run"),
                         conditioned_run=args.conditioned,
                         surface_output=not args.no_surfaces)
    result = engine.simulate(design, options,
                             with_uncertainty=not args.no_uncertainty,
                             with_reference=not args.no_reference)
    _report(result)
    if args.out:
        Path(args.out).write_text(
            json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 1 if result.has_blocking_warnings else 0


def _report(result) -> None:
    comfort = result.comfort
    print(f"design            {result.design.name}  ({result.design.arrangement.value})")
    print(f"site              {result.site_id}   period {result.period.start} to "
          f"{result.period.end}")
    print(f"engine            EnergyPlus {result.energyplus_version} in "
          f"{result.runtime_s:.1f} s")
    print(f"headline metric   {result.headline_metric}")
    print("")
    print(f"  minimum overnight operative   "
          f"{_fmt(comfort.minimum_overnight_operative_c)} C")
    print(f"  minimum on the design day     "
          f"{_fmt(comfort.minimum_on_design_day_c)} C  ({comfort.design_day_date})")
    print(f"  mean operative                {_fmt(comfort.mean_operative_c)} C")
    print(f"  hours outside the band        {comfort.hours_outside_band:.0f}")
    print(f"  degree hours outside          {comfort.degree_hours_total_kh:.0f} Kh "
          f"(below {comfort.degree_hours_below_kh:.0f}, "
          f"above {comfort.degree_hours_above_kh:.0f})")
    print(f"  decrement factor / time lag   {_fmt(comfort.decrement_factor, 2)} / "
          f"{_fmt(comfort.time_lag_h, 1)} h")
    if result.energy.heating_demand_kwh is not None:
        print(f"  heating demand                "
              f"{result.energy.heating_demand_kwh:.0f} kWh")
        if result.energy.fuel_units is not None:
            print(f"  fuel                          "
                  f"{result.energy.fuel_units:.0f} {result.energy.fuel_unit_name} of "
                  f"{result.energy.fuel}")
    print(f"  condensation hours            {result.condensation.hours:.0f}")
    print(f"  envelope cost                 {result.cost.total_inr:,.0f} INR "
          f"({result.cost.max_mason_skill} labour)")

    for band in result.uncertainty:
        print(f"\n  {band.metric}: {describe_band(band)} "
              f"[nominal {band.nominal:.1f}, {band.runs} runs]")

    if result.reference is not None:
        reference = result.reference
        print(f"\nagainst {reference.reference_name}:")
        print(f"  overnight minimum   {_fmt(reference.delta_minimum_overnight_k, 1, '+')} K")
        print(f"  degree hours        {_fmt(reference.delta_degree_hours_kh, 0, '+')} Kh")
        if reference.delta_heating_demand_kwh is not None:
            print(f"  heating demand      "
                  f"{reference.delta_heating_demand_kwh:+,.0f} kWh")
        if reference.fuel_saved_units is not None:
            print(f"  fuel avoided        {reference.fuel_saved_units:+,.0f} "
                  f"{reference.fuel_unit_name}")
        if reference.delta_cost_inr is not None:
            print(f"  build cost          {reference.delta_cost_inr:+,.0f} INR")

    breakdown = result.breakdown.loss_fractions()
    if breakdown:
        print("\nwhere the heat goes:")
        for path, share in sorted(breakdown.items(), key=lambda item: -item[1]):
            print(f"  {path:22s} {share * 100:5.1f} %")

    if result.recommendations:
        print("\nrecommendations, generated from the numbers above:")
        for item in result.recommendations:
            print(f"  [{item.priority}] {item.title}")
            print(f"       {item.detail}")

    blocking = [item for item in result.warnings
                if item.severity in (Severity.SEVERE, Severity.FATAL)]
    other = [item for item in result.warnings if item not in blocking]
    if blocking:
        print("\nblocking warnings:")
        _print_warnings(blocking)
    if other:
        print(f"\nother warnings ({len(other)}):")
        _print_warnings(other)


def _fmt(value, decimals: int = 1, sign: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:{sign}.{decimals}f}"


def command_compare(args) -> int:
    engine = _engine(args)
    designs = [_load_design(Path(item)) for item in args.design]
    options = RunOptions(period=_period(args.start, args.end, "run"),
                         conditioned_run=args.conditioned, surface_output=False)
    table = engine.compare(designs, options)
    header = (f"{'design':28s} {'min night C':>12s} {'h below':>8s} "
              f"{'Kh out':>9s} {'cost INR':>12s} {'wall m':>7s} {'skill':>12s}")
    print(header)
    print("-" * len(header))
    for row in table.rows:
        name = row.name + (" (reference)" if row.is_reference else "")
        print(f"{name[:28]:28s} {_fmt(row.minimum_overnight_operative_c):>12s} "
              f"{_fmt(row.hours_below_band, 0):>8s} "
              f"{_fmt(row.degree_hours_total_kh, 0):>9s} "
              f"{_fmt(row.total_cost_inr, 0):>12s} "
              f"{_fmt(row.wall_thickness_m, 2):>7s} {str(row.mason_skill):>12s}")
    _print_warnings(table.warnings)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shelter-engine",
        description="Layer 2 simulation and design search engine for passive shelters")
    parser.add_argument("--materials", help="path to the Layer 0 material database")
    parser.add_argument("--site", help="path to a site JSON record")
    parser.add_argument("--comfort-model", default="imac_2016_nv")
    parser.add_argument("--fuel", default="diesel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="verify the engine can run at all")
    check.set_defaults(handler=command_check)

    geometry = subparsers.add_parser("geometry",
                                     help="emit the display mesh without simulating")
    geometry.add_argument("design")
    geometry.add_argument("--out")
    geometry.set_defaults(handler=command_geometry)

    idf = subparsers.add_parser("idf", help="write the EnergyPlus input file")
    idf.add_argument("design")
    idf.add_argument("--out", required=True)
    idf.add_argument("--start", default="2019-01-01")
    idf.add_argument("--end", default="2019-01-31")
    idf.add_argument("--conditioned", action="store_true")
    idf.set_defaults(handler=command_idf)

    run = subparsers.add_parser("run", help="simulate one design and report")
    run.add_argument("design")
    run.add_argument("--start", default="2019-01-01")
    run.add_argument("--end", default="2019-01-31")
    run.add_argument("--conditioned", action="store_true")
    run.add_argument("--no-surfaces", action="store_true")
    run.add_argument("--no-uncertainty", action="store_true")
    run.add_argument("--no-reference", action="store_true")
    run.add_argument("--out")
    run.set_defaults(handler=command_run)

    compare = subparsers.add_parser("compare",
                                    help="compare designs against the reference")
    compare.add_argument("design", nargs="+")
    compare.add_argument("--start", default="2019-01-01")
    compare.add_argument("--end", default="2019-01-31")
    compare.add_argument("--conditioned", action="store_true")
    compare.set_defaults(handler=command_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    needs_inputs = args.command in ("geometry", "idf", "run", "compare")
    if needs_inputs and not (args.site and args.materials):
        parser.error(f"{args.command} needs --site and --materials")
    try:
        return args.handler(args)
    except EngineError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
