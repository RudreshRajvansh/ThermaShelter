from __future__ import annotations

from dataclasses import dataclass, field

from .costing import envelope_u_values
from .geometry import GeometryModel
from .materials import MaterialLibrary
from .mesh import facade_glazing_ratio
from .schema import (BuildabilityMetrics, ClimateObjective, ComfortMetrics,
                     CondensationMetrics, CostBreakdown, DesignSpec, Facade,
                     HeatBalanceBreakdown, Provenance, Recommendation)

GLAZING_LOSS_SHARE = 0.30
INFILTRATION_LOSS_SHARE = 0.25
ROOF_LOSS_SHARE = 0.28
FLOOR_LOSS_SHARE = 0.15
BRIDGE_LOSS_SHARE = 0.10
LOW_SOUTH_WWR = 0.25
HIGH_SOUTH_WWR = 0.60
DECREMENT_HEAVY = 0.45
DECREMENT_COUPLED = 0.30
TIME_LAG_HOURS = 4.0
CONDENSATION_HOURS = 24.0
COMPLIANCE_RISK = 0.85
COMPACT_SURFACE_TO_VOLUME = 1.30
NON_LOCAL_FRACTION = 0.40
MINOR_GLAZING_SHARE = 0.15
ECO_NIWAS_WALL_U = 1.80
ECO_NIWAS_ROOF_U = 1.20


@dataclass
class RecommendationInputs:
    design: DesignSpec
    library: MaterialLibrary
    model: GeometryModel
    comfort: ComfortMetrics
    breakdown: HeatBalanceBreakdown
    condensation: CondensationMetrics
    buildability: BuildabilityMetrics
    cost: CostBreakdown
    climate_objective: ClimateObjective = ClimateObjective.HEATING_DOMINATED
    incident_solar_kwh_by_facade: dict[Facade, float] = field(default_factory=dict)
    comfort_lower_bound_c: float | None = None


def _priority(share: float, high: float, medium: float) -> str:
    if share >= high:
        return "high"
    if share >= medium:
        return "medium"
    return "low"


