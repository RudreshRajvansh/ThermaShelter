# Layer 2, the engine

Owner: Rudresh.

Takes a shelter description and a weather file, returns hourly temperatures, solar gains,
heat losses and per surface results. Searches across shelter descriptions to find the good
ones and reports how uncertain each answer is. It also emits the geometry as something a
browser can draw, and exports boundary conditions for the fluid dynamics run.

Physics happens here and nowhere else. If a temperature is ever calculated in the API or in
the browser, something has gone wrong in the design and not just in the code.

---

## Setup

```bash
cd engine
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
```

EnergyPlus is a separate install and is required at runtime. This repository was developed
and tested against **EnergyPlus 24.2.0**. Point the engine at it with `ENERGYPLUS_DIR`, or
put `energyplus` on `PATH`:

```bash
ENERGYPLUS_DIR=R:\SIH\tools\EnergyPlus .venv/Scripts/python -m shelter_engine.cli check
```

`check` prints the EnergyPlus version it found, the comfort models and reference designs it
loaded, and every record that does not yet carry a verified source. Run it first when
anything looks wrong.

To run the engine before Layer 0 has delivered anything:

```bash
.venv/Scripts/python scripts/make_dev_inputs.py
```

That writes a synthetic weather year and a placeholder material database under `build/dev`.
Both are clearly labelled. Nothing produced from them is a result and none of it may be put
in front of anyone.

---

## What Layer 0 has to hand over

One SQLite file with three tables. Column names are matched case sensitively, and a few
aliases are accepted where the obvious alternative spelling exists.

### `materials`

| column | required | notes |
| --- | --- | --- |
| `id` | yes | stable identifier, referenced by every design |
| `name` | yes | shown on screen |
| `conductivity_w_mk` | yes | W/mK |
| `density_kg_m3` | yes | kg/m3 |
| `specific_heat_j_kgk` | yes | J/kgK |
| `cost_inr_m3` | yes | delivered cost before the transport multiplier |
| `colour` | yes | `#rrggbb`, Layer 5 renders from it |
| `source` | yes | free text naming exactly where the numbers came from |
| `source_status` | no | `verified`, `pending`, `derived` or `assumed`; defaults to `pending` |
| `finish` | no | `rough`, `smooth`, `matte`, `glossy`, `textured` |
| `thermal_absorptance`, `solar_absorptance`, `visible_absorptance` | no | 0 to 1 |
| `roughness` | no | one of the EnergyPlus roughness keywords |
| `locally_available` | no | accepts 1/0, yes/no, true/false |
| `transport_multiplier` | no | applied to cost when not locally available |
| `mason_skill` | no | `unskilled`, `semiskilled`, `skilled`, `specialist` |
| `conductivity_low`, `conductivity_high` | no | plausible range; drives the uncertainty bands |
| `density_low`, `density_high` | no | as above |

A material with no `source` or no `colour` is rejected outright rather than silently
defaulted. That is deliberate: a data error here never announces itself, so the engine
refuses to start instead.

### `glazing_systems`

`id`, `name`, `u_value_w_m2k`, `shgc`, `visible_transmittance`, `cost_inr_m2`, `colour`,
`source` are required. `layers`, `frame_fraction`, `locally_available`, `finish` and
`source_status` are optional.

### `phase_change_materials`

`id`, `name`, `melt_temperature_c`, `latent_heat_j_kg`, `conductivity_w_mk`,
`density_kg_m3`, `specific_heat_j_kgk`, `cost_inr_m3`, `colour`, `source` are required.
`melt_band_k` and `cycle_capacity_retention` are optional.

### Identifiers the reference designs need

The three reference designs in `src/shelter_engine/data/reference_designs.json` will not
load without these. `shelter_engine.cli check --materials <db>` lists any that are missing.

```
brick_solid  cement_plaster  concrete_dense  gravel_bed  gi_sheet  timber_softwood
mud_brick    mud_plaster     rammed_earth    stone_granite  eps_thermocol  rockwool
single_clear_4mm  double_clear_6_12_6  double_lowe_argon
```

