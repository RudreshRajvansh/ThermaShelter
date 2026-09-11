# Page briefs — Entropy Zero

Every feature below exists in the code today. Each page has: what it is for, what it
contains, what is wrong with how it is shown, the best way to show it, and a prompt you
can hand to a designer or a build pass.

---

## Non-negotiables (paste these into every prompt)

```
HARD RULES — a DRDO jury will check these

1. Nothing on screen may look computed if it was not. Every coloured surface, curve
   and number must come from EnergyPlus output or a cited Layer 0 file.
2. If a metric was not computed, say so. Never blur it, fake it, or quietly drop it.
3. "Interior air (CFD)" draws only an ANSYS Fluent field solved for that exact design,
   with the solver, hour and cell count on screen. With no solved field it says so and
   draws nothing inside the room; the page never interpolates air temperature itself.
4. Air exchange arrows are whole-zone flow, not a velocity field. Never draw
   streamlines or particles that imply air movement inside a room.
5. No composite score out of 100. No PMV. Named metrics against a named baseline.
6. Heating climate (Leh): lead with minimum overnight temperature. Never lead with,
   or headline, a cooling metric.
7. Rupee figures are provisional (unsourced rates). Fuel savings read "withheld"
   until a procurement price exists. Keep both labels.
8. Live conditions are context only. They never feed or appear to feed a result.
9. The two comfort models have identical coefficients. The selector must say so.
10. Must work with the network cable pulled out.
```

---

## Where the structure is broken today

The pages were added one at a time, so the same feature now lives in several places.

| Feature | Currently appears in |
| --- | --- |
| Pareto front / search | Design → recommend, **and** Evidence → results → pareto explorer |
| Performance over time chart | Evidence → results, Evidence → compare, Report |
| Construction build-up | Design → model → construction view, Evidence → construction, Evidence → results → materials, Report |
| Assumptions | Evidence → results → assumptions, Report |
| Validation | Evidence → results → validation, Report |
| "What X reported about itself" | Climate, Design → model, Report |
| The 3D viewport | Design → recommend, **and** Design → model |
| Tier-one inputs | Design → recommend, **and** Design → all inputs |

Worst single problem: **Evidence → results has 7 tabs inside a tab.** Nested tabs hide
things; a judge will never find "detailed analysis".

Known defects to fix during the redesign:
- Wireframe and X-ray act on the raw surface meshes, but the visible building is the
  extruded solid, so both modes are only partly wired to what is on screen.
- Climate page shows "Cooling degree days: 0" on a Ladakh run (rule 6).

---

## Proposed structure

```
DESIGN      one surface: inputs rail + viewport + recommendation
            (Model and All inputs become a drawer and viewport modes, not tabs)

EVIDENCE    Climate · Construction · Performance · Compare · Trust
            five flat tabs, nothing nested

REPORT      print surface, assembled from Evidence components
```

"Trust" collects everything that proves the result is honest: assumptions, validation,
solver and weather warnings, and the CFD tab. Report reuses the same components rather
than re-implementing them.

---

## 1. Design

**Purpose:** a judge sets up a shelter and sees the recommended design within seconds.

**Features today**

Inputs rail
- Location; what it is for (7 programmes); occupants
- Programme line: area per person, ceiling height, room count
- Advanced: floor area, orientation, comfort target, search budget (100 / 300 / 500)
- Find the best shelter — live NSGA-II search, front redrawn every generation
- Recommendation: design name, headline metric, hours outside band, cost, flags
- Pareto scatter with the knee highlighted, infeasible points shown in red
- Search warnings

Viewport
- Painted: Construction / Surface temperature / Incident solar
- Interior air (CFD), separate and dashed, ANSYS Fluent field or an explicit absence
- Shell: Solid / Wireframe / X-ray · Section cut
- Overlays: Built detail, Rooms, Air exchange, sun path, compass
- Render quality: High / Balanced / Plain · colour ramp: Cold to hot / Spectral / Night to sun
- Time: Play 24 h, Solar noon, hour scrubber
- Full screen (F), auto-hiding controls
- HUD: arrangement, surface count, volume, azimuth, sun altitude and azimuth
- Hover a face → surface name and value
- Sun panel: astronomical daylight, daylight clear of the ridge, hours lost to the valley
- Real SRTM ridgeline and terrain