def generate_recommendations(inputs: RecommendationInputs) -> tuple[Recommendation, ...]:
    design = inputs.design
    shares = inputs.breakdown.loss_fractions()
    recommendations: list[Recommendation] = []
    heating = inputs.climate_objective is not ClimateObjective.COOLING_DOMINATED

    glazing_share = shares.get("windows_conduction", 0.0)
    shutters = {facade: window.shutter for facade, window in design.windows.items()
                if window.wwr > 0.0}
    glazed_area = inputs.model.exterior_window_area_by_facade()
    total_glazed = sum(glazed_area.values())
    unshuttered = [facade.value for facade, shutter in shutters.items()
                   if not shutter.present
                   and (total_glazed <= 0.0
                        or glazed_area.get(facade, 0.0) / total_glazed
                        >= MINOR_GLAZING_SHARE)]
    if glazing_share >= GLAZING_LOSS_SHARE and unshuttered:
        recommendations.append(Recommendation(
            code="fit_night_shutters",
            title="Fit insulated night shutters to the glazing",
            detail=(f"Glazing carries {glazing_share * 100:.0f} percent of total envelope "
                    f"loss while {', '.join(unshuttered)} facing windows have no shutter. "
                    "Closing an insulated shutter over the glass after sunset removes "
                    "most of that path for the hours when it costs the most."),
            priority=_priority(glazing_share, 0.40, GLAZING_LOSS_SHARE),
            triggered_by={"glazing_loss_share": round(glazing_share, 3),
                          "unshuttered_facades": float(len(unshuttered))},
            expected_effect="Reduces overnight conduction loss through the glazing"))

    weak_compliance = [facade.value for facade, shutter in shutters.items()
                       if shutter.present and shutter.compliance < COMPLIANCE_RISK]
    if weak_compliance:
        lowest = min(shutter.compliance for shutter in shutters.values()
                     if shutter.present)
        recommendations.append(Recommendation(
            code="shutter_operation_dependency",
            title="Performance depends on shutters actually being closed",
            detail=(f"Assumed compliance on {', '.join(weak_compliance)} is "
                    f"{lowest * 100:.0f} percent. The result above is what happens at "
                    "that rate, not at perfect operation. Either report both cases or "
                    "make closing the shutters part of the standing routine."),
            priority="medium",
            triggered_by={"lowest_shutter_compliance": round(lowest, 3)},
            expected_effect="Names a dependency on human behaviour rather than hiding it"))

    infiltration_share = shares.get("infiltration", 0.0)
    if infiltration_share >= INFILTRATION_LOSS_SHARE:
        recommendations.append(Recommendation(
            code="reduce_infiltration",
            title="Seal the envelope, keeping the fresh air minimum",
            detail=(f"Air leakage carries {infiltration_share * 100:.0f} percent of total "
                    f"loss at an assumed {design.ventilation.infiltration_ach:.2f} air "
                    "changes per hour. Sealing joints, door frames and service "
                    f"penetrations towards the "
                    f"{design.ventilation.minimum_fresh_air_ach:.2f} air changes per hour "
                    "health minimum recovers most of it. Do not go below that minimum."),
            priority=_priority(infiltration_share, 0.35, INFILTRATION_LOSS_SHARE),
            triggered_by={"infiltration_loss_share": round(infiltration_share, 3),
                          "infiltration_ach": design.ventilation.infiltration_ach},
            expected_effect="Reduces the air change heat loss without breaching "
                            "the ventilation constraint"))

    roof_share = shares.get("roof", 0.0)
    u_values = envelope_u_values(design, inputs.library)
    if roof_share >= ROOF_LOSS_SHARE or u_values["roof"] > ECO_NIWAS_ROOF_U:
        recommendations.append(Recommendation(
            code="insulate_roof",
            title="Add roof insulation",
            detail=(f"The roof carries {roof_share * 100:.0f} percent of envelope loss at "
                    f"U {u_values['roof']:.2f} W/m2K. A roof also radiates to a very cold "
                    "clear sky at this altitude, so it loses more than its area suggests. "
                    f"Eco-Niwas Samhita sets {ECO_NIWAS_ROOF_U:.1f} W/m2K for the cold "
                    "zone."),
            priority=_priority(max(roof_share, u_values["roof"] / 4.0), 0.35, 0.2),
            triggered_by={"roof_loss_share": round(roof_share, 3),
                          "roof_u_value": round(u_values["roof"], 3),
                          "code_limit_u": ECO_NIWAS_ROOF_U},
            expected_effect="Cuts the largest single conduction path on most light roofs"))

    if u_values["wall"] > ECO_NIWAS_WALL_U:
        recommendations.append(Recommendation(
            code="wall_u_above_code",
            title="Wall U-value is above the cold zone code limit",
            detail=(f"The wall assembly is U {u_values['wall']:.2f} W/m2K against the "
                    f"Eco-Niwas Samhita cold zone limit of {ECO_NIWAS_WALL_U:.1f}. "
                    "Adding insulation on the outside of the mass keeps the mass working "
                    "on the room side."),
            priority="high",
            triggered_by={"wall_u_value": round(u_values["wall"], 3),
                          "code_limit_u": ECO_NIWAS_WALL_U},
            expected_effect="Brings the envelope inside the residential code"))

    floor_share = shares.get("floor", 0.0)
    if floor_share >= FLOOR_LOSS_SHARE:
        recommendations.append(Recommendation(
            code="insulate_floor",
            title="Insulate under the slab and at the perimeter",
            detail=(f"The floor carries {floor_share * 100:.0f} percent of loss into "
                    "ground that freezes seasonally. Perimeter insulation usually buys "
                    "more than the same thickness spread across the whole slab."),
            priority=_priority(floor_share, 0.25, FLOOR_LOSS_SHARE),
            triggered_by={"floor_loss_share": round(floor_share, 3),
                          "floor_u_value": round(u_values["floor"], 3)},
            expected_effect="Reduces ground loss during the coldest months"))

    bridge_share = shares.get("thermal_bridge", 0.0)
    if bridge_share >= BRIDGE_LOSS_SHARE:
        recommendations.append(Recommendation(
            code="treat_thermal_bridges",
            title="Treat the junctions, not just the flat wall",
            detail=(f"Junction heat flow computed outside the whole building model adds "
                    f"{bridge_share * 100:.0f} percent of total loss. Wrapping insulation "
                    "continuously past the wall to floor and wall to roof junctions is "
                    "the cheapest part of the envelope to fix."),
            priority=_priority(bridge_share, 0.18, BRIDGE_LOSS_SHARE),
            triggered_by={"thermal_bridge_loss_share": round(bridge_share, 3),
                          "thermal_bridge_kwh": round(
                              inputs.breakdown.thermal_bridge_kwh, 1)},
            provenance=inputs.breakdown.thermal_bridge_provenance,
            expected_effect="Removes loss the one dimensional model cannot see"))

    ratios = facade_glazing_ratio(inputs.model)
    south_ratio = ratios.get(Facade.SOUTH, 0.0)
    solar = inputs.incident_solar_kwh_by_facade
    best_facade = max(solar, key=solar.get) if solar else None
    if heating and south_ratio < LOW_SOUTH_WWR and best_facade is Facade.SOUTH:
        recommendations.append(Recommendation(
            code="increase_south_glazing",
            title="Increase south facing glazing",
            detail=(f"The south facade collects {solar[Facade.SOUTH]:.0f} kWh of incident "
                    f"solar over the run period, more than any other face, but only "
                    f"{south_ratio * 100:.0f} percent of it is glazed. More south glazing "
                    "collects more of a free resource. Pair it with shutters so the "
                    "extra area does not cost more overnight than it gains by day."),
            priority="high" if south_ratio < 0.15 else "medium",
            triggered_by={"south_glazing_ratio": round(south_ratio, 3),
                          "south_incident_solar_kwh": round(solar[Facade.SOUTH], 1)},
            expected_effect="Raises daytime solar gain into the mass"))
    if heating and south_ratio > HIGH_SOUTH_WWR and not any(
            shutter.present for shutter in shutters.values()):
        recommendations.append(Recommendation(
            code="south_glazing_without_shutters",
            title="Large south glazing without shutters is a net overnight loss",
            detail=(f"South glazing is {south_ratio * 100:.0f} percent of the facade with "
                    "no night insulation. Past roughly half the facade, unshuttered "
                    "glazing gives back at night more than it collects by day."),
            priority="high",
            triggered_by={"south_glazing_ratio": round(south_ratio, 3)},
            expected_effect="Prevents the glazing area from working against the design"))

    decrement = inputs.comfort.decrement_factor
    lag = inputs.comfort.time_lag_h
    if decrement is not None and decrement > DECREMENT_HEAVY:
        recommendations.append(Recommendation(
            code="add_thermal_mass",
            title="Add thermal mass on the room side of the insulation",
            detail=(f"The indoor swing is {decrement * 100:.0f} percent of the outdoor "
                    f"swing"
                    + (f" with a {lag:.1f} hour lag" if lag is not None else "")
                    + ". That means daytime solar heat is not being stored, so it is gone "
                    "by the time it is needed. Mass belongs inside the insulation line."),
            priority="high" if decrement > 0.6 else "medium",
            triggered_by={"decrement_factor": round(decrement, 3),
                          "time_lag_h": round(lag, 2) if lag is not None else 0.0},
            expected_effect="Carries afternoon solar gain through the night"))
    elif (lag is not None and lag < TIME_LAG_HOURS and decrement is not None
          and decrement > DECREMENT_COUPLED):
        recommendations.append(Recommendation(
            code="mass_placement",
            title="Thermal mass is present but not doing useful work",
            detail=(f"Indoor peak follows outdoor peak by only {lag:.1f} hours. Mass "
                    "separated from the room by insulation stores heat the room cannot "
                    "reach. Check that the dense layer sits on the inside face."),
            priority="medium",
            triggered_by={"time_lag_h": round(lag, 2),
                          "decrement_factor": round(decrement, 3)},
            expected_effect="Moves stored heat back into the occupied hours"))

    if inputs.condensation.hours > CONDENSATION_HOURS:
        surface = inputs.condensation.worst_surface or "the glazing"
        recommendations.append(Recommendation(
            code="control_condensation",
            title="Condensation risk on the glazing",
            detail=(f"{inputs.condensation.hours:.0f} hours fall below the indoor dew "
                    f"point at {surface}. Nine occupants exhale several litres of water a "
                    "day and it finds the coldest surface. Better glazing raises that "
                    "surface temperature; controlled ventilation removes the moisture. "
                    "Sealing the room without doing either makes it worse."),
            priority="high" if inputs.condensation.hours > 100 else "medium",
            triggered_by={"condensation_hours": round(inputs.condensation.hours, 1),
                          "minimum_inner_glazing_c": round(
                              inputs.condensation.minimum_inner_glazing_c or 0.0, 2)},
            expected_effect="Protects light transmission and prevents mould"))

    if inputs.buildability.seismic_flag:
        recommendations.append(Recommendation(
            code="seismic_check",
            title="The mass wall needs a structural check",
            detail=inputs.buildability.seismic_note or
                   "Thick mass walls in a high seismic zone need a structural check.",
            priority="high",
            triggered_by={"wall_thickness_m": round(
                inputs.buildability.wall_thickness_m, 3)},
            expected_effect="Flags a structural cost the thermal result does not show"))

    if design.plan.surface_to_volume > COMPACT_SURFACE_TO_VOLUME and heating:
        recommendations.append(Recommendation(
            code="compact_the_form",
            title="Compact the plan",
            detail=(f"The enclosure exposes {design.plan.surface_to_volume:.2f} square "
                    "metres of envelope per cubic metre of room. Indian cold region "
                    "guidance moves towards a ratio near one. Every square metre of "
                    "envelope removed is loss that never has to be insulated against."),
            priority="medium",
            triggered_by={"surface_to_volume": round(design.plan.surface_to_volume, 3),
                          "aspect_ratio": design.plan.aspect_ratio},
            expected_effect="Reduces total envelope area for the same floor area"))

    if inputs.cost.non_local_material_fraction > NON_LOCAL_FRACTION:
        recommendations.append(Recommendation(
            code="material_availability",
            title="A large share of the envelope cost is imported material",
            detail=(f"{inputs.cost.non_local_material_fraction * 100:.0f} percent of "
                    "envelope material cost is material that has to be trucked over the "
                    "passes, and the passes close. Substituting local mass for part of it "
                    "usually costs little thermally."),
            priority="medium",
            triggered_by={"non_local_material_fraction": round(
                inputs.cost.non_local_material_fraction, 3),
                "envelope_cost_inr": round(inputs.cost.envelope_inr, 0)},
            expected_effect="Reduces exposure to the construction season window"))

    if design.pcm.present and design.pcm.pcm_id:
        pcm = inputs.library.pcm(design.pcm.pcm_id)
        mean_operative = inputs.comfort.mean_operative_c
        if mean_operative is not None and pcm.melt_temperature_c > mean_operative + 4.0:
            recommendations.append(Recommendation(
                code="pcm_melt_point_too_high",
                title="The phase change material never melts",
                detail=(f"{pcm.name} melts at {pcm.melt_temperature_c:.1f} C while the "
                        f"room averages {mean_operative:.1f} C. A material that never "
                        "reaches its melt point stores nothing latent; it is dead weight "
                        "and dead cost. Choose one melting inside the operating band."),
                priority="high",
                triggered_by={"pcm_melt_c": pcm.melt_temperature_c,
                              "mean_operative_c": round(mean_operative, 2),
                              "pcm_cost_inr": round(inputs.cost.pcm_inr, 0)},
                expected_effect="Removes cost that buys no storage"))

    lower = inputs.comfort_lower_bound_c
    minimum = inputs.comfort.minimum_overnight_operative_c
    if heating and lower is not None and minimum is not None and minimum < lower:
        recommendations.append(Recommendation(
            code="overnight_shortfall",
            title="The design still falls below the comfort band overnight",
            detail=(f"Minimum overnight operative temperature is {minimum:.1f} C against "
                    f"a lower acceptability bound of {lower:.1f} C, a shortfall of "
                    f"{lower - minimum:.1f} K. The remaining gap has to be closed by the "
                    "envelope, by more collected solar, or by accepting a heating load."),
            priority="high",
            triggered_by={"minimum_overnight_operative_c": round(minimum, 2),
                          "comfort_lower_bound_c": round(lower, 2),
                          "shortfall_k": round(lower - minimum, 2)},
            provenance=Provenance.SIMULATED,
            expected_effect="States the residual gap rather than leaving it implied"))

    order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda item: order[item.priority])
    return tuple(recommendations)
