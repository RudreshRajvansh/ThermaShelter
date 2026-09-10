# How everything works, and which file controls which

Read top to bottom once and you will be able to answer any "where does that number come
from" question a judge asks.

The rule that holds it together: **physics happens only in Layer 2**. Not in the API, not
in the browser. If a temperature is ever calculated outside the engine, that is a design
bug, not just a code bug.

---

## Part 1 — How data gets in

### Step 1. Mahi hands over files, not an API

A zip with this shape:

```
layer_0/
  weather/IND_JK_Leh.427053_TMYx.2011-2025.epw
  materials/materials.csv
  pcm/pcm.csv
  terrain/horizon_36dir.csv
  terrain/terrain_mesh.obj
  ground_temperature/soil_temperature_TSOIL1_2015_2024.csv
```

Files rather than a live API on purpose. Weather APIs go down, venues have bad wifi, and
a demo that depends on a network call depends on luck. A frozen file also makes results
reproducible, so when a judge asks to run something twice they get the same answer.

### Step 2. One script converts it

```bash
python scripts/ingest_layer0.py --source build/layer0/layer_0 --out data
```

`scripts/ingest_layer0.py` is the **only** thing that writes `data/`. It:

- copies the EPW and loads it once to check it (row count, altitude pressure, warnings)
- maps Mahi's material names to engine ids (`Clay brick` → `brick_solid`)
- runs each row through the physical plausibility check and **corrects** what fails,
  recording the correction rather than doing it quietly
- fills in materials and glazing Layer 0 did not supply, from ASHRAE, marked `pending`
- writes cost provenance separately from thermal provenance, so a verified conductivity
  never vouches for an invented price
- keeps Mahi's raw rows in a `layer0_raw` audit table
- reads the 36 direction horizon and the ground temperatures
- writes `data/layer0_manifest.json` saying exactly what came from whom

It prints all of this. Nothing is silent.

### Step 3. What `data/` then holds

| path | what it is | written by |
| --- | --- | --- |
| `data/weather/*.epw` | the weather year | ingest, copied from Layer 0 |
| `data/materials.db` | materials, glazing, PCM, and the raw Layer 0 audit table | ingest |
| `data/sites/leh.json` | site, horizon, ground temperatures | ingest |
| `data/terrain/terrain_mesh.obj` | the SRTM mesh | ingest |
| `data/layer0_manifest.json` | provenance record | ingest |
| `data/designs/*.json` | the stored design library | hand written, served by the API |
| `data/runs.db` | simulation history and cached results | the API at runtime |
| `data/cfd/` | ANSYS exports, named by design fingerprint | `precompute.py` |
| `data/validation/` | Layer 1 evidence | Mahi, when it exists |

### Step 4. One command brings the whole thing up

```bash
python scripts/start.py
```

`scripts/start.py` verifies the install, finds EnergyPlus, ingests Layer 0 if `data/` is
missing, precomputes a few results, starts the API, regenerates the frontend types from
the running API, starts the interface, and prints the cycle. `--check` stops after
verifying. `--reingest` and `--recompute` force the two build steps.

---

## Part 2 — How one number is produced

A judge picks a shelter type and presses the button. Here is every hop.

```
  Site Input screen
        │  six fields
        ▼
  DesignSpec ──────────────────────────► engine.geometry()  ─► GeometryPayload ─► three.js
        │   one frozen object                (milliseconds)         │
        │   that describes everything                               │ surface names match
        ▼                                                           ▼
  engine.simulate()
     1. programme       what the shelter is for becomes rooms, ceiling height, gains
     2. constructions   wall and roof build-ups from the material database
     3. geometry        surfaces, windows per facade, horizon shading panels
     4. IDF             the EnergyPlus input file is written
     5. EnergyPlus      runs the period hour by hour against the real weather
     6. read back       eplusout.sql for results, eplusout.err for solver complaints
     7. comfort         adaptive band, degree hours both directions, condensation
     8. cost            volumes from the geometry, rates from the material database
     9. reference       the same run again for current practice, same size, same site
    10. uncertainty     8 more runs at the corners of the uncertain inputs
    11. recommend       advice generated from those numbers, not from a template
        │
        ▼
  SimulationResult ─► store (SQLite, keyed by design hash) ─► /api/simulate ─► screens
```

Two identities travel with every result and they are **not** the same thing:

- `design_hash` — this design **under these run options** at this engine version. Layer 3
  keys stored runs on it, so a repeat request is served from cache.
- `design_fingerprint` — the design **alone**. CFD exports are named after it, because a
  fluid dynamics run belongs to a shelter, not to a run period.

---

## Part 3 — Which file controls which

### Layer 2, the engine — `engine/src/shelter_engine/`

| file | controls |
| --- | --- |
| `schema.py` | **the contract.** Every type crossing a layer boundary. Change a field here and the API, the generated TypeScript and the screens all follow |
| `engine.py` | the facade. `simulate`, `geometry`, `compare`, `optimise`, `self_check`. Also every integrity warning |
| `programme.py` | what a shelter is **for** → floor area, ceiling height, room layout, occupancy, gains |
| `geometry.py` | the parametric model. Rooms, the four arrangements, surface orientation, window placement, horizon shading |
| `constructions.py` | design description → layered wall, roof, floor, partition build-ups |
| `materials.py` | reads `materials.db`. Also the **plausibility check** that caught the clay brick |
| `idf.py` | writes the EnergyPlus input file. Field name validation and the fast serialiser |
| `runner.py` | runs EnergyPlus, reads `eplusout.sql` and `eplusout.err` |
| `weather.py` | EPW loading, degree days, and the altitude pressure check |
| `solar.py` | sun position, sun path, terrain mesh, daylight lost to the ridge |
| `comfort.py` | the adaptive band, degree hours in both directions |
| `metrics.py` | everything derived from the hourly series |
| `costing.py` | capital cost from real volumes, buildability, U-values |
| `baseline.py` | the reference designs the headline difference is measured against |
| `uncertainty.py` | the 2^k corner runs that turn a decimal into a band |
| `optimize.py` | NSGA-II, the Pareto front, the knee point, and the per-generation stage reporting |
| `recommend.py` | advice generated from computed numbers |
| `cfd.py` | boundary condition export for ANSYS |
| `results.py` | assembles `SimulationResult` |
| `cli.py` | command line entry point |

