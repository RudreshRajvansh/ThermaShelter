from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from xml.sax.saxutils import escape

import numpy as np

from . import __version__
from .costing import bill_of_quantities
from .compliance import eco_niwas_sheet
from .geometry import GeometryModel, rotate_about_z, unit_normal
from .materials import MaterialLibrary
from .schema import (BillOfQuantities, ComplianceSheet, ConstructionSpec, DesignSpec, Facade,
                     SiteSpec, SurfaceKind)

VIEW_WIDTH = 900.0
VIEW_HEIGHT = 560.0
MARGIN = 70.0
FACADE_AXES = {
    Facade.SOUTH: ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
    Facade.NORTH: ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
    Facade.EAST: ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    Facade.WEST: ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
}
STYLE = ("<style>text{font-family:Arial,Helvetica,sans-serif;fill:#1b1f24}"
         ".zone{fill:#eef2f6;stroke:#1b1f24;stroke-width:2}"
         ".wall{fill:#dfe4ea;stroke:#1b1f24;stroke-width:1.5}"
         ".roof{fill:#c9d0d8;stroke:#1b1f24;stroke-width:1.5}"
         ".glass{fill:#9cc3e6;stroke:#1b1f24;stroke-width:1}"
         ".opening{stroke:#2f7fc1;stroke-width:6}"
         ".dim{stroke:#555;stroke-width:1}.small{font-size:12px}.title{font-size:18px}"
         "</style>")


@dataclass
class PackInputs:
    design: DesignSpec
    site: SiteSpec
    model: GeometryModel
    library: MaterialLibrary
    idf_text: str | None = None
    weather_path: Path | None = None
    extra_files: Mapping[str, bytes] = field(default_factory=dict)
    notes: Sequence[str] = ()


class _Frame:
    def __init__(self, xs: Iterable[float], ys: Iterable[float]):
        xs, ys = list(xs), list(ys)
        self.x0, self.x1 = min(xs), max(xs)
        self.y0, self.y1 = min(ys), max(ys)
        span_x = max(self.x1 - self.x0, 1e-6)
        span_y = max(self.y1 - self.y0, 1e-6)
        self.scale = min((VIEW_WIDTH - 2 * MARGIN) / span_x,
                         (VIEW_HEIGHT - 2 * MARGIN - 30.0) / span_y)

    def point(self, x: float, y: float) -> tuple[float, float]:
        return (MARGIN + (x - self.x0) * self.scale,
                VIEW_HEIGHT - MARGIN - (y - self.y0) * self.scale)


def _svg(title: str, body: list[str]) -> str:
    return ("<svg xmlns=\"http://www.w3.org/2000/svg\" "
            f"viewBox=\"0 0 {VIEW_WIDTH:.0f} {VIEW_HEIGHT:.0f}\" "
            f"width=\"{VIEW_WIDTH:.0f}\" height=\"{VIEW_HEIGHT:.0f}\">" + STYLE
            + "<rect width=\"100%\" height=\"100%\" fill=\"#ffffff\"/>"
            + f"<text x=\"{MARGIN}\" y=\"36\" class=\"title\">{escape(title)}</text>"
            + "".join(body) + "</svg>\n")


def _polygon(points: Sequence[tuple[float, float]], css: str) -> str:
    joined = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f"<polygon points=\"{joined}\" class=\"{css}\"/>"


def _dimension(frame: _Frame, start: tuple[float, float], end: tuple[float, float],
               offset: tuple[float, float], label: str) -> str:
    a = frame.point(*start)
    b = frame.point(*end)
    ax, ay = a[0] + offset[0], a[1] + offset[1]
    bx, by = b[0] + offset[0], b[1] + offset[1]
    mx, my = (ax + bx) / 2, (ay + by) / 2
    return (f"<line x1=\"{ax:.1f}\" y1=\"{ay:.1f}\" x2=\"{bx:.1f}\" y2=\"{by:.1f}\" "
            "class=\"dim\"/>"
            f"<text x=\"{mx:.1f}\" y=\"{my - 6:.1f}\" class=\"small\" "
            f"text-anchor=\"middle\">{escape(label)}</text>")


