# Inputs and outputs — exact specification

Everything here is taken from the code and from a live run, not from memory.

**Reference run used for every example value:** design `barrack_sunspace`, site Leh,
simulated week 10–16 January 2019, EnergyPlus 24.2.0, 3.7 s per run, with the current
practice reference and the 8-run uncertainty sweep.

Use these numbers in any mockup. A mockup built on invented numbers will be implemented
wrong, because the real values tell a different story (see section C).

---

## A. Inputs

### A1. What the user enters on the Design surface

| Control | Field | Type / range | Default | Notes |
| --- | --- | --- | --- | --- |
| Location | `site_id` | choice | `leh` | Only one site exists: Leh, Ladakh |
| What it is for | `plan.use` | 7 choices | `barrack` | Sets area, ceiling, rooms and heat gains, see A3 |
| Occupants | `occupancy.occupants` | 0–200 | 10 for barrack | Rescales equipment, lighting, moisture |
| Floor area | `plan.floor_area_m2` | 4–2000 m² | 90 for barrack | Advanced |
| Orientation | `plan.azimuth_deg` | degrees | 180 | Advanced |
| Comfort target | `comfort_model_id` | `imac_2016_nv`, `imac_r_2022_nv` | `imac_2016_nv` | Advanced. The two are numerically identical today |
| Search budget | population × generations | 100, 300 or 500 designs | 100 | Advanced. ~3.7 s per design, so 100 ≈ 6 min |
| Find the best shelter | action | — | — | Starts the live search |

### A2. What the search is allowed to change (fixed today)

| Lever | Range |
| --- | --- |
| Wall mass layer thickness | 0.15–0.55 m |
| Insulation layer thickness | 0.02–0.18 m |
| South window-to-wall ratio | 0.08–0.45 |

Objectives, all three at once: minimise degree hours outside the comfort band, minimise
cost, maximise the worst-night margin.
Constraints: condensation ≤ 2,000 h, wall thickness ≤ 0.9 m.

### A3. Shelter programmes (`/api/shelter-uses`)

| Use | Label | m² per person | Occupants | Area m² | Ceiling m | Rooms |
| --- | --- | --- | --- | --- | --- | --- |
| barrack | Troop accommodation | 9.0 | 10 | 90.0 | 2.6 | Sleeping bay east, Sleeping bay west, Kit store, Ablution, Entry lobby |
| medical_post | Medical post | 12.0 | 6 | 72.0 | 2.8 | Treatment room, Ward, Pharmacy and store, Staff room, Entry lobby |
| command_post | Command and communications post | 6.0 | 8 | 48.0 | 2.7 | Operations room, Radio room, Rest bay, Equipment store, Entry lobby |
| kitchen_mess | Cookhouse and mess | 2.2 | 24 | 52.8 | 3.0 | Dining hall, Kitchen, Dry store, Wash up, Entry lobby |
| workshop | Workshop | 16.0 | 4 | 64.0 | 3.4 | Work bay, Tool store, Office, Entry lobby |
| store | Unheated store | 30.0 | 1 | 30.0 | 3.0 | Store floor, Entry lobby |
| relief_shelter | Family relief shelter | 5.0 | 5 | 25.0 | 2.5 | Living and sleeping, Second sleeping room, Cooking corner, Entry lobby |

### A4. Design library (`/api/designs`)

Candidates (9): `barrack_single_room`, `barrack_sunspace`, `barrack_trombe_wall`,
`medical_post_single_room`, `medical_post_sunspace`, `medical_post_trombe_wall`,
`single_room`, `sunspace`, `trombe_wall`.
References (3): `current_practice_masonry`, `traditional_mud_stone`,
`modern_insulated_prefab`.

Arrangements: `single_room`, `sunspace`, `trombe_wall`, `vestibule`.

### A5. Advanced inputs (Construction tab and All inputs)