### Layers 3 and 4, storage and API — `api/src/shelter_api/`

| file | controls |
| --- | --- |
| `main.py` | the app, CORS, exception handling |
| `config.py` | every path and setting, all overridable by environment variable |
| `deps.py` | builds the engine per (site, comfort model) and caches it. Where 503s come from when EnergyPlus is missing |
| `store.py` | **Layer 3.** SQLite: run history, per surface results, optimisation jobs and their intermediate fronts |
| `schemas.py` | request and response shapes |
| `routers/catalog.py` | `/health`, `/meta`, `/materials`, `/designs`, `/references`, `/comfort-models`, `/shelter-uses` |
| `routers/design.py` | `/geometry`, `/simulate`, `/surface-results`, `/compare`, `/climate`, `/sun-path`, `/terrain` |
| `routers/search.py` | `/optimise` and its polling endpoint |

### Layer 5, the interface — `web/src/`

| file | controls |
| --- | --- |
| `lib/schema.d.ts` | **generated, never edited.** `npx openapi-typescript` writes it from the running API, so the frontend cannot drift from the engine |
| `lib/api.ts` | the typed client. One function per endpoint |
| `lib/store.tsx` | the seven step state: site, design, period, comfort model |
| `lib/scene.ts` | three.js helpers: sky, terrain, room layout, sun path, compass |
| `lib/format.ts` | number formatting and every colour ramp |
| `App.tsx` | the stepper, header, design dropdown, weather toggle |
| `pages/SiteInput.tsx` | the six tier-one fields |
| `pages/ClimateData.tsx` | weather summary, degree days, sun availability |
| `pages/DesignParameters.tsx` | construction, windows, ventilation |
| `pages/Simulation.tsx` | the 3D view, plan, sections, construction tabs |
| `pages/Results.tsx` | headline numbers, hourly curves, recommendations, Pareto explorer |
| `pages/Compare.tsx` | the comparison table |
| `pages/Reports.tsx` | the printable report |
| `components/Scene3D.tsx` | the 3D sun study |
| `components/PlanView.tsx` | plan and section drawings |
| `components/Pareto.tsx` | the search, its budget selector and the live-updating front |

### Scripts

| file | controls |
| --- | --- |
| `scripts/start.py` | one command to bring everything up |
| `scripts/ingest_layer0.py` | the only writer of `data/` |
| `scripts/precompute.py` | runs every stored design once so the first screen is not empty |
| `scripts/build_data.py` | the old synthetic generator, superseded by the ingest |

---

## Part 4 — How the search finds the best design

Simulate answers *"what does this shelter do?"* Search answers *"what is the best?"* Only
in search do you hand over control.

1. You declare which variables it may move and their ranges — insulation thickness,
   glazing, window fraction per facade, overhang depth, orientation, thermal mass.
2. NSGA-II evolves a population. **Every individual is a real EnergyPlus run.** No
   surrogate — that decision was made by measurement, not taste: an evaluation takes
   about 2.4 s, so a surrogate was not worth the fidelity loss.
3. Three objectives at once, exactly as the build guide specifies: minimise degree hours
   outside the comfort band, minimise cost, maximise the margin on the worst design day.
4. After **every generation** the current front is published to the job record, so the
   screen updates while the search is still running. Pick a budget of 100, 300, 500, 900
   or 1000 designs; you see a front after roughly the first twentieth of it.
5. It returns a **Pareto front**, not a winner — the set where you cannot get warmer
   without paying more.
6. It also names a **knee point**: the design closest to the ideal corner once the three
   objectives are normalised. That is the "best design" answer, and it is marked amber on
   the chart.

Choosing a point on that front is a command decision about budget and buildability. That
is why there is no score out of 100.

---

## Part 5 — What the tool refuses to do

This is the part that survives a DRDO panel.

- **No EnergyPlus, no result.** `/api/health` reports degraded and every simulation
  returns 503 with an explanation. Nothing is invented in its place.
- **No velocity or temperature field** is rendered that the whole-building simulator did
  not produce. Those live behind the detailed analysis tab, come from an ANSYS run for
  one design, and carry the hour and the solver on the panel.
- **No cooling metric on a Ladakh run.** The headline follows the climate objective from
  Layer 0.
- **No composite score, no PMV.** Named metrics against a named baseline.
- **Every unverified input says so** — on `/api/meta`, on screen, and inside
  `SimulationResult.warnings` on every single run.

The warning codes worth knowing by name:

| code | meaning |
| --- | --- |
| `fuel_price_unset` | the diesel price is zero, so rupee savings read as withheld |
| `capital_cost_unsourced` | the capital cost rates have no verified source |
| `materials_unsourced` | material or glazing records without a citation |
| `material_properties_implausible` | a property failed the physical sanity check |
| `reference_designs_uncited` | the baseline has no source |
| `comfort_model_unverified` | the comfort coefficients are unconfirmed |
| `comfort_model_duplicates_another` | two comfort models are numerically identical |
| `no_feasible_design` | the search found nothing satisfying every constraint |