def _scale_bar(frame: _Frame) -> str:
    length = frame.scale
    y = VIEW_HEIGHT - 24
    return (f"<line x1=\"{MARGIN}\" y1=\"{y}\" x2=\"{MARGIN + length:.1f}\" y2=\"{y}\" "
            "stroke=\"#1b1f24\" stroke-width=\"3\"/>"
            f"<text x=\"{MARGIN + length + 8:.1f}\" y=\"{y + 4}\" class=\"small\">1 m</text>")


def plan_svg(model: GeometryModel, design: DesignSpec) -> str:
    floors = [polygon for polygon in model.polygons if polygon.kind is SurfaceKind.FLOOR]
    if not floors:
        return _svg("Plan: no floor in the model", [])
    frame = _Frame([v[0] for p in floors for v in p.vertices],
                   [v[1] for p in floors for v in p.vertices])
    body: list[str] = []
    for polygon in floors:
        body.append(_polygon([frame.point(v[0], v[1]) for v in polygon.vertices], "zone"))
        cx = sum(v[0] for v in polygon.vertices) / len(polygon.vertices)
        cy = sum(v[1] for v in polygon.vertices) / len(polygon.vertices)
        px, py = frame.point(cx, cy)
        body.append(f"<text x=\"{px:.1f}\" y=\"{py:.1f}\" text-anchor=\"middle\" "
                    f"class=\"small\">{escape(polygon.zone.replace('_', ' ').lower())}, "
                    f"{polygon.area_m2:.1f} m2</text>")
    drawn: set[tuple] = set()
    for window in model.windows:
        if abs(window.normal[2]) > 0.5:
            points = [frame.point(v[0], v[1]) for v in window.vertices]
            key = tuple(sorted((round(x), round(y)) for x, y in points))
            if key not in drawn:
                drawn.add(key)
                body.append(_polygon(points, "glass"))
            continue
        xs = [v[0] for v in window.vertices]
        ys = [v[1] for v in window.vertices]
        a = frame.point(min(xs), min(ys))
        b = frame.point(max(xs), max(ys))
        key = (round(a[0]), round(a[1]), round(b[0]), round(b[1]))
        if key in drawn:
            continue
        drawn.add(key)
        body.append(f"<line x1=\"{a[0]:.1f}\" y1=\"{a[1]:.1f}\" x2=\"{b[0]:.1f}\" "
                    f"y2=\"{b[1]:.1f}\" class=\"opening\"/>")
    body.append(_dimension(frame, (frame.x0, frame.y0), (frame.x1, frame.y0), (0, 34),
                           f"{frame.x1 - frame.x0:.2f} m"))
    body.append(_dimension(frame, (frame.x1, frame.y0), (frame.x1, frame.y1), (34, 0),
                           f"{frame.y1 - frame.y0:.2f} m"))
    body.append(f"<g transform=\"translate({VIEW_WIDTH - 50:.0f},90)\">"
                "<polygon points=\"0,-26 8,8 0,2 -8,8\" fill=\"#1b1f24\"/>"
                "<text x=\"0\" y=\"-32\" text-anchor=\"middle\" class=\"small\">N</text></g>")
    body.append(_scale_bar(frame))
    return _svg(f"Plan of {design.name}, true north up: inside faces of the surfaces "
                "EnergyPlus models, windows in blue", body)


