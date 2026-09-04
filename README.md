# Passive solar shelter design tool

Takes a location and a candidate shelter, works out hour by hour what the temperature
inside would be, compares it against what current construction would have done, and says
how much diesel the difference is worth.

Six layers do that work, and the rule that holds them together is that **physics happens
only in Layer 2**. Not in the API, not in the browser. If a temperature is ever calculated
outside the engine, something has gone wrong in the design and not just in the code.

| layer | what it is | where it lives | owner |
| --- | --- | --- | --- |
| 0, data | weather, terrain, ground temperatures, material database | `data/` | Mahi |
| 1, validation | Standard 140, ANSYS thermal bridge and fluid dynamics, calibration | `data/validation/`, `data/cfd/` | Mahi |
| 2, engine | simulation, geometry, comfort, search, uncertainty | `engine/` | Rudresh |
| 3, storage | run history, per surface results, projects, cached fronts | `api/src/shelter_api/store.py` | Abhinav |
| 4, API | HTTP surface over the engine | `api/` | Abhinav |
| 5, interface | the screens a DRDO officer uses | `web/` | Krishna and Madhav |

The interface is a seven step flow: Site Input → Climate Data → Design Parameters →
Simulation → Results → Compare Designs → Reports. Site input is three tiers with only the
first six fields shown; the simulation step carries the 3D view, plan, sections and
construction build-up as tabs over one geometry.

---

## Run the whole thing

### One command

```bash
python scripts/start.py
```

That is the whole cycle from a cold checkout. It verifies the install, finds EnergyPlus,
turns the Layer 0 delivery into `data/`, precomputes a few results so the first screen is
not empty, starts the API, regenerates the front end types from the running API, starts
the interface, and then prints how a number travels from the six questions on Site Input
to the figures on Results.

It refuses rather than guesses. No EnergyPlus, no Layer 0 delivery, a port already taken:
each stops the script with the specific remedy, and nothing is invented in place of a
missing input.

| flag | what it does |
| --- | --- |
| `--check` | verify the install and the data, then stop |
| `--layer0 path` | point at the Layer 0 zip or its extracted directory |
| `--reingest` | rebuild `data/` from the delivery |
| `--recompute` | run the precomputed simulations again |
| `--quick` | fewer precomputed designs, no uncertainty sweep |
| `--api-only` | skip the interface |
| `--api-port`, `--web-port` | move off 8000 and 5173 |
| `--serve-built` | serve the production build instead of the dev server |

EnergyPlus is a separate install and is required. The script looks at `ENERGYPLUS_DIR`,
then `tools/EnergyPlus`, then the usual system locations. Without it the API still starts,
`/api/health` reports `degraded`, and every simulation request returns 503 with an
explanation.

### In containers

```bash
python scripts/start.py --check
docker compose up --build
```

The `--check` first is not optional: the compose file mounts `./data` into the API
container, so the delivery has to be ingested on the host before the container can read
it. The API is then on http://localhost:8000 with its documentation at `/docs`, and the
interface is on http://localhost:8080. The image carries EnergyPlus 24.2.0 inside it.
Nothing in the demo path reaches the network.

The image build runs `energyplus --version` as its last step, so a missing shared library
fails the build rather than surfacing as a solver exit code during a demo.

### Piece by piece, for development

```bash
# once
cd engine && python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pip install -e ../api --no-deps
.venv/Scripts/python -m pip install "fastapi>=0.110,<1" "uvicorn[standard]>=0.27,<1" httpx
cd ../web && npm install

# turn the Layer 0 delivery into the data directory
cd .. && engine/.venv/Scripts/python scripts/ingest_layer0.py     --source build/layer0/layer_0 --out data

# run every stored design once and keep the results
ENERGYPLUS_DIR=... engine/.venv/Scripts/python scripts/precompute.py

# two terminals
SHELTER_DATA_DIR=./data ENERGYPLUS_DIR=... engine/.venv/Scripts/python     -m uvicorn shelter_api.main:app --port 8000
cd web && npm run dev
```

---

## How a number gets to the screen

```
DesignSpec ──► engine.geometry()   ──────────────► GeometryPayload ──► three.js
    │            (milliseconds, no simulation)          │
    │                                                   │ surface names match
    ▼                                                   ▼
engine.simulate()
    ├─ build constructions and geometry from one description
    ├─ write the EnergyPlus input file
    ├─ run EnergyPlus, read eplusout.sql, read eplusout.err
    ├─ comfort, heat balance, condensation, cost, buildability
    ├─ simulate the reference design and difference against it
    ├─ 8 corner runs for the uncertainty band
    └─ recommendations generated from those numbers
         │
         ▼
   SimulationResult ──► store (SQLite) ──► /api/simulate ──► TanStack Query ──► screens
```

Two identities travel with every result and they are not the same thing:

- `design_hash` identifies **this design under these run options** at this engine version.
  Layer 3 keys stored runs on it, and a repeat request is served from cache.
- `design_fingerprint` identifies **the design alone**. Pre computed fluid dynamics exports
  are named after it, because a CFD run belongs to a shelter and not to a run period.

---

## The data flow between people

Layer 0 hands over **files, not an API**. The build guide is explicit about this and it is
worth repeating: weather APIs go down, hackathon venues have bad wifi, and a demo that
depends on a network call depends on luck. A frozen weather file also makes results
reproducible, so when a judge asks to run something twice they get the same answer.
Fetching from a teammate once to obtain the files is fine; that is a build step. The engine
reads files.