| Group | Fields and ranges |
| --- | --- |
| Plan | aspect ratio 0.25–6 · ceiling height 1.8–6 m |
| Wall, roof, floor | up to 10 layers each · material from 15 · thickness 0.1 mm–2 m |
| Windows, per facade | window-to-wall ratio 0–0.9 · glazing system (3) · sill height 0–3 m · overhang depth 0–3 m · shutter on/off, R-value, closed hours, compliance |
| Ventilation | infiltration 0–15 ACH · fresh-air minimum · night ventilation on/off, rate, hours |
| Occupancy | metabolic W/person · equipment W · lighting W · cooking W · occupied hours · daytime fraction · extra moisture g/h |
| Arrangement | sunspace depth 0.6–6 m, glazed roof · Trombe gap 0.02–0.5 m, width fraction · vestibule depth 0.6–3 m, facade · zone coupling ACH and hours |
| Phase change material | product (4 Rubitherm) and layer |
| Run | period (default winter 10–16 Jan) · timesteps 1–60 per hour (default 6) · per-surface window (default 12–14 Jan) · heating setpoint · horizon shading on/off |

### A6. Supplied by the system, never typed by the user

| Input | Value |
| --- | --- |
| Weather year | TMYx 2011–2025, Leh station WMO 427053, 8,760 hours |
| Site | 34.136 N, 77.547 E, 3,255.9 m, UTC+5:30, heating dominated |
| Terrain | SRTM, 2,601 points, 10 km square, ridges to 1,151 m above site |
| Horizon | 36 directions, blocked 1.9°–12.2° |
| Ground temperature | 12 monthly values at 0.5 m, −11.9 to 10.1 °C |
| Materials | 15 (5 from Layer 0, 10 filled from ASHRAE, marked pending) |
| Glazing systems | 3 · Phase change materials: 4 |
| Live conditions | Open-Meteo, context only, never a simulation input |

---

## B. Outputs

All values below come from the reference run.

### B1. The recommendation (search result, `ParetoResult` + `ParetoPoint`)

| Output | Field | Example |
| --- | --- | --- |
| Progress | `generation` / `generations_planned` | 3 of 5 |
| Designs simulated | `evaluated` | 60 |
| Still running | `complete` | false |
| Chosen design | `knee_hash` | the balanced point on the front |
| Per point | `metrics.minimum_overnight_operative_c` | 5.6 °C |
| Per point | `objectives.total_cost_inr` | ₹11.58 L |
| Per point | `metrics.degree_hours_total_kh`, `hours_outside_band`, `condensation_hours`, `wall_thickness_m`, `surface_to_volume`, `worst_night_margin_k`, `time_lag_h` | — |
| Per point | `feasible`, `constraint_violation` | — |
| Per point | `flags` | `seismic_structural_check_required`, `condensation_hours_present` |

Not on search points: heating demand and fuel. The search runs without heating, so these
exist only after a full simulation of the chosen design.

### B2. Comfort (`comfort`)

| Output | Field | Example |
| --- | --- | --- |
| Minimum overnight operative temperature | `minimum_overnight_operative_c` | **5.6 °C** |
| Lowest operative temperature | `minimum_operative_c` | 4.3 °C |
| Highest daytime operative temperature | `maximum_daytime_operative_c` | 12.1 °C |
| Mean operative temperature | `mean_operative_c` | 8.7 °C |
| Coldest day in the period | `design_day_date`, `minimum_on_design_day_c` | 16 Jan, 4.3 °C |
| Hours below comfort band | `hours_below_band` | **168 of 168** |
| Occupied hours outside band | `occupied_hours_outside_band` | 91 |
| Degree hours outside band | `degree_hours_total_kh` | 1,299 Kh |
| Hours below DIHAR operational target | `fixed_target_hours_below` | 84 |
| Worst-night margin | `worst_night_margin_k` | −12.1 K |
| Mean daily swing | `mean_daily_swing_k` | 3.5 K |
| Decrement factor / time lag | `decrement_factor`, `time_lag_h` | 0.14 / 1 h |

### B3. Against current construction (`reference`)