def elevation_svg(model: GeometryModel, design: DesignSpec, facade: Facade) -> str:
    normal, right = FACADE_AXES[facade]
    turn = 180.0 - design.plan.azimuth_deg
    faces = []
    for polygon in model.polygons:
        if polygon.outside_boundary != "Outdoors":
            continue
        local = rotate_about_z(polygon.vertices, turn)
        facing = float(np.dot(unit_normal(local), normal))
        if facing <= 0.01:
            continue
        depth = sum(sum(v[i] * normal[i] for i in range(3)) for v in local) / len(local)
        faces.append((depth, polygon.kind, local))
    if not faces:
        return _svg(f"{facade.value.title()} elevation: no surface faces this way", [])

    def along(vertex) -> float:
        return sum(vertex[i] * right[i] for i in range(3))

    frame = _Frame([along(v) for _, _, local in faces for v in local],
                   [v[2] for _, _, local in faces for v in local])
    body: list[str] = []
    order = {SurfaceKind.WINDOW: 2}
    for _, kind, local in sorted(faces, key=lambda item: (round(item[0], 3),
                                                          order.get(item[1], 0))):
        css = {SurfaceKind.WINDOW: "glass", SurfaceKind.ROOF: "roof"}.get(kind, "wall")
        body.append(_polygon([frame.point(along(v), v[2]) for v in local], css))
    body.append(_dimension(frame, (frame.x0, frame.y0), (frame.x1, frame.y0), (0, 34),
                           f"{frame.x1 - frame.x0:.2f} m"))
    body.append(_dimension(frame, (frame.x1, frame.y0), (frame.x1, frame.y1), (34, 0),
                           f"{frame.y1 - frame.y0:.2f} m"))
    spec = design.windows.get(facade)
    if spec is not None and spec.wwr > 0:
        body.append(f"<text x=\"{MARGIN}\" y=\"60\" class=\"small\">window to wall ratio "
                    f"{spec.wwr:.2f}, sill {spec.sill_height_m:.2f} m</text>")
    body.append(_scale_bar(frame))
    return _svg(f"{facade.value.title()} elevation of {design.name}", body)


def section_svg(construction: ConstructionSpec, library: MaterialLibrary, title: str) -> str:
    layers = library.layers(construction)
    total = sum(layer.thickness_m for layer in layers)
    width = VIEW_WIDTH - 2 * MARGIN - 320
    body: list[str] = []
    x = MARGIN
    top, height = 90.0, VIEW_HEIGHT - 190.0
    palette = ("#dfe4ea", "#f3e3c3", "#c9d0d8", "#e8d5cf", "#d5e6d3", "#e4dcef")
    for index, layer in enumerate(layers):
        share = max(layer.thickness_m / total * width, 2.0) if total > 0 else 2.0
        body.append(f"<rect x=\"{x:.1f}\" y=\"{top}\" width=\"{share:.1f}\" "
                    f"height=\"{height}\" fill=\"{palette[index % len(palette)]}\" "
                    "stroke=\"#1b1f24\"/>")
        label_y = top + 20 + index * 18
        body.append(f"<text x=\"{MARGIN + width + 30:.1f}\" y=\"{label_y:.1f}\" "
                    f"class=\"small\">{index + 1}. {escape(layer.material.name)}, "
                    f"{layer.thickness_m * 1000:.1f} mm</text>")
        body.append(f"<text x=\"{x + share / 2:.1f}\" y=\"{top + height + 18:.1f}\" "
                    f"class=\"small\" text-anchor=\"middle\">{index + 1}</text>")
        x += share
    body.append(f"<text x=\"{MARGIN}\" y=\"{top - 14}\" class=\"small\">outside</text>")
    body.append(f"<text x=\"{x:.1f}\" y=\"{top - 14}\" class=\"small\" "
                "text-anchor=\"end\">inside</text>")
    body.append(f"<text x=\"{MARGIN}\" y=\"{VIEW_HEIGHT - 30}\" class=\"small\">"
                f"{total * 1000:.0f} mm in all, layers not to scale below 2 px</text>")
    return _svg(title, body)


def quantities_csv(bill: BillOfQuantities) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["item_id", "name", "kind", "used_in", "area_m2", "volume_m3", "mass_kg",
                     "locally_available", "cost_inr", "note"])
    for line in bill.lines:
        writer.writerow([line.item_id, line.name, line.kind, " ".join(line.used_in),
                         f"{line.area_m2:.3f}",
                         "" if line.volume_m3 is None else f"{line.volume_m3:.4f}",
                         "" if line.mass_kg is None else f"{line.mass_kg:.1f}",
                         "" if line.locally_available is None else line.locally_available,
                         f"{line.cost_inr:.0f}", line.note or ""])
    return buffer.getvalue()


