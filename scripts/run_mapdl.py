from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine" / "src"))

from shelter_engine import Engine, MaterialLibrary  # noqa: E402
from shelter_engine.errors import ConfigurationError, EngineError  # noqa: E402
from shelter_engine.mapdl import (LEVELS, agreement, build_grid, build_report,  # noqa: E402
                                  check_record, find_mapdl, level_summary, mapdl_version,
                                  parse_hourly, run_deck, study, write_deck, write_report)
from shelter_engine.mapdl_verify import check_rectangle, check_step, check_wall  # noqa: E402
from shelter_engine.evidence import mapdl_evidence  # noqa: E402
from shelter_engine.schema import DesignSpec, RunPeriod, SiteSpec  # noqa: E402


def load_site(data: Path, site_id: str) -> SiteSpec:
    path = data / "sites" / f"{site_id}.json"
    if not path.is_file():
        raise ConfigurationError(f"no site {site_id!r} in {path.parent}")
    site = SiteSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return site.model_copy(update={"epw_path": str((data / site.epw_path).resolve())})


def load_design(data: Path, design: str) -> DesignSpec:
    path = Path(design)
    if not path.is_file():
        path = data / "designs" / f"{design}.json"
    if not path.is_file():
        raise ConfigurationError(f"no design {design!r}; give a JSON path or a name in "
                                 f"{data / 'designs'}")
    return DesignSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def solve(case, level: str, cycles: int, substeps: int, workdir: Path, executable: Path,
          cores: int, timeout_s: float):
    grid = build_grid(case, level)
    deck = write_deck(case, grid, cycles=cycles, substeps=substeps)
    print(f"  {level}, {substeps} substeps: {deck.node_count} nodes, "
          f"{deck.element_count} elements", flush=True)
    path, runtime = run_deck(deck, workdir, executable, cores, timeout_s)
    run = parse_hourly(path, case, deck, level, cycles, runtime)
    fit = agreement(case, run)
    print(f"    {runtime:.0f} s, RMSE {fit.rmse_k:.2f} K, bias {fit.bias_k:+.2f} K",
          flush=True)
    return run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Solve a design's envelope and room air in ANSYS Mechanical APDL on "
                    "the loads EnergyPlus computed, and store how closely the two agree.")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--site", default="leh")
    parser.add_argument("--design", required=True,
                        help="design name in data/designs, or a path to a design JSON")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2019, 1, 10))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2019, 1, 16))
    parser.add_argument("--label", default="week")
    parser.add_argument("--level", choices=sorted(LEVELS), default="coarse")
    parser.add_argument("--cycles", type=int, default=1,
                        help="passes over the period; runs that carry EnergyPlus's lead in "
                             "of real weather make one pass")
    parser.add_argument("--substeps", type=int, default=1, help="solver steps per hour")
    parser.add_argument("--study", action="store_true",
                        help="also solve the other two meshes and halve the time step, "
                             "for the grid convergence index")
    parser.add_argument("--checks", action="store_true",
                        help="also run the analytic checks of the element types")
    parser.add_argument("--cores", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=4 * 3600.0)
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end falls before --start")
    if args.cycles < 1 or args.substeps < 1 or args.cores < 1:
        parser.error("--cycles, --substeps and --cores must be at least one")

    try:
        executable = find_mapdl()
        site = load_site(args.data, args.site)
        design = load_design(args.data, args.design)
        library = MaterialLibrary.load(args.data / "materials.db")
        engine = Engine(site, library)
        print(f"EnergyPlus: {design.name} at {site.id}, {args.start} to {args.end}",
              flush=True)
        case = engine.mapdl_case(design, RunPeriod(start=args.start, end=args.end),
                                 label=args.label)
        base = args.workdir or Path(tempfile.mkdtemp(prefix="shelter_mapdl_"))
        print(f"MAPDL {mapdl_version(executable)} in {base}", flush=True)
        checks = []
        if args.checks:
            heaviest = max(case.wall, key=lambda layer: layer.density_kg_m3
                           * layer.specific_heat_j_kgk * layer.thickness_m)
            for result in (check_rectangle(base / "check_rectangle", executable),
                           check_wall(case.wall, base / "check_wall", executable),
                           check_step(heaviest, base / "check_step", executable,
                                      substeps=args.substeps)):
                print(f"  check {result.name}: {result.max_abs_k:.4f} K against "
                      f"{result.tolerance_k} K", flush=True)
                checks.append(check_record(result))
        main_run = solve(case, args.level, args.cycles, args.substeps,
                         base / args.level, executable, args.cores, args.timeout)
        levels = [level_summary(case, main_run, args.substeps)]
        convergence = None
        if args.study:
            for level in LEVELS:
                if level == args.level:
                    continue
                run = solve(case, level, args.cycles, args.substeps, base / level,
                            executable, args.cores, args.timeout)
                levels.append(level_summary(case, run, args.substeps))
            halved_run = solve(case, args.level, args.cycles, args.substeps * 2,
                               base / f"{args.level}_halved", executable, args.cores,
                               args.timeout)
            halved = level_summary(case, halved_run, args.substeps * 2)
            convergence = study(levels, halved)
            levels.append(halved)
        report = build_report(case, main_run, site.id, mapdl_version(executable),
                              args.substeps, levels=levels, convergence=convergence,
                              checks=checks)
    except (ConfigurationError, EngineError) as error:
        print(f"failed: {error}")
        return 1
    path = write_report(report, args.data / "mapdl")
    folder = base / args.level
    files = {"report.json": report.model_dump_json(indent=1).encode("utf-8")}
    for name in ("shelter.inp", "mapdl_hourly.csv"):
        if (folder / name).is_file():
            files[name] = (folder / name).read_bytes()
    log = folder / "shelter.out"
    if log.is_file():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        files["shelter_out_tail.txt"] = "\n".join(lines[-400:]).encode("utf-8")
    mapdl_evidence(args.data / "evidence", report.design_hash, report.label, files, {
        "solver": report.solver, "version": report.version, "level": report.level,
        "nodes": report.node_count, "rmse_k": report.agreement.rmse_k,
        "meets_target": report.agreement.meets_target,
        "checks": {check.name: check.passed for check in report.checks}})
    fit = report.agreement
    print(f"stored {path.name}: RMSE {fit.rmse_k:.2f} K against the {fit.target_rmse_k} K "
          f"target ({'met' if fit.meets_target else 'not met'})")
    if convergence is not None:
        print(f"grid convergence: order {convergence.order}, GCI {convergence.gci_fine_k} K, "
              f"time step {convergence.time_step_difference_k} K")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