| Output | Field | Example |
| --- | --- | --- |
| Reference | `reference_name` | current_practice_masonry (citation pending) |
| Overnight minimum improvement | `delta_minimum_overnight_k` | **+20.1 K** (reference −14.5 °C) |
| Degree hours change | `delta_degree_hours_kh` | −2,985 Kh (reference 4,284) |
| Heating demand change | `delta_heating_demand_kwh` | −2,168 kWh (reference 2,499) |
| Diesel avoided | `fuel_saved_units` | **291 litres** over the week |
| Rupees saved | `rupees_saved_per_year` | withheld, fuel price not set |
| Cost change | `delta_cost_inr` | **+₹7.45 L** (reference ₹4.13 L) |

### B4. Energy (`energy`)

| Output | Field | Example |
| --- | --- | --- |
| Remaining heating demand | `heating_demand_kwh` | 331 kWh for the simulated week |
| Peak heating | `peak_heating_w` | 3,963 W |
| Fuel | `fuel`, `fuel_units` | diesel, 44.4 L |
| Fuel cost | `fuel_cost_inr` | withheld |

Not computed: kWh per m² per year. The default run is one winter week, not a year.

### B5. Heat balance (`breakdown`, kWh over the week, negative is loss)

| Path | Example |
| --- | --- |
| Floor | −120 |
| Infiltration | −114 |
| Roof | −76 |
| Walls | −33 |
| Window conduction | +1.7 |
| Window solar gain | +14 |
| From sunspace (interzone) | +90 |
| Internal gains | +166 |
| Thermal bridges | 0 (derived, not yet from ANSYS) |

### B6. Condensation (`condensation`)

Hours 26 · worst surface `ROOM_WALL_WEST_WIN` · worst margin −2.7 K · coldest inner glazing
−5.3 °C · peak indoor RH 66 %.

### B7. Cost and buildability (`cost`, `buildability`)

| Output | Example |
| --- | --- |
| Total delivered material cost | ₹11.58 L (provisional, unsourced rates) |
| Envelope / glazing / shutters / PCM | ₹9.10 L / ₹2.48 L / 0 / 0 |
| Highest mason skill needed | skilled |
| Imported material share | 71 % |
| Wall thickness | 540 mm |
| Surface to volume | 1.22 |
| Seismic flag | yes: walls ≥ 450 mm need a structural check |
| Fresh-air minimum met | yes |
| Heaviest wall | 851 kg/m² |

### B8. Uncertainty (`uncertainty`, 8 corner runs)

| Metric | Nominal | Band | Dominant input |
| --- | --- | --- | --- |
| Minimum overnight operative | 5.6 °C | **1.7 to 7.1 °C** | Envelope air leakage (0.3–1.0 ACH) |
| Degree hours | 1,299 Kh | 1,046 to 1,909 Kh | Envelope air leakage |

### B9. Recommendations (`recommendations`, generated from the numbers)

| Priority | Title |
| --- | --- |
| high | Insulate under the slab and at the perimeter |
| high | The mass wall needs a structural check |
| high | The design still falls below the comfort band overnight |
| medium | Seal the envelope, keeping the fresh air minimum |
| medium | Condensation risk on the glazing |
| medium | A large share of the envelope cost is imported material |

### B10. Hourly series (`hourly`, 168 points for the week)

`operative_temperature_c`, `air_temperature_c`, `outdoor_temperature_c`,
`comfort_lower_bound_c`, `comfort_upper_bound_c`, `indoor_relative_humidity_pct`,
`reference_operative_temperature_c`.

Per surface (`surfaces`, 18 surfaces, per hour over the surface window): inside face
temperature, outside face temperature, conduction W, plus incident solar W/m².

### B11. Geometry for the viewport (`/api/geometry`)

| Output | Example |
| --- | --- |
| Arrangement | sunspace |
| Azimuth | 180° |
| Surfaces | 18: floor, roof, wall, window, partition |
| Zone volumes | ROOM 234 m³ + SUNSPACE 66 m³ = 300 m³ |
| Per surface | name, zone, kind, facade, polygon, normal, area, material colour, finish, opacity, parent |
| Layout | Troop accommodation: Sleeping bay east 30.6 m², Sleeping bay west 30.6, Kit store 9.0, Ablution 10.8, Entry lobby 9.0 (unheated) |
| Partitions | 4, total 51.3 m², entered as internal mass |
| Roof | flat, at 2.6 m |

