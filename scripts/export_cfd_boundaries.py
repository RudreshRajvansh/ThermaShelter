from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine" / "src"))

from shelter_engine.cfd import write_cfd_export  # noqa: E402
from shelter_engine.engine import Engine  # noqa: E402
from shelter_engine.materials import MaterialLibrary  # noqa: E402
from shelter_engine.schema import DesignSpec, SiteSpec  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from precompute import periods  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the fluid dynamics boundary conditions for one stored design "
                    "at one site, without re-running the whole precompute cycle.")
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--site", default="leh")
    parser.add_argument("--design", required=True,
                        help="a file name in data/designs, without the extension")
    args = parser.parse_args()

    data = Path(args.data)
    site_path = data / "sites" / f"{args.site}.json"
    design_path = data / "designs" / f"{args.design}.json"
    if not site_path.is_file():
        print(f"error: no site description at {site_path}", file=sys.stderr)
        return 2
    if not design_path.is_file():
        print(f"error: no design at {design_path}", file=sys.stderr)
        return 2

    site = SiteSpec.model_validate(json.loads(site_path.read_text(encoding="utf-8")))
    site = site.model_copy(update={"epw_path": str((data / site.epw_path).resolve())})
    design = DesignSpec.model_validate(json.loads(design_path.read_text(encoding="utf-8")))
    library = MaterialLibrary.load(data / "materials.db")

    engine = Engine(site, library, fuel="diesel")
    options, label = periods(site.climate_objective)
    print(f"{site.name}  {label}  {design.name}")
    cfd_dir = data / "cfd"
    cfd_dir.mkdir(parents=True, exist_ok=True)
    for export in engine.cfd_export(design, options):
        path = write_cfd_export(export, cfd_dir)
        print(f"  {export.label} at {export.timestamp} -> {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