### The weather file

An EPW at the path named by `SiteSpec.epw_path`. On load the engine checks the row count,
the missing value sentinels, the header coordinates and time zone against the site record,
and **the pressure column against the site elevation**. A file whose mean station pressure
differs from the barometric value for its elevation by more than five percent produces a
severe warning, because EnergyPlus will not complain and every air related heat transfer
will be quietly wrong. Gaps of up to six hours are interpolated; anything longer is refused
rather than invented.

The site record also carries the terrain horizon profile and the twelve monthly ground
temperatures. Both are optional to the code and neither is optional to a correct answer:
without a horizon the engine assumes a flat skyline in every direction, and without ground
temperatures EnergyPlus falls back on temperate climate defaults. Each omission raises its
own warning.

---

## What Layer 4 gets

Every request and response shape is a Pydantic model in `shelter_engine.schema`, so FastAPI
can use them directly and the generated API documentation is the contract. Import them, do
not redefine them.

| endpoint | engine call | returns |
| --- | --- | --- |
| geometry | `Engine.geometry(design)` | `GeometryPayload`, milliseconds, no simulation |
| simulate | `Engine.simulate(design, options)` | `SimulationResult` |
| surface results | `Engine.surface_results(design, options, period)` | `SurfaceResults` |
| compare | `Engine.compare(designs, options, reference)` | `ComparisonTable` |
| optimise | `Engine.optimise(spec, options)` | `ParetoResult` |
| fluid dynamics export | `Engine.cfd_export(design, options)` | `tuple[CFDExport, ...]` |
| solved fluid dynamics field | `build_cfd_field(export, centres, temperatures, solver)` | `CFDField` |
| materials | `MaterialLibrary.materials` and friends | `Material`, `GlazingSystem`, `PhaseChangeMaterial` |
| reference designs | `load_references()` | `ReferenceCatalogue` |
| health | `Engine.self_check()` | `list[EngineWarning]` |

`SimulationResult` carries more than a temperature series: the comfort metrics, the energy
and fuel figures, the heat flow broken down by path, condensation hours, cost and
buildability, the hourly series including the reference for comparison, the per surface
series, the uncertainty bands with their dominant input named, the recommendations, every
assumption with its source, and every warning the run produced.

Read `SimulationResult.warnings` and pass it up. `has_blocking_warnings` is true when
anything severe or fatal is present. A tool that reports its own problems is more credible
than one that hides them, and hiding them means eventually presenting a broken result on
stage without knowing.

Simulation is synchronous and returns in seconds. Optimisation is a batch job; run it
offline and store the front.

---

## What Layer 5 gets, and the naming rule

`GeometryPayload.surfaces` is a list of `MeshSurface`. Each carries `positions` as a flat
`[x, y, z, x, y, z, ...]` list, `indices` as triangles, an outward `normal`, the material
`colour` and `finish` from the database, `area_m2`, and `opacity` below one for glazing.
Coordinates are metres, x east, y north, z up, with the building already rotated to its real
orientation, so the terrain mesh drops straight in beside it.

**`MeshSurface.name` is the same string EnergyPlus uses for that face, and the same string
that keys `SurfaceSeries.surface` in the results.** That is the whole mechanism behind the
3D heatmap. `build_mesh` asserts the two sets match, on every call, and raises rather than
letting them drift; if they ever drift you would show roof temperature on the north wall and
never notice, because it would look perfectly reasonable.

Names are stable and predictable:

```
ROOM_FLOOR              ROOM_ROOF               ROOM_WALL_SOUTH
ROOM_WALL_SOUTH_WIN     ROOM_WALL_SOUTH_L       ROOM_WALL_SOUTH_C
SUNSPACE_WALL_NORTH     TROMBE_GAP_WALL_SOUTH   VESTIBULE_FLOOR
```