Built detail drawn from the design (no result painted on it): stone plinth, parapet and
coping, entrance door, entry steps, ribbed deck if the roof is sheet metal, window frames.

### B12. Air exchange (`airflow`)

| Zone / pair | Example |
| --- | --- |
| ROOM | 234 m³, 0.55 ACH, 133 m³/h through the envelope |
| SUNSPACE | 66 m³, 0.54 ACH |
| Sunspace → room | 38 m³/h mean, 132 m³/h peak |

Whole-zone flow only. Not a velocity field.

### B13. Sun and terrain (`/api/sun-path`, `/api/terrain`, 12 January)

| Output | Example |
| --- | --- |
| Astronomical daylight | 10.0 h (07:40–17:20) |
| Daylight clear of the ridge | 8.7 h (08:40–17:00) |
| Lost to the valley | 1.3 h |
| Highest sun altitude that day | **34.6°** |
| Hour-by-hour sun | altitude, azimuth, above horizon, blocked by terrain |
| Terrain mesh | real SRTM surface |

### B14. Climate (`/api/climate`)

Annual mean −0.8 °C · range −31.8 to 27.5 °C · heating degree days 6,878 ·
global horizontal 2,172 kWh/m² · direct normal 2,454 kWh/m² · wind 1.2 m/s ·
design day 3 Dec, −29.4 to −12.1 °C · station pressure 69,287 Pa against 67,857 expected ·
air density 0.886 kg/m³ against 1.296 at sea level · monthly values · coldest week ·
horizon profile.

### B15. Trust

| Output | Example |
| --- | --- |
| Assumptions | 24, each with source and status |
| Provenance warnings | materials unsourced, capital cost unsourced, comfort model unverified, comfort models identical, reference designs uncited, fuel price unset |
| Solver notices | 6 informational from EnergyPlus |
| Validation | none committed yet |
| Detailed CFD | not run for this design |

### B16. Live conditions (`/api/live-weather`)

12.4 °C · 48 % RH · 69,640 Pa · wind 0.4 m/s · source Open-Meteo · never a simulation input.

---

## C. The mockup against reality

Verdicts:
**KEEP** real and shown correctly ·
**FIX VALUE** real field, wrong number ·
**NOT BUILT** could exist, does not yet ·
**CANNOT** would misrepresent what was simulated.

### C1. Shell and navigation

| Mockup element | Verdict | Change |
| --- | --- | --- |
| Logo, "Entropy Zero", Design / Evidence / Report | KEEP | — |
| User avatar, top right | NOT BUILT | No accounts exist. Remove |
| Set up → Searching → Recommended | KEEP | — |
| "Leh · Troop accommodation · 9 people · Edit" | KEEP | Barrack default is 10 people |

### C2. Recommendation rail

| Mockup element | Verdict | Change |
| --- | --- | --- |
| Name "Himalaya Haven" | NOT BUILT | Designs have no display name. Use a name built from real properties: "Sunspace barrack, 540 mm wall" |
| "Maintains 16.8 °C minimum overnight" | FIX VALUE | Real: **5.6 °C** (band 1.7–7.1) |
| "7.4 °C warmer than current construction" | FIX VALUE | Real: **+20.1 K**; current construction −14.5 °C |
| Implied comfortable | FIX VALUE | Real: below the comfort band **168 of 168 hours**. Must be stated |
| Estimated cost ₹28.4 L (−18 %) | FIX VALUE | Real: **₹11.58 L, +₹7.45 L more** than current construction. Needs a "provisional" label |
| Energy demand 42 kWh/m²·yr (−32 %) | NOT BUILT | Annual per-m² is not computed. Real: **331 kWh for the week, −87 %**, or **291 L diesel avoided** |
| "High wind exposure (needs stronger sealing)" | CANNOT | Wind exposure is not computed |
| "Limited solar gain in winter" | CANNOT | Not a generated finding |
| "Requires local stone availability" | CANNOT | Opposite of the real finding: 71 % of envelope cost is imported |
| Replace flags with | — | The 6 real recommendations in B9 |
| Pareto: min overnight vs cost | KEEP | Both on search points. Real axes ≈ 0–8 °C and ₹8–15 L |
| Chosen / feasible / infeasible / front | KEEP | — |

