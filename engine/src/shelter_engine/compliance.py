from __future__ import annotations

from .geometry import GeometryModel, ROOM
from .materials import MaterialLibrary
from .schema import (ClimateObjective, ComplianceItem, ComplianceSheet, DesignSpec,
                     SiteSpec, SourceRef, SourceStatus, SurfaceKind)

ENS_CODE = "Eco-Niwas Samhita 2024 (Energy Conservation Building Code for Residential Buildings)"
ENS_SOURCE = SourceRef(
    citation=("Bureau of Energy Efficiency, Eco-Niwas Samhita (ENS) 2024: 5.2.1 mandatory "
              "envelope provisions, 8.2.8.2 compliance for cold climates, 9.4 and 9.7 "
              "calculation of Uroof and Uenvelope,cold, Table 62 climate zones"),
    status=SourceStatus.VERIFIED,
    url="https://beeindia.gov.in/WriteReadData/L45218/0405504518282114.pdf")
ROOF_U_MAX = 1.2
ENVELOPE_U_COLD_MAX = 1.8
WFR_OPENABLE_MIN_COLD = 8.33
VLT_MINIMUM = ((0.30, 0.27), (0.40, 0.20), (0.50, 0.16), (0.60, 0.13), (0.70, 0.11))


def _round_half_up(value: float, digits: int) -> float:
    from decimal import ROUND_HALF_UP, Decimal

    return float(Decimal(repr(value)).quantize(Decimal(1).scaleb(-digits),
                                               rounding=ROUND_HALF_UP))


def minimum_vlt(wwr: float) -> float | None:
    rounded = _round_half_up(wwr, 2)
    for upper, minimum in VLT_MINIMUM:
        if rounded <= upper + 1e-9:
            return minimum
    return None


def eco_niwas_sheet(design: DesignSpec, model: GeometryModel, library: MaterialLibrary,
                    site: SiteSpec) -> ComplianceSheet:
    if site.climate_objective is not ClimateObjective.HEATING_DOMINATED:
        return ComplianceSheet(
            code=ENS_CODE, climate_zone=None, assessed=False, source=ENS_SOURCE,
            zone_basis=("This site is cooling dominated. Outside the cold zone ENS asks for "
                        "the Residential Envelope Transmittance Value (RETV), which needs "
                        "orientation factors this tool does not compute yet, so the sheet "
                        "is not assessed rather than guessed."))
    walls = [polygon for polygon in model.polygons
             if polygon.zone == ROOM and polygon.kind is SurfaceKind.WALL
             and polygon.outside_boundary == "Outdoors"]
    wall_names = {polygon.name for polygon in walls}
    windows = [polygon for polygon in model.windows
               if polygon.parent in wall_names and polygon.outside_boundary == "Outdoors"]
    gross = sum(polygon.area_m2 for polygon in walls)
    glazed = sum(polygon.area_m2 for polygon in windows)
    wall_u = library.u_value(design.wall, "wall")
    roof_u = library.u_value(design.roof, "roof")
    conductance = (gross - glazed) * wall_u
    visible = []
    for polygon in windows:
        spec = design.windows.get(polygon.facade) if polygon.facade is not None else None
        if spec is None or spec.glazing_id is None:
            continue
        glazing = library.glazing(spec.glazing_id)
        conductance += polygon.area_m2 * glazing.u_value_w_m2k
        visible.append((glazing.name, glazing.visible_transmittance))
    envelope_u = conductance / gross if gross > 0.0 else None
    wwr = glazed / gross if gross > 0.0 else 0.0
    floor = model.zone(ROOM).floor_area_m2
    roof_rounded = _round_half_up(roof_u, 1)
    items = [
        ComplianceItem(
            clause="5.2.1.3, 9.4", title="Roof thermal transmittance (Uroof)",
            value=roof_u, unit="W/m2K", limit=ROOF_U_MAX, comparison="max", mandatory=True,
            passes=roof_rounded <= ROOF_U_MAX,
            note=f"Compared after rounding to one decimal as 9.4 requires: {roof_rounded:.1f}."),
        ComplianceItem(
            clause="5.2.1.5, 9.7", title="Walls and openings, except roof (Uenvelope,cold)",
            value=envelope_u, unit="W/m2K", limit=ENVELOPE_U_COLD_MAX, comparison="max",
            mandatory=True,
            passes=None if envelope_u is None else envelope_u <= ENVELOPE_U_COLD_MAX,
            note=(f"Area weighted over {gross:.1f} m2 of gross external wall: opaque wall "
                  f"at {wall_u:.2f} W/m2K and {glazed:.1f} m2 of glazing at its whole "
                  "window U-value. Thermal bridges are not part of the ENS formula.")),
        ComplianceItem(
            clause="5.2.1.1, 9.1", title="Openable window to floor area ratio (WFRop)",
            value=None, unit="%", limit=WFR_OPENABLE_MIN_COLD, comparison="min",
            mandatory=False, passes=None,
            note=(f"The design does not say how much of each window opens, so WFRop is not "
                  f"computed. The glazed area is {100.0 * glazed / floor:.2f} % of the "
                  "floor, which is the most it could be. 8.2.8.2 does not make this a "
                  "compliance condition in the cold zone.")),
    ]
    minimum = minimum_vlt(wwr)
    for name, transmittance in sorted(set(visible)):
        items.append(ComplianceItem(
            clause="5.2.1.2, Table 9", title=f"Visible light transmittance, {name}",
            value=transmittance, unit="", limit=minimum, comparison="min", mandatory=False,
            passes=None if minimum is None else transmittance >= minimum,
            note=(f"Window to wall ratio {wwr:.2f}." if minimum is not None else
                  f"Window to wall ratio {wwr:.2f} is beyond Table 9, which stops at 0.70.")))
    mandatory = [item.passes for item in items if item.mandatory]
    notes = ["ENS is written for dwelling units. It is applied here to the shelter's heated "
             "room as the nearest civil reference; the sunspace, Trombe gap and vestibule "
             "are unheated buffers and are left out of the envelope, so walls shared with "
             "them are not counted."]
    return ComplianceSheet(
        code=ENS_CODE, climate_zone="Cold", assessed=True, items=tuple(items),
        mandatory_pass=None if None in mandatory else all(mandatory),
        zone_basis=("Cold-zone provisions apply because the site is heating dominated. ENS "
                    "Table 62 lists Leh as Cold; the tool does not rebuild the NBC zone map "
                    "for other points."),
        notes=tuple(notes), source=ENS_SOURCE)