From Design → model (to fold in): run period, surface-output window, solver version and
runtime, status, plan view, section views, construction view.

**How it is shown badly**
- Rail is uniform small grey text: nothing says "start here" or "this is the answer".
- Recommendation appears below the fold, under the inputs, after scrolling.
- Viewport control bar is two dense rows of equal-weight buttons.
- Sun panel and HUD compete with the controls.
- Model tab duplicates the viewport.

**Best way to show it**
- Rail has three visually distinct states: *Set up* → *Searching* → *Recommended*. When a
  result exists, it moves to the top of the rail and the inputs collapse to a one-line
  summary ("Leh · Troop accommodation · 9 people · edit").
- The recommendation is a single strong statement, not a stat grid: the design name, then
  one sentence with the headline number against current construction.
- While searching, show a progress line with generation and designs simulated, and let the
  viewport show the current best design live.
- Viewport controls: one primary segmented control (what is painted), everything else in a
  compact overflow group. Plan and Section become viewport modes next to 3D.
- Sun panel becomes a slim strip along the bottom of the viewport.

**Prompt**
```
Redesign the DESIGN surface of a passive-shelter engineering tool (Operate mode,
light theme, existing palette tokens). Two columns: a 380px rail and a viewport that
fills the rest at up to 78vh.

The rail has three states. SET UP: location, shelter use, occupants, and a collapsed
"Advanced" group (floor area, orientation, comfort target, search budget), then one
primary button "Find the best shelter". SEARCHING: inputs collapse to a one-line
summary with an edit link; a progress line shows generation N of M and designs
simulated; the current best design name updates live. RECOMMENDED: the design name in
large type, one sentence stating its minimum overnight temperature against current
construction, three secondary metrics, flags as plain warnings, and a small Pareto
scatter with the chosen design highlighted and constraint-breaking designs visible.

The viewport: 3D / Plan / Section as a top-left segmented control; "what is painted"
(Construction, Surface temperature, Incident solar) as the primary control; shell
style, overlays, render quality and colour scale in a compact overflow menu; time
controls and the sun summary (daylight, clear of ridge, lost to valley) as a slim bar
along the bottom edge; Full screen top-right. "Interior air (CFD)" sits apart from the
painted-results control, with the solver, solved hour and convergence visible whenever
it is active, or the reason no field exists.

Apply the HARD RULES.
```

---

## 2. Evidence → Climate

**Purpose:** prove the weather the result rests on is real and handled correctly.

**Features today**
- Weather year stats: annual mean, annual range, heating degree days, cooling degree days,
  global horizontal solar, direct normal solar, mean wind, design day
- Air pressure at altitude: station pressure, expected at elevation, air density used,
  sea-level comparison
- Temperature through the year: monthly mean, full range, daily swing
- Solar resource by month: GHI vs DNI
- The worst-night week
- Terrain horizon profile
- Weather file warnings
- Live conditions (navbar): current temperature at the site

**How it is shown badly**
- Eight equal stat cards: the one that matters (design day) looks like the rest.
- Cooling degree days is displayed on a heating site.
- The altitude pressure check — the strongest credibility point on the page — reads as
  one more grid of numbers.
- Live conditions and the EPW's pressure agree (69,640 vs 69,287 Pa) but nothing shows it.

**Best way to show it**
- Lead with the design day and the worst-night week together: "the night this shelter has
  to survive".
- Pressure check as a verdict with its evidence: *Correct for 3,256 m* — file, expected,
  live reading, all within tolerance.
- Hide cooling metrics on heating sites (and vice versa).
- Horizon profile as a polar skyline with the winter sun path drawn on it.

**Prompt**
```
Redesign the CLIMATE evidence tab. Lead with one hero block: the design day and the
coldest week as a temperature chart, titled as the weather this shelter must survive.
Below, a verdict card "Pressure correct for 3,256 m" showing station pressure from the
weather file, the barometric expectation, and the live reading as an independent check,
with the tolerance. Then a compact row of the remaining annual figures, omitting cooling
degree days on a heating-dominated site. Then monthly temperature and monthly solar as
two small multiples. Then the terrain horizon as a polar skyline with the winter solstice
sun path overlaid, showing hours of sun lost to the ridge. Weather-file warnings last.
Apply the HARD RULES.
```

