from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .air_quality import size_fresh_air
from .endurance import FLOOR_C, night_window, window_sum
from .errors import WeatherError
from .schema import (Arrangement, CardOrder, CommanderCard, DesignSpec, FuelModel,
                     SiteSpec, SourceRef, SourceStatus)
from .solar import horizon_altitude_deg, sun_position

TARGET_C = 14.0
SUNSPACE_MARGIN_K = 1.0
SUNSET_STEP_MINUTES = 5
EPW_COLUMNS = ("dry_bulb_c", "dew_point_c", "relative_humidity_pct", "pressure_pa",
               "global_horizontal_wh_m2", "direct_normal_wh_m2", "diffuse_horizontal_wh_m2",
               "wind_speed_m_s", "wind_direction_deg", "sky_cover_tenths")
TARGET_SOURCE = SourceRef(
    citation="DRDO space heating ToT, 17 +/- 3 C inside the shelter; 14 C is its lower edge",
    status=SourceStatus.VERIFIED)
TEXT = {
    "en": {
        "close_shutters": "Close the shutters on every window",
        "close_sunspace_door": "Close the sunspace door",
        "keep_sunspace_closed": "Keep the sunspace door shut; the sunspace does not get "
                                "warmer than the room today",
        "light_bukhari": "Light the bukhari",
        "no_bukhari": "No bukhari needed tonight",
        "fresh_air": "Keep at least {l_s:.0f} L/s ({m3_h:.0f} m3/h) of fresh air coming in "
                     "for {people} people; never close every vent while the bukhari burns",
        "fuel_low": "At tonight's rate the {fuel} runs out in {hours:.0f} h; send for more",
        "co_alarm": "Keep a working CO alarm by the bukhari. This card does not compute "
                    "carbon monoxide.",
        "all_night": "all night",
        "now": "now",
    },
    "hi": {
        "close_shutters": "सभी खिड़कियों के शटर बंद करें",
        "close_sunspace_door": "सनस्पेस का दरवाज़ा बंद करें",
        "keep_sunspace_closed": "सनस्पेस का दरवाज़ा बंद रखें; आज सनस्पेस कमरे से ज़्यादा "
                                "गरम नहीं होता",
        "light_bukhari": "बुखारी जलाएँ",
        "no_bukhari": "आज रात बुखारी की ज़रूरत नहीं",
        "fresh_air": "{people} लोगों के लिए कम से कम {l_s:.0f} लीटर/सेकंड ({m3_h:.0f} घन "
                     "मीटर/घंटा) ताज़ी हवा आने दें; बुखारी जलते समय सारे वेंट कभी बंद न करें",
        "fuel_low": "आज रात की खपत पर {fuel} {hours:.0f} घंटे में खत्म हो जाएगा; और मँगवाएँ",
        "co_alarm": "बुखारी के पास चालू CO अलार्म रखें। यह कार्ड कार्बन मोनोऑक्साइड की गणना "
                    "नहीं करता।",
        "all_night": "पूरी रात",
        "now": "अभी",
    },
}
FUEL_NAMES = {"en": {"kerosene": "kerosene", "diesel": "diesel", "lpg": "LPG", "wood": "wood",
                     "electricity": "power"},
              "hi": {"kerosene": "केरोसिन", "diesel": "डीज़ल", "lpg": "एलपीजी",
                     "wood": "लकड़ी", "electricity": "बिजली"}}


def nominal_index(day: date, hour: int) -> int:
    if day.month == 2 and day.day == 29:
        raise WeatherError("the typical year has no 29 February")
    day_of_year = date(2019, day.month, day.day).timetuple().tm_yday - 1
    return day_of_year * 24 + hour - 1


def splice_forecast(base: pd.DataFrame, utc_times: np.ndarray,
                    columns: Mapping[str, np.ndarray], utc_offset_h: float,
                    first_day: date, last_day: date) -> dict[str, np.ndarray]:
    if len(base) != 8760:
        raise WeatherError("the site weather file must hold one non leap year of hours")
    if last_day < first_day:
        raise WeatherError("the forecast window ends before it starts")
    source = np.asarray(utc_times).astype("datetime64[s]").astype(np.int64)
    if len(source) < 2 or np.any(np.diff(source) <= 0):
        raise WeatherError("the forecast timestamps must be strictly increasing")
    result = {"sky_cover_tenths": base["total_sky_cover"].to_numpy(dtype=float).copy()}
    for name in EPW_COLUMNS:
        if name != "sky_cover_tenths":
            result[name] = base[name].to_numpy(dtype=float).copy()
    offset = int(round(utc_offset_h * 3600.0))
    day = first_day
    while day <= last_day:
        if not (day.month == 2 and day.day == 29):
            for hour in range(1, 25):
                local = np.datetime64(datetime.combine(day, time()) + timedelta(hours=hour),
                                      "s").astype(np.int64)
                target = local - offset
                if target < source[0] or target > source[-1]:
                    raise WeatherError(f"the forecast does not reach {day} hour {hour}")
                index = nominal_index(day, hour)
                for name, values in columns.items():
                    result[name][index] = float(np.interp(target, source, values))
        day += timedelta(days=1)
    result["relative_humidity_pct"] = np.clip(result["relative_humidity_pct"], 0.0, 100.0)
    result["dew_point_c"] = np.minimum(result["dew_point_c"], result["dry_bulb_c"])
    for name in ("global_horizontal_wh_m2", "direct_normal_wh_m2", "diffuse_horizontal_wh_m2"):
        result[name] = np.clip(result[name], 0.0, None)
    result["sky_cover_tenths"] = np.clip(np.round(result["sky_cover_tenths"]), 0.0, 10.0)
    return result