`_WIN` is a window on the surface named by its prefix. `_L`, `_C`, `_R` are the left, centre
and right segments of a wall that a sunspace, Trombe wall or vestibule only partly covers.
`_REV` on a construction name is the reversed layer order used by the other side of an
interzone surface; it never appears on a surface name.

Two display modes are honest and both come straight from the simulation:
`SurfaceSeries.inside_face_temperature_c` and `SurfaceSeries.incident_solar_w_m2`. There is
no air temperature field and no velocity field in a `SimulationResult`, because EnergyPlus
computes neither. Those come only from a `CFDField` solved by ANSYS Fluent on the
`CFDExport` boundary conditions, for the one recommended design, and every panel showing
them must state the hour and the solver.

`CFDExport` carries the inside face temperature of every surface plus the internal
convective gain, infiltration and interzone mixing heat flows EnergyPlus reported for that
hour, so the fluid dynamics run closes the same air heat balance. It is written as
`<fingerprint>_<hour>.boundary.json`. `build_cfd_field` turns the solver's cell centres and
values into a `CFDField` on a regular grid, each grid point taking the nearest cell and
points outside the air left empty, and records `boundary_hash`. The API refuses a field
whose boundary conditions have since changed, and a field is written as
`<fingerprint>_<hour>.field.json`.

---

## Conventions worth knowing before reading the code

**Construction layers run outside to inside.** `ConstructionSpec.layers[0]` is the weather
face. Interzone surfaces get a matching `_REV` construction automatically.

**Heat flows are signed, negative means the room lost heat.** `HeatBalanceBreakdown` fields
are kWh over the run period. `loss_fractions()` turns the negative ones into shares of total
loss, which is what the breakdown chart wants.

**Comfort is operative temperature, never air temperature, and never PMV.** The adaptive
band comes from `ComfortModel`, applied against the exponentially weighted running mean of
outdoor temperature. Degree hours are accumulated **in both directions** and summed, so the
same metric is a heating objective in Leh and a cooling objective in Jaisalmer with no code
change. Degree hours rather than hour counts, because a step function gives the optimiser no
gradient to follow.

**Every result is a difference against a defined baseline.** `load_references()` returns
three named, described, cited reference designs; `current_practice_masonry` is the default.
`Engine.simulate` attaches a `ReferenceComparison` unless you explicitly turn it off.

**Approximate numbers are labelled.** `Provenance` on a series or a metric says whether it
came from the simulator, from literature, from an assumption, or from ANSYS.

---

## What is in the engine, and what is deliberately not

Complete and exercised against a real EnergyPlus run:

- four multi zone arrangements: plain room, attached sunspace, Trombe wall, entry vestibule
- parametric geometry at any orientation, with window placement, wall splitting for partly
  covered facades, interzone pairing and terrain horizon shading
- one geometry description producing both the EnergyPlus surfaces and the display mesh,
  with name matching asserted on every call
- operative temperature, adaptive comfort band, degree hours accumulated in both directions,
  overnight minimum, design day minimum, decrement factor and time lag
- heat flow broken down by path, including a thermal bridge line when Layer 1 supplies one
- condensation hours against the inner glazing dew point
- occupancy, equipment and lighting schedules, an unoccupied case, added moisture from
  cooking and washing as a latent gain
- night shutters with partial compliance modelled as a deterministic day pattern
- window overhangs, night ventilation and a minimum fresh air floor
- phase change materials, with the solver and timestep changed automatically
- cost, mason skill, local availability and a seismic flag on thick mass walls
- uncertainty bands from a two level factorial with the dominant input named
- multi objective search with hard constraints, and recommendations generated from the
  resulting numbers rather than written by hand
- boundary condition export for the fluid dynamics run at the coldest and sunniest hours

Deliberately not here, because it belongs to another layer or the guide ruled it out:

- **ASHRAE Standard 140 test cases and the ANSYS runs** are Layer 1. The engine will run the
  Standard 140 cases, whose definitions ship with EnergyPlus, but producing and committing
  those results is Mahi's job, not the engine's.