### C3. Viewport controls

| Mockup element | Verdict | Change |
| --- | --- | --- |
| 3D / Plan / Section | KEEP | Plan and sections exist; move them into the viewport |
| Construction / Surface temperature / Incident solar | KEEP | — |
| "Interior estimate 16.2 °C (min overnight)" | CANNOT as drawn | The headline number is the solved minimum (5.6 °C). Air inside the room is shown only from an ANSYS Fluent field (`/api/cfd/{fingerprint}`), labelled with solver and hour; with no field the toggle states that none exists |
| Shell Solid · Overlays Rooms · Render Balanced · Scale Spectral | KEEP | Overlays also has Built detail and Air exchange |
| Full screen | KEEP | — |

### C4. The render

| Mockup element | Verdict | Change |
| --- | --- | --- |
| Snow-capped Himalaya photograph | CANNOT as the site | That is not the terrain at this site. Use the real SRTM terrain, better shaded. A photo is only acceptable in a presentation mode labelled "illustrative backdrop" |
| Pitched gable roof | CANNOT | The simulated roof is flat at 2.6 m. A pitched roof changes roof area and solar gain. Draw a flat roof with parapet, which is also what Ladakh barracks look like |
| Stone rubble walls | FIX VALUE | Texture must follow the wall material. This design is rammed earth, insulation and plaster, not stone |
| Rainbow gradient over walls and interior | CANNOT | EnergyPlus gives one temperature per surface per hour, so each face is one flat colour. A smooth gradient invents data |
| Thermal colours while "Construction" is selected | CANNOT | Construction mode shows material colours. The mockup contradicts its own selection |
| Beds and tables in a cutaway | CANNOT | Furniture is not modelled. Rooms and partitions are real and can be shown in the cutaway |
| Section cutaway | KEEP | Section cut exists |
| Glazed sunspace on the south | KEEP | Correct for this arrangement |
| Compass | KEEP | — |

### C5. HUD and time bar

| Mockup element | Verdict | Change |
| --- | --- | --- |
| Arrangement "3 × 3 × 2" | FIX VALUE | Real: "sunspace" |
| Surface count 142 | FIX VALUE | Real: **18** |
| Volume 312 m³ | FIX VALUE | Real: **300 m³** |
| Azimuth 162° | FIX VALUE | Real: **180°** |
| Sun altitude 58° | FIX VALUE | Impossible at Leh in January. Real maximum on 12 Jan: **34.6°** |
| Sun azimuth 128° | FIX VALUE | Take from the sun path at the chosen hour |
| Play, 12:00–24:00 scrubber | KEEP | — |
| Daylight 8.6 h | FIX VALUE | Real: **10.0 h** |
| Clear of ridge 6.2 h | FIX VALUE | Real: **8.7 h** |
| Lost to valley 2.4 h | FIX VALUE | Real: **1.3 h** |

### C6. Search bar

| Mockup element | Verdict | Change |
| --- | --- | --- |
| "Searching for better designs…" | KEEP | — |
| "Generation 42 of 200 · 24,680 designs simulated" | FIX VALUE | Budgets are 100, 300 or 500 designs. 24,680 at 3.7 s each is about 25 hours. Real: "Generation 3 of 5 · 60 designs simulated" |
| "Current best" thumbnail | KEEP | Fix the name and values |

### C7. Real outputs the mockup leaves out

These exist, are strong, and should be in the design:

- Uncertainty band on the headline: **5.6 °C, range 1.7–7.1, driven by air leakage**
- Still below comfort band **168 of 168 hours** (the honest headline)
- **291 L diesel avoided** over the week
- Condensation **26 h** on the west window
- Seismic flag on the 540 mm wall
- Live conditions **12.4 °C now**
- "Cost provisional" and "fuel savings withheld" labels
- Air exchange overlay
- Heat balance: floor and infiltration are the two largest losses