Data flows one way through Layer 5. Krishna and Madhav consume the API, they do not supply
it. What they need already exists and is generated, not hand written:

```bash
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/lib/schema.d.ts
```

That is every endpoint and response shape, typed, straight from the running API. Re run
it whenever the engine schema changes and TypeScript will point at every place that needs
updating.

---

## What is real and what is not, right now

The Layer 0 delivery has landed and `data/` is built from it. What follows is the exact
state, and `data/layer0_manifest.json` is the machine readable version of the same thing.

**Real, and verified:**

- The **weather file**, a TMYx 2011&ndash;2025 year for Leh at 3,255.9 m. 8,760 hours, no
  loader warnings. Annual mean &minus;0.84 &deg;C, 6,878 heating degree days, zero cooling
  degree days. The engine checks the pressure column against the site elevation, because at
  this altitude a sea level column would make every air related heat transfer roughly a
  third too large and EnergyPlus would not warn. It reads 69,287 Pa against 67,857 expected,
  2.1 % apart, so Layer 0 got the altitude correction right.
- The **terrain**: a USGS SRTM elevation model, 10 km square at 200 m spacing, ridges to
  1,151 m above the site, plus the 36 direction horizon profile measured from it. The
  simulation shades with the horizon table and the 3D view stands on the mesh, so what
  blocks the sun on screen is what blocked it in the run.
- The **ground temperatures**, from the EPW header at 0.5 m depth.
- The **phase change materials**, four Rubitherm products.

**Not real yet, and stated as such on every screen:**

1. **Eleven of the fifteen materials** and **all three glazing systems** are filled from the
   ASHRAE Handbook of Fundamentals and carry `source_status: pending`. Layer 0 supplied five
   material rows.
2. **Delivered costs, availability and mason skill** were not supplied at all. The Layer 0
   source file says these must be replaced with locally sourced values, so every rupee
   figure is provisional.
3. **Absorptances** were not supplied. Every material carries the schema defaults. Surface
   colour changes solar gain on an exposed wall by a large fraction, so a painted Trombe
   absorber needs its own measured value.
4. **One Layer 0 row is wrong and was corrected.** Clay brick arrived at 0.17 W/mK and
   330&ndash;440 J/kgK at 1,980 kg/m&sup3;. Fired brick at that density conducts near 0.9 and
   stores near 800. It fails the engine's plausibility check on load. Because it is the
   baseline wall, an implausibly low conductivity would make current construction look
   insulated and shrink every headline difference, so ASHRAE values were substituted and the
   substitution is recorded in the manifest rather than made quietly.
5. **The reference design citations.** All three are uncited. The current practice assembly
   is the single most important citation in the project, because every headline difference is
   measured against it.
6. **The comfort model coefficients.** `imac_2016_nv` carries the widely quoted IMAC relation
   marked pending, and `imac_r_2022_nv` holds the same numbers until the residential paper is
   read.
7. **The fuel price**, deliberately zero, so a missing procurement figure shows as a withheld
   number instead of a plausible invented one.
8. **Layer 1 evidence.** Standard 140 results, the ANSYS junction psi values and any
   calibration, committed as files in `data/validation/`.

`/api/meta` returns these as a `provenance_gaps` list, the interface shows it, and
`SimulationResult.warnings` repeats the relevant ones on every run.

---

## Tests

```bash
cd engine && ENERGYPLUS_DIR=... .venv/Scripts/python -m pytest -q   # 235
cd api    && ENERGYPLUS_DIR=... ../engine/.venv/Scripts/python -m pytest -q   # 33
cd web    && npx tsc --noEmit && npm run build
```

The engine suite skips its 39 EnergyPlus tests when EnergyPlus is absent and runs them when
it is present. They are behavioural rather than structural: more insulation has to raise the
overnight minimum, more leakage has to lower it, an overhang has to cut incident solar on
the glazing it shades, added moisture has to raise indoor humidity and condensation hours.
A parameter that reaches the schema but never reaches the solver is exactly the kind of
defect those catch.

---

### The 3D view

The model tab is a real sun study, not a decoration. Everything in it is computed:

- **The sun** is placed from solar declination and the equation of time for the site's
  latitude, longitude and time zone, computed in Layer 2 and served by `/api/sun-path`.
  Its position drives a real directional light, so the shadows on screen are the shadows
  that geometry casts at that hour.
- **The skyline** is the terrain horizon table Layer 0 sampled, served by `/api/terrain`.
  It is the same table the simulation shades with, so what blocks the sun on screen is
  what blocked it in the run. The display radius can be changed for framing; that scales
  the ring uniformly and does not touch the elevation angles.
- **The painted faces** are per surface values EnergyPlus reported, keyed by the surface
  names the simulator uses. Hovering a face names it and reads its value back.
- **What is absent** is an air temperature field and a velocity field, because EnergyPlus
  computes neither. Those live behind the detailed analysis tab, come from a fluid
  dynamics run for one design, and carry the hour and the solver on the panel.

The panel under the view states astronomical daylight, how much of it is actually clear of
the ridge, and the difference. On the synthetic Leh horizon in mid January that is 9.8 h
against 7.3 h, so 2.5 h of collecting time is lost to the valley. That number falls
straight out of the horizon table and almost no team will have it.

---

## Layer documentation

- `engine/README.md` covers the engine in detail: what Layer 0 must supply, the surface
  naming rule that the 3D heatmap depends on, sign conventions, solver behaviour, and the
  measurement behind the decision not to build a surrogate.
- `http://localhost:8000/docs` is the API contract, generated from the code.