---

## 3. Evidence → Construction

**Purpose:** show exactly what is being built, layer by layer, and how it compares.

**Features today**
- Dimensions: floor area, plan proportion, ceiling height, orientation
- Derived: plan dimensions, volume, envelope area, surface-to-volume ratio
- Wall / roof / floor build-up with per-layer thickness sliders and material colours
- Glazing and shutters per facade (window-to-wall ratio)
- Infiltration and ventilation, with the fresh-air floor
- Construction detail with ISO 6946 U-values
- Against current construction

**How it is shown badly**
- Build-ups are three columns of sliders: you edit layers without seeing a wall.
- U-values and the Eco-Niwas Samhita limits (wall ≤ 1.8, roof ≤ 1.2) are not shown side
  by side.
- Glazing per facade is a list, not a plan.

**Best way to show it**
- Each build-up as a drawn section: layers stacked to scale with their real thickness,
  outside on the left, U-value beside it, pass/fail against the code limit.
- Glazing as a small plan diagram with each facade's ratio on its edge, south highlighted
  as the collector.
- Current construction drawn beside the candidate in the same scale.

**Prompt**
```
Redesign the CONSTRUCTION evidence tab. For wall, roof and floor, draw each build-up as a
horizontal section to true scale: each layer a band sized by its thickness, coloured by
its material, labelled with material and millimetres, outside face on the left. Beside
each, show the computed U-value and a pass or fail against Eco-Niwas Samhita 2018 cold
zone limits (wall 1.8, roof 1.2 W/m2K). Thickness is edited by dragging a layer boundary,
with a numeric field as a fallback. Show current construction's section directly beneath
in the same scale. Glazing becomes a plan diagram of the footprint with each facade's
window-to-wall ratio on its edge and the south face marked as the collector. Dimensions
and derived figures (volume, envelope area, surface-to-volume) as a compact header.
Ventilation shows the fresh-air floor as a hard limit, not a target. Apply the HARD RULES.
```

---

## 4. Evidence → Performance (today: Results)

**Purpose:** what the shelter does, hour by hour, against current construction.

**Features today**
- Headline sentence: candidate vs current construction overnight minimum
- Three stat deltas: minimum overnight, hours below band, remaining heating demand
- Performance over time: operative temperature, comfort band, current construction line
- Sub-tabs: summary, materials, heat flow, pareto explorer, assumptions, validation,
  detailed analysis
- Baseline self-comparison guard (says so when the design is the baseline)

**How it is shown badly**
- Seven nested tabs.
- Materials duplicates Construction; pareto duplicates Design; assumptions and validation
  belong in Trust.
- Heat flow is buried behind a tab.

**Best way to show it**
- One scrolling page, no sub-tabs: headline → chart → heat balance → uncertainty band.
- Heat balance as a single stacked bar or Sankey: where heat enters and leaves (walls,
  roof, floor, glazing, infiltration) per the simulated breakdown.
- Uncertainty as a band on the chart, with the dominant input named.

**Prompt**
```
Redesign the PERFORMANCE evidence tab as a single scrolling page with no sub-tabs. Top:
one sentence stating the design's minimum overnight operative temperature against current
construction, with the difference in kelvin; if the design is itself the baseline, say so
and show no difference. Then three metric deltas. Then the hourly operative temperature
chart with the adaptive comfort band shaded, current construction as a faint line, and
the uncertainty range as a band labelled with its dominant input. Then the heat balance
as one horizontal stacked bar or Sankey showing losses through walls, roof, floor, glazing
and infiltration from the simulated breakdown. Remove materials, pareto, assumptions and
validation from this page; they live on Construction, Design and Trust. Apply the HARD RULES.
```

---

## 5. Evidence → Compare

**Purpose:** the comparison table the problem statement asks for by name.

**Features today**
- Pick up to three candidates (nine available)
- Temperature curves for each
- Design ranking: overnight minimum, hours below, degree hours, heating kWh, fuel, cost,
  wall thickness, labour skill

**How it is shown badly**
- Candidates are identified only by name in the table; no visual of what each is.
- All columns carry equal weight.
- The best value in each column is not marked.

**Best way to show it**
- Each column header gets a small thumbnail of that design.
- Transpose to designs as columns and metrics as rows (easier for 2–3 designs).
- Mark the best value per metric; order metrics by importance for the climate.

