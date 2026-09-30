from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from xml.sax.saxutils import escape

from . import __version__
from .errors import EngineError
from .schema import HourlySeries, Severity, SimulationResult

MANIFEST = "manifest.json"
PLOT_WIDTH = 900.0
PLOT_HEIGHT = 360.0
PLOT_MARGIN = 50.0
PLOT_COLOURS = ("#d9822b", "#2f7fc1", "#3a9d5d", "#8e5bb5", "#b8455b")


def sha256_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def series_svg(title: str, series: Sequence[HourlySeries]) -> str:
    drawn = [item for item in series if any(value is not None for value in item.values)]
    body: list[str] = [f"<text x=\"{PLOT_MARGIN}\" y=\"24\" font-size=\"15\">"
                       f"{escape(title)}</text>"]
    if not drawn:
        body.append(f"<text x=\"{PLOT_MARGIN}\" y=\"60\" font-size=\"12\">No hourly series "
                    "was reported for this run.</text>")
    else:
        values = [value for item in drawn for value in item.values if value is not None]
        low, high = min(values), max(values)
        span = max(high - low, 1e-6)
        count = max(len(item.values) for item in drawn)
        width = PLOT_WIDTH - 2 * PLOT_MARGIN
        height = PLOT_HEIGHT - 2 * PLOT_MARGIN

        def point(index: int, value: float) -> str:
            x = PLOT_MARGIN + width * index / max(count - 1, 1)
            y = PLOT_HEIGHT - PLOT_MARGIN - height * (value - low) / span
            return f"{x:.1f},{y:.1f}"

        for rank, item in enumerate(drawn):
            colour = PLOT_COLOURS[rank % len(PLOT_COLOURS)]
            runs: list[list[str]] = [[]]
            for index, value in enumerate(item.values):
                if value is None:
                    runs.append([])
                else:
                    runs[-1].append(point(index, value))
            for run in runs:
                if len(run) > 1:
                    body.append(f"<polyline fill=\"none\" stroke=\"{colour}\" "
                                f"stroke-width=\"1.5\" points=\"{' '.join(run)}\"/>")
            body.append(f"<text x=\"{PLOT_MARGIN + 165 * rank}\" y=\"{PLOT_HEIGHT - 12}\" "
                        f"font-size=\"12\" fill=\"{colour}\">{escape(item.name)} "
                        f"({escape(item.unit)})</text>")
        for value in (low, high):
            y = PLOT_HEIGHT - PLOT_MARGIN - height * (value - low) / span
            body.append(f"<text x=\"4\" y=\"{y + 4:.1f}\" font-size=\"11\">{value:.1f}</text>")
        body.append(f"<text x=\"{PLOT_MARGIN}\" y=\"{PLOT_HEIGHT - PLOT_MARGIN + 16}\" "
                    f"font-size=\"11\">{escape(drawn[0].timestamps[0])}</text>")
        body.append(f"<text x=\"{PLOT_WIDTH - PLOT_MARGIN}\" "
                    f"y=\"{PLOT_HEIGHT - PLOT_MARGIN + 16}\" font-size=\"11\" "
                    "text-anchor=\"end\">"
                    f"{escape(drawn[0].timestamps[-1])}</text>")
    return ("<svg xmlns=\"http://www.w3.org/2000/svg\" "
            f"viewBox=\"0 0 {PLOT_WIDTH:.0f} {PLOT_HEIGHT:.0f}\" width=\"{PLOT_WIDTH:.0f}\" "
            f"height=\"{PLOT_HEIGHT:.0f}\"><rect width=\"100%\" height=\"100%\" "
            "fill=\"#ffffff\"/>" + "".join(body) + "</svg>\n")


def _safe(name: str) -> str:
    path = Path(name)
    if not name or path.anchor or ".." in path.parts:
        raise EngineError(f"evidence file name {name!r} leaves the folder")
    return path.as_posix()


def write_folder(folder: Path, files: Mapping[str, bytes], summary: Mapping) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    listing = []
    for name, payload in sorted(files.items()):
        relative = _safe(name)
        if relative == MANIFEST:
            raise EngineError("the manifest is written by the evidence folder itself")
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        listing.append({"file": relative, "bytes": len(payload), "sha256": sha256_of(payload)})
    previous = read_manifest(folder) if (folder / MANIFEST).is_file() else {}
    known = {item["file"]: item for item in previous.get("files", [])}
    known.update({item["file"]: item for item in listing})
    manifest = dict(previous)
    manifest.update(summary)
    manifest["files"] = [known[name] for name in sorted(known)]
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=2, default=str),
                                   encoding="utf-8")
    return folder


def read_manifest(folder: Path) -> dict:
    path = Path(folder) / MANIFEST
    if not path.is_file():
        raise EngineError(f"no evidence manifest in {folder}")
    return json.loads(path.read_text(encoding="utf-8"))


def simulation_evidence(directory: Path, result: SimulationResult, idf_text: str | None,
                        weather_path: Path | None) -> Path:
    files: dict[str, bytes] = {
        "design.json": result.design.model_dump_json(indent=2).encode("utf-8"),
        "result.json": result.model_dump_json(indent=1, exclude={"surfaces"}).encode("utf-8"),
        "warnings.json": json.dumps([item.model_dump(mode="json") for item in result.warnings],
                                    indent=2).encode("utf-8"),
        "plots/temperatures.svg": series_svg(
            f"{result.design.name}: hourly temperatures, {result.period.start} to "
            f"{result.period.end}",
            [item for item in result.hourly if item.unit in ("C", "°C")]).encode("utf-8"),
    }
    if idf_text is not None:
        files["energyplus/in.idf"] = idf_text.encode("utf-8")
    weather = None
    if weather_path is not None and Path(weather_path).is_file():
        payload = Path(weather_path).read_bytes()
        weather = {"file": Path(weather_path).name, "sha256": sha256_of(payload)}
    counts = {level.value: sum(1 for item in result.warnings if item.severity is level)
              for level in Severity}
    summary = {
        "run_hash": result.design_hash, "design_fingerprint": result.design_fingerprint,
        "design_name": result.design.name, "site_id": result.site_id,
        "period": {"start": result.period.start.isoformat(),
                   "end": result.period.end.isoformat()},
        "solvers": {"shelter_engine": result.engine_version or __version__,
                    "energyplus": result.energyplus_version or None},
        "weather": weather,
        "checks": {"warnings": counts, "blocking": result.has_blocking_warnings},
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return write_folder(Path(directory) / result.design_hash, files, summary)


def mapdl_evidence(directory: Path, design_fingerprint: str, label: str,
                   files: Mapping[str, bytes], summary: Mapping) -> Path:
    prefixed = {f"mapdl_{label}/{name}": payload for name, payload in files.items()}
    return write_folder(Path(directory) / design_fingerprint, prefixed,
                        {f"mapdl_{label}": dict(summary)})


def zip_folder(folder: Path) -> bytes:
    folder = Path(folder)
    read_manifest(folder)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder).as_posix())
    return buffer.getvalue()