---

## D. Prompt to regenerate the mockup exactly

```
Regenerate this Entropy Zero Design-surface mockup keeping the same layout, component
style, spacing and light theme. Change only the content, to match the real engine
output below. Every number shown must be one of these.

TOP BAR: logo, "Entropy Zero", "Passive Shelter Design for DRDO", tabs Design (active),
Evidence, Report. No user avatar. At right: "Live conditions · 12.4 °C now".

LEFT RAIL
- Stepper: Set up ✓ → Searching → Recommended (active).
- Context chip: "Leh · Troop accommodation · 10 people · Edit".
- Title: "Sunspace barrack, 540 mm wall", badge RECOMMENDED.
- Sentence: "Holds 5.6 °C at its coldest overnight, 20.1 K warmer than current
  construction (−14.5 °C). It is still below the comfort band all 168 hours of the
  week."
- Uncertainty line under the sentence: "Range 1.7 to 7.1 °C, driven by envelope air
  leakage."
- Three metric cards:
  1. "Min. overnight" 5.6 °C, "+20.1 K vs current"
  2. "Material cost" ₹11.58 L, "+₹7.45 L vs current", small tag "provisional"
  3. "Diesel avoided" 291 L, "this week", small tag "₹ withheld, no fuel price"
- Findings list (real recommendations):
  high  "Insulate under the slab and at the perimeter"
  high  "The mass wall needs a structural check"
  high  "Still below the comfort band overnight"
  med   "Seal the envelope, keeping the fresh air minimum"
  med   "Condensation risk on the glazing (26 h)"
  med   "71 % of envelope cost is imported material"
- Pareto chart: y "Min. overnight temperature (°C)" 0–8, x "Material cost (₹ L)" 8–15,
  about 60 points, chosen design highlighted near (11.6, 5.6), infeasible points as ×.

VIEWPORT TOOLBAR: 3D (active) | Plan | Section. "What is painted": Construction
(active) | Surface temperature | Incident solar. A separate dashed toggle "Interior
estimate" with no number. Dropdowns: Shell Solid, Overlays Rooms, Render Balanced,
Scale Spectral. Full screen icon.

3D MODEL, construction mode, so material colours only, no thermal colours:
- A single-storey flat-roofed rectangular barrack, 14 m × 6.4 m, 2.6 m high, with a
  glazed lean-to sunspace 1.8 m deep along the long south face.
- Walls: pale cement plaster outside over 100 mm insulation and 400 mm rammed earth,
  540 mm thick, visible at the window reveals. Flat roof with a low stone parapet and coping. Stone plinth
  0.45 m high with three entry steps and a dark timber door.
- Section cut through the building showing five rooms divided by thin partitions:
  two large sleeping bays, a kit store, an ablution room and an entry lobby. Rooms shown
  as lightly tinted floor areas. No furniture.
- Setting: high-altitude cold desert valley, bare brown and grey slopes rising to
  ridgelines behind. Winter light, sun low in the south at about 34 degrees, long soft
  shadows. No snow-capped photographic peaks, no vegetation.

HUD panel: Arrangement sunspace · Surfaces 18 · Volume 300 m³ · Azimuth 180° ·
Sun altitude 33.7° · Sun azimuth 172.4°.

BOTTOM BAR: play button, scrubber at 12:00 of 00:00–24:00, then "Daylight 10.0 h",
"Clear of ridge 8.7 h", "Lost to valley 1.3 h".

SEARCH STRIP: "Searching for better designs… Generation 3 of 5 · 60 designs simulated",
progress 60 %, and "Best so far: Sunspace barrack, 540 mm wall · 5.6 °C · ₹11.58 L".

DO NOT include: a pitched roof, stone rubble walls, rainbow or gradient colours on any
surface, furniture, a photographic backdrop, a user avatar, wind-exposure findings,
kWh per m² per year, or any value not listed above.
```

The sun altitude and azimuth in the HUD are the values at 12:00 on 12 January; if you pick
another hour, take them from `/api/sun-path`.