- **A surrogate model.** The measurement said no. See the last section.
- **A full airflow network.** Zones exchange air through a scheduled cross mixing flow, and
  the assumed rate is recorded as an assumption. The full network is more physically
  complete and costs days.
- **Warm humid climates.** The physics runs anywhere, but the lever set and the objective are
  built for large diurnal swings. In Kerala the mass has nothing to work against and the
  correct strategy inverts. State it as a known limitation rather than being caught by it.
- **Anything the network has to fetch at run time.** The engine reads files. Layer 0 gathers
  its data once, checks it once and freezes it, so the demo does not depend on venue wifi
  and a result run twice gives the same answer.

---

## Solver behaviour you may hit

The engine changes solver settings on its own in three situations, and says so in the
warnings each time:

- **Phase change materials** force the conduction finite difference solver and raise the
  timestep to at least twenty steps per hour. Those runs are noticeably slower.
- **A conduction transfer function convergence failure** triggers one automatic retry with
  the finite difference solver. This happens when a very light or very resistive layer sits
  next to a heavy one, which the optimiser will produce sooner or later.
- **Sunspace and Trombe wall arrangements** are simulated with `FullInteriorAndExterior`
  solar distribution rather than `FullExterior`. Under `FullExterior` EnergyPlus lands all
  transmitted solar on the floor, which drives a thin Trombe gap floor past two hundred
  degrees and starves the mass wall of the gain the whole design depends on.

---

## Running it

```bash
python -m shelter_engine.cli check --materials build/dev/materials.db

python -m shelter_engine.cli --site build/dev/site.json \
  --materials build/dev/materials.db \
  run build/dev/single_room.json --start 2019-01-01 --end 2019-01-31 --conditioned

python -m shelter_engine.cli --site build/dev/site.json \
  --materials build/dev/materials.db \
  compare build/dev/single_room.json build/dev/sunspace.json
```

`geometry` emits the display mesh without simulating, and `idf` writes the EnergyPlus input
file so it can be opened and read directly. When something looks wrong in a result, read the
IDF first.

Tests:

```bash
.venv/Scripts/python -m pytest              # skips the EnergyPlus tests if it is missing
ENERGYPLUS_DIR=... .venv/Scripts/python -m pytest    # runs all of them
```

Set `SHELTER_ENGINE_KEEP_RUNS=1` to keep the EnergyPlus working directories for inspection,
`SHELTER_ENGINE_TIMEOUT` to change the per run timeout, and `SHELTER_ENGINE_WORKERS` to
change how many runs go in parallel.

---

## ANSYS Fluent for the air inside the room

EnergyPlus ranks designs and gives one air temperature per room. For the chosen design,
`shelter_engine.ansys.run_fluent` solves the air inside the room at the exported hours:

1. `build_room_mesh` meshes the room as a structured hex grid, 1 cm at every surface and
   growing to 20 cm inside, with grid lines placed exactly on every window and wall segment
   edge so each exported surface gets its own faces. Rooms that are not boxes are refused.
2. The mesh is written as NASTRAN bulk data and imported with Fluent's
   `file/import/nastran/bulkdata`. The CGNS route was dropped: Fluent 2026 R1's CGNS reader
   crashed on half the imports of the same file.
3. Every wall zone is matched to its exported surface by its own vertex bounds, and held at
   the EnergyPlus inside face temperature. The internal convective gain, infiltration and
   mixing heat flows for the hour go in as one uniform volumetric source.
4. Steady pressure based solver, k-omega SST, full buoyancy with incompressible ideal gas at
   the site pressure, body force weighted pressure. No radiation model, because the surface
   temperatures are fixed and air is transparent.
