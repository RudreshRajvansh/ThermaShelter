from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine" / "src"))

from shelter_engine.ansys import FluentOptions, run_fluent  # noqa: E402
from shelter_engine.cfd import (BOUNDARY_SUFFIX, FIELD_SUFFIX, label_of,  # noqa: E402
                                load_cfd_export, load_cfd_field, write_cfd_field)
from shelter_engine.errors import EngineError  # noqa: E402
from shelter_engine.hashing import stable_hash  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Solve the air inside a room with ANSYS Fluent from the boundary "
                    "conditions EnergyPlus exported, and store the field for the API.")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--design", help="design fingerprint, or its first characters")
    parser.add_argument("--label", help="coldest_night or peak_solar")
    parser.add_argument("--processors", type=int, default=4)
    parser.add_argument("--max-iterations", type=int, default=2000)
    parser.add_argument("--force", action="store_true",
                        help="solve again even when a current field exists")
    args = parser.parse_args()

    directory = args.data / "cfd"
    exports = sorted(directory.glob(f"*{BOUNDARY_SUFFIX}"))
    if args.design:
        exports = [path for path in exports if path.name.startswith(args.design)]
    if args.label:
        exports = [path for path in exports if path.name.split("_", 1)[1]
                   == f"{args.label}{BOUNDARY_SUFFIX}"]
    if not exports:
        print(f"no boundary conditions match in {directory}")
        return 1

    options = FluentOptions(processor_count=args.processors,
                            max_iterations=args.max_iterations)
    failures = 0
    for path in exports:
        export = load_cfd_export(path)
        label = label_of(path, export.design_hash)
        existing = directory / f"{export.design_hash}_{label}{FIELD_SUFFIX}"
        if existing.is_file() and not args.force:
            try:
                if load_cfd_field(existing).boundary_hash == stable_hash(export):
                    print(f"{existing.name}: current, skipped")
                    continue
            except (EngineError, ValueError):
                pass
        started = time.perf_counter()
        try:
            field = run_fluent(export, ROOT / "build" / "cfd_runs" / export.design_hash,
                               options, progress=print)
        except EngineError as error:
            failures += 1
            print(f"{path.name}: FAILED, {error}")
            continue
        written = write_cfd_field(field, directory)
        print(f"{written.name}: {'converged' if field.solver.converged else 'NOT CONVERGED'}, "
              f"air {field.min_c:.2f} to {field.max_c:.2f} C, mean {field.mean_c:.2f} C, "
              f"EnergyPlus {field.energyplus_zone_air_c:.2f} C, "
              f"fastest {field.max_speed_m_s or 0.0:.3f} m/s, "
              f"{time.perf_counter() - started:.0f} s")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