**Prompt**
```
Redesign the COMPARE evidence tab. Designs become columns, up to three plus current
construction as a fixed reference column. Each column header shows a small rendered
thumbnail of that design, its name and arrangement. Metrics become rows, ordered by
importance for the site's climate objective, starting with minimum overnight temperature
for a heating site. Mark the best value in each row; show differences against the
reference in a secondary line. Cost and fuel rows carry their provisional or withheld
label. Above the table, the hourly temperature curves for the selected designs with the
comfort band shaded. No composite score and no overall winner badge. Apply the HARD RULES.
```

---

## 6. Evidence → Trust (new; collects scattered panels)

**Purpose:** answer "how do we know this is not made up?" in one place.

**Features today (currently scattered)**
- Assumptions table with sources (Results tab, Report)
- Validation evidence: Standard 140, ANSYS junctions, calibration (Results tab, Report)
- What the weather file reported (Climate)
- What the simulation reported (Design → model, Report)
- Detailed analysis / CFD for one design (Results tab)
- Provenance gaps (`/api/meta`): unsourced materials, uncited references, pending comfort
  model, zero fuel price, unsourced costs

**Best way to show it**
- A provenance ledger: every input grouped by status — verified, derived, assumed,
  pending — with counts at the top.
- Open gaps listed as plain statements with what would close them.
- Validation shown as present or absent, never implied.

**Prompt**
```
Design a TRUST evidence tab for an engineering tool reviewed by a DRDO jury. Top: a
provenance summary counting inputs by status (verified, derived, assumed, pending). Then
"open gaps": each unverified input as a plain statement of what is missing and what would
close it. Then the assumptions ledger grouped by status with source citations. Then
validation evidence (Standard 140, ANSYS thermal bridges, calibration), shown explicitly
as not yet committed where absent. Then solver and weather-file warnings by severity.
Then detailed CFD analysis, available for one design, always labelled with solver and hour.
Tone: an audit, not a disclaimer. Apply the HARD RULES.
```

---

## 7. Report

**Purpose:** a printable document someone signs off.

**Features today**
- Print or save as PDF
- Executive summary sentence
- Result table and site-and-climate table
- Performance over time
- Construction specification
- Assumptions
- Validation
- Run warnings
- Baseline self-comparison guard

**How it is shown badly**
- Reads as a web page printed, not a document: no title block, no page structure.
- Re-implements charts and tables that exist elsewhere.

**Best way to show it**
- A proper A4 document: title block (site, design, date, engine and EnergyPlus versions,
  design hash), numbered sections, page breaks before charts, footer with provenance.
- Built from the Evidence components, so the report can never disagree with the app.

**Prompt**
```
Design a printable A4 REPORT for a passive shelter assessment. Page one: a title block
with site, design name, date, engine version, EnergyPlus version and the design hash, then
an executive summary of three sentences and a single key-figures table. Following pages,
numbered: site and climate, construction specification drawn as scaled sections,
performance over time, heat balance, comparison against current construction, assumptions
with sources, validation status, and solver warnings. Page breaks before each chart; a
footer on every page with the design hash and "results from EnergyPlus; see section 7 for
open provenance gaps". It must print cleanly in black and white. Apply the HARD RULES.
```

---

## Master prompt (whole app, one pass)

```
Redesign "Entropy Zero", a passive solar shelter design tool for DRDO. Operate mode:
earned familiarity over novelty, light theme, existing palette tokens, one sans family,
fixed rem type scale, motion 150–250 ms for state only.

Information architecture:
- DESIGN: 380px input rail with three states (set up, searching, recommended) and a
  full-height 3D viewport with 3D/Plan/Section modes and compact grouped controls.
- EVIDENCE: five flat tabs, no nesting — Climate, Construction, Performance, Compare,
  Trust.
- REPORT: printable A4 document built from Evidence components.

Remove duplication: one Pareto view (Design), one performance chart component, one
construction build-up component, assumptions and validation only in Trust.

Representation principles:
- Lead every page with the single most decision-relevant fact as a sentence.
- Draw physical things (wall build-ups, glazing by facade, horizon) instead of listing them.
- Show every result against current construction.
- Absent data is stated, never hidden.

Apply the HARD RULES in full.
```