5. Scaled residuals plateau for air moved only by surface temperature differences, so
   convergence is judged on the solution: across two consecutive windows of four 50
   iteration chunks the volume average air temperature must drift less than 0.02 K and
   stay inside a 0.1 K band, and the wall heat averaged over the last window must balance
   the air heat source to 5 percent. That air never sits perfectly still, so the test is
   for a stationary state rather than a frozen one. The basis is written into the field's
   notes, and an unconverged field says so.
6. Some hours never settle. At peak sun the warm air arriving from a sunspace keeps the
   room moving, and a steady solver swings without closing the heat balance. After 1000
   steady iterations the driver switches to a transient run from where it stopped: 2 s
   steps, 10 iterations each, 60 s of spin up, then a sample of every cell every 10 s. It
   stops when the time averaged air temperature drifts less than 0.05 K between two 120 s
   windows and the time averaged wall heat balances the air heat source, and the stored
   field is the average over those 240 s. The record carries `time_step_s` and
   `averaging_s`, and the interface says the field is a time average. A transient run costs
   roughly five seconds of computer time per second of simulated flow on four cores.

Every solve starts from the air at the EnergyPlus zone air temperature, which puts it close
to the heat balance EnergyPlus already found.

Install the extra and run it from the repository root once Fluent is installed:

```bash
.venv/Scripts/python -m pip install -e ".[cfd]"
.venv/Scripts/python ../scripts/run_cfd.py --design 993f0edc --label coldest_night
```

Fluent is found from `SHELTER_ANSYS_ROOT`, then `AWP_ROOT261` and friends, then the Windows
machine environment. Fields land in `data/cfd/<fingerprint>_<hour>.field.json`; Fluent's
case and data files stay under `build/cfd_runs`. `SHELTER_RUN_FLUENT=1` enables the one
test that launches Fluent.

---

## Still outstanding, and why it matters

These are provenance gaps, not code gaps. The engine already warns about each one on every
run, and `cli check` lists them. None of them should reach a jury unresolved.

1. **The comfort model coefficients are unverified.** `imac_2016_nv` carries the widely
   quoted IMAC relation with `status: pending`, and `imac_r_2022_nv` is a placeholder
   holding the same numbers until the residential paper is read. Every comfort number
   depends on these. Read the papers and update
   `src/shelter_engine/data/comfort_models.json`.
2. **The reference designs are uncited.** All three carry `status: pending`. The current
   practice assembly and its infiltration rate are the single most important citation in the
   project, because every headline difference is measured against them.
3. **The fuel price is zero.** Deliberately, so that a missing procurement figure shows up as
   a zero rather than as a plausible invented number. Set it before showing rupees.
4. **Thermal bridge psi values come from Layer 1.** Pass them to `Engine(thermal_bridges=...)`
   as `ThermalBridge` records once the ANSYS junction run exists. Until then the breakdown
   has no bridge line and says so.
5. **The surrogate decision is made: no surrogate.** The rule was to time one single room
   run over a week of January weather and drop the surrogate if it came in under about five
   seconds. Measured on this machine, one evaluation as the optimiser sees it, free running
   with surface output off:

   | period | time |
   | --- | --- |
   | one January week | 2.4 s |
   | one January month | 2.4 s |
   | three winter months | 3.4 s |

   Of that, EnergyPlus itself is about 1.3 s and the rest is building the input file. So
   there is no surrogate, no job queue and no cache server, and the optimiser calls the
   simulator directly. A thousand evaluations is roughly forty minutes on one core, and
   `SHELTER_ENGINE_WORKERS` parallelises it. Phase change materials force the slower solver
   and would change this answer, so re-measure before committing to a PCM search.

   Two things are worth knowing if that number ever moves. The EnergyPlus run barely
   responds to run length, because startup and warmup dominate a winter period. And building
   the input file used to cost twelve seconds, all of it inside eppy's pretty printer doing
   a linear scan per field; the engine keeps eppy for its input dictionary validated object
   model and writes the text itself, which is what `serialise` in `idf.py` is for. Do not
   replace it with `IDF.idfstr()` without re-measuring.
