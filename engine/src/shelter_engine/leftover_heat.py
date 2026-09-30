from __future__ import annotations

from dataclasses import dataclass

from .schema import FuelModel, SourceRef, SourceStatus

HIM_TAPAK_L_PER_H = (0.5, 0.7)
HIM_TAPAK_SOURCE = SourceRef(
    citation=("Him Tapak kerosene space heating device, designed and developed by DIPAS "
              "(DRDO): uses 500 to 700 ml of kerosene an hour"),
    status=SourceStatus.VERIFIED,
    url="https://krishnaallied.com/product/space-heating-device-himtapak/",
    note=("No heat output rating is published, so the tool converts the night's heat to "
          "litres with its own kerosene model and then to burn hours at the published "
          "consumption. Whether one unit keeps up with the peak needs DIPAS's rating."))
SOLAR_SYSTEM_SOURCE = SourceRef(
    citation=("DIHAR solar thermal space heating system for shelters in cold conditions, "
              "DRDO transfer of technology"),
    status=SourceStatus.PENDING,
    url="https://drdo.gov.in/drdo/sites/default/files/tot/DOSTTbSfSHoSiCC11022025_0.pdf",
    note=("The transfer of technology document gives no storage or collector capacity, "
          "so the tool reports the heat the system must hold for one night rather than a "
          "number of units. Enter DIHAR's tank capacity to turn it into units."))


@dataclass(frozen=True)
class LeftoverHeat:
    night_heating_kwh: float
    night_peak_w: float | None
    fuel: str | None
    litres: float | None
    him_tapak_hours_low: float | None
    him_tapak_hours_high: float | None
    him_tapak_input_kw: tuple[float, float] | None
    solar_storage_kwh: float
    notes: tuple[str, ...]


def leftover_heat(night_heating_kwh: float, night_peak_w: float | None,
                  fuel: FuelModel | None) -> LeftoverHeat:
    heat = max(0.0, float(night_heating_kwh))
    notes: list[str] = []
    litres = hours_low = hours_high = None
    input_kw = None
    if fuel is not None and fuel.fuel == "kerosene":
        litres = fuel.units_for(heat)
        low_rate, high_rate = HIM_TAPAK_L_PER_H
        hours_low = litres / high_rate
        hours_high = litres / low_rate
        input_kw = (low_rate * fuel.lower_heating_value_kwh_per_unit,
                    high_rate * fuel.lower_heating_value_kwh_per_unit)
        if night_peak_w is not None and night_peak_w / 1000.0 > input_kw[1]:
            notes.append(
                f"The night peaks at {night_peak_w / 1000.0:.1f} kW, more than the "
                f"{input_kw[1]:.1f} kW of fuel one Him Tapak burns at its highest "
                "published rate, so one unit cannot hold the room at 14 C through the "
                "coldest hour.")
    elif fuel is not None:
        litres = fuel.units_for(heat)
        notes.append(f"Him Tapak burns kerosene; this run is priced in {fuel.fuel}.")
    if heat <= 0.0:
        notes.append("The design needs no heat on the design night to stay at 14 C.")
    return LeftoverHeat(
        night_heating_kwh=heat, night_peak_w=night_peak_w,
        fuel=fuel.fuel if fuel else None, litres=litres,
        him_tapak_hours_low=hours_low, him_tapak_hours_high=hours_high,
        him_tapak_input_kw=input_kw, solar_storage_kwh=heat, notes=tuple(notes))