def terrain_sunset(site: SiteSpec, day: date) -> datetime | None:
    last_sun = None
    for minute in range(12 * 60, 24 * 60, SUNSET_STEP_MINUTES):
        altitude, azimuth = sun_position(site.latitude_deg, site.longitude_deg,
                                         site.time_zone_h, day, minute / 60.0)
        if altitude > max(0.0, horizon_altitude_deg(site.horizon, azimuth)):
            last_sun = minute
    if last_sun is None:
        return None
    return datetime.combine(day, time()) + timedelta(minutes=last_sun + SUNSET_STEP_MINUTES)


def _clock(moment: pd.Timestamp | datetime) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def _night(series: pd.Series, night_of: date) -> pd.Series:
    start, end = night_window(night_of)
    return series[(series.index >= start) & (series.index < end)]


def commander_card(*, post_id: str, post_name: str, design: DesignSpec, site: SiteSpec,
                   night_of: date, issued_at: datetime, lang: str, people: int,
                   operative_c: pd.Series, room_air_c: pd.Series, outdoor_c: pd.Series,
                   sunspace_air_c: pd.Series | None, heating_j: pd.Series, fuel: FuelModel,
                   stock_units: float | None, forecast_source: str,
                   forecast_age_h: float | None, model_error_k: float | None,
                   calibrated_on: date | None, notes: Sequence[str] = ()) -> CommanderCard:
    if lang not in TEXT:
        raise ValueError(f"no card text in {lang!r}; use one of {sorted(TEXT)}")
    words = TEXT[lang]
    night = _night(operative_c, night_of)
    if night.empty:
        raise WeatherError(f"the run holds no hours of the night of {night_of}")
    outside = _night(outdoor_c, night_of)
    lowest_at = night.idxmin()
    below = float((night < TARGET_C).sum())
    orders: list[CardOrder] = []

    has_shutters = any(window.shutter.present for window in design.windows.values())
    sunset = terrain_sunset(site, night_of)
    if has_shutters:
        moment = sunset or datetime.combine(night_of, time(16, 0))
        orders.append(CardOrder(time=_clock(moment), key="close_shutters",
                                action=words["close_shutters"]))

    if design.arrangement is Arrangement.SUNSPACE and sunspace_air_c is not None:
        day_start = pd.Timestamp(datetime.combine(night_of, time(8, 0)))
        day_end = pd.Timestamp(datetime.combine(night_of, time(20, 0)))
        span = (sunspace_air_c.index >= day_start) & (sunspace_air_c.index < day_end)
        gain = sunspace_air_c[span] - room_air_c.reindex(sunspace_air_c[span].index)
        warmer = gain[gain >= SUNSPACE_MARGIN_K]
        if warmer.empty:
            orders.append(CardOrder(time=_clock(day_start), key="keep_sunspace_closed",
                                    action=words["keep_sunspace_closed"]))
        else:
            orders.append(CardOrder(time=_clock(warmer.index[-1] + pd.to_timedelta(1, unit="h")),
                                    key="close_sunspace_door",
                                    action=words["close_sunspace_door"]))

    start, end = night_window(night_of)
    cold = night[night < TARGET_C]
    if cold.empty:
        orders.append(CardOrder(time=words["all_night"], key="no_bukhari",
                                action=words["no_bukhari"]))
    else:
        orders.append(CardOrder(time=_clock(cold.index[0]), key="light_bukhari",
                                action=words["light_bukhari"]))

    sizing = size_fresh_air(site.elevation_m, design.occupancy.metabolic_w_per_person)
    fresh_l_s = sizing.fresh_air_l_s_per_person * people
    orders.append(CardOrder(time=words["all_night"], key="fresh_air",
                            action=words["fresh_air"].format(l_s=fresh_l_s,
                                                             m3_h=fresh_l_s * 3.6,
                                                             people=people)))

    night_kwh = window_sum(heating_j, start, end) / 3_600_000.0 if not heating_j.empty else 0.0
    tonight = fuel.units_for(night_kwh)
    hours = (end - start).total_seconds() / 3600.0
    rate = tonight / hours if hours > 0 else 0.0
    fuel_out = None
    if stock_units is not None and rate > 0.0:
        fuel_out = stock_units / rate
        if stock_units < 2.0 * tonight:
            orders.append(CardOrder(
                time=words["now"], key="fuel_low",
                action=words["fuel_low"].format(fuel=FUEL_NAMES[lang].get(fuel.fuel, fuel.fuel),
                                                hours=fuel_out)))

    inside_min = float(night.min())
    if inside_min < FLOOR_C or (stock_units is not None and stock_units < tonight):
        status = "red"
    elif inside_min < TARGET_C or (stock_units is not None and stock_units < 2.0 * tonight):
        status = "amber"
    else:
        status = "green"
    return CommanderCard(
        post_id=post_id, post_name=post_name, night_of=night_of, issued_at=issued_at,
        lang=lang, status=status,
        outside_min_c=float(outside.min()) if not outside.empty else None,
        inside_min_c=inside_min, inside_min_at=_clock(lowest_at),
        hours_below_target=below, target_c=TARGET_C,
        sunset=None if sunset is None else _clock(sunset), orders=tuple(orders),
        fuel=fuel.fuel, fuel_unit=fuel.unit, fuel_tonight_units=tonight,
        stock_units=stock_units, fuel_out_hours=fuel_out, fresh_air_l_s=fresh_l_s,
        model_error_k=model_error_k, calibrated_on=calibrated_on,
        forecast_source=forecast_source, forecast_age_h=forecast_age_h,
        safety=(words["co_alarm"],), notes=tuple(notes), target_source=TARGET_SOURCE)