def layers_csv(design: DesignSpec, library: MaterialLibrary) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["element", "order_from_outside", "material_id", "material",
                     "thickness_mm", "conductivity_w_mk", "density_kg_m3",
                     "specific_heat_j_kgk", "source_status"])
    elements = [("wall", design.wall), ("roof", design.roof), ("floor", design.floor)]
    if design.partition is not None:
        elements.append(("partition", design.partition))
    for element, construction in elements:
        for index, layer in enumerate(library.layers(construction), start=1):
            material = layer.material
            writer.writerow([element, index, material.id, material.name,
                             f"{layer.thickness_m * 1000:.1f}", material.conductivity_w_mk,
                             material.density_kg_m3, material.specific_heat_j_kgk,
                             material.source.status.value])
    return buffer.getvalue()


def _readme(inputs: PackInputs, files: Sequence[str], sheet: ComplianceSheet,
            bill: BillOfQuantities) -> str:
    lines = [f"Design pack for {inputs.design.name}",
             f"Site {inputs.site.name} ({inputs.site.id}), "
             f"{inputs.site.latitude_deg:.5f} N, {inputs.site.longitude_deg:.5f} E, "
             f"{inputs.site.elevation_m:.0f} m",
             f"Built {datetime.now(timezone.utc).isoformat(timespec='seconds')} by shelter "
             f"engine {__version__}.",
             "",
             f"Envelope weight {bill.total_mass_kg / 1000:.1f} t, of which "
             f"{bill.imported_mass_kg / 1000:.1f} t is brought in.",
             f"{sheet.code}: " + ("not assessed. " + sheet.zone_basis if not sheet.assessed
                                  else "meets every mandatory provision"
                                  if sheet.mandatory_pass else
                                  "fails a mandatory provision, see bye_law_sheet.json"),
             "",
             "Files"]
    lines += [f"  {name}" for name in files]
    if inputs.notes:
        lines += ["", "Notes"] + [f"  {note}" for note in inputs.notes]
    lines += ["", "The drawings show the surfaces as EnergyPlus models them: walls at their "
              "inside faces with no thickness, which the layer sections give instead."]
    return "\n".join(lines) + "\n"


def build_design_pack(inputs: PackInputs) -> bytes:
    design, library, model = inputs.design, inputs.library, inputs.model
    bill = bill_of_quantities(design, model, library)
    sheet = eco_niwas_sheet(design, model, library, inputs.site)
    files: dict[str, bytes] = {
        "design.json": design.model_dump_json(indent=2).encode("utf-8"),
        "site.json": inputs.site.model_dump_json(indent=2).encode("utf-8"),
        "bill_of_quantities.csv": quantities_csv(bill).encode("utf-8"),
        "bill_of_quantities.json": bill.model_dump_json(indent=2).encode("utf-8"),
        "bye_law_sheet.json": sheet.model_dump_json(indent=2).encode("utf-8"),
        "construction_layers.csv": layers_csv(design, library).encode("utf-8"),
        "drawings/plan.svg": plan_svg(model, design).encode("utf-8"),
    }
    for facade in Facade:
        files[f"drawings/elevation_{facade.value}.svg"] = \
            elevation_svg(model, design, facade).encode("utf-8")
    sections = [("wall", design.wall), ("roof", design.roof), ("floor", design.floor)]
    for element, construction in sections:
        files[f"drawings/section_{element}.svg"] = section_svg(
            construction, library, f"{element.title()} build up, {construction.name}"
        ).encode("utf-8")
    if inputs.idf_text is not None:
        files["energyplus/shelter.idf"] = inputs.idf_text.encode("utf-8")
    if inputs.weather_path is not None and Path(inputs.weather_path).is_file():
        files[f"energyplus/{Path(inputs.weather_path).name}"] = \
            Path(inputs.weather_path).read_bytes()
    for name, payload in inputs.extra_files.items():
        if name in files or name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"the pack cannot hold a second or outside file named {name}")
        files[name] = payload
    manifest = sorted(files) + ["README.txt"]
    files["README.txt"] = _readme(inputs, manifest, sheet, bill).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return buffer.getvalue()

