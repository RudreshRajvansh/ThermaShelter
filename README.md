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

### One command, no network

```bash
docker compose up --build
```

The API is on http://localhost:8000 with its documentation at `/docs`, and the interface is
on http://localhost:8080. The image carries EnergyPlus 24.2.0 inside it and reads the
weather file, material database and pre computed results from `data/`, which is mounted in.
Nothing in the demo path reaches the network.

### Without Docker, for development

```bash
# once
cd engine && python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pip install -e ../api --no-deps
.venv/Scripts/python -m pip install "fastapi>=0.110,<1" "uvicorn[standard]>=0.27,<1" httpx
cd ../web && npm install

# generate the synthetic development inputs, and precompute a few results
cd .. && ENERGYPLUS_DIR=... engine/.venv/Scripts/python scripts/build_data.py

# two terminals
SHELTER_DATA_DIR=./data ENERGYPLUS_DIR=... engine/.venv/Scripts/python -m uvicorn shelter_api.main:app --port 8000
cd web && npm run dev
```

EnergyPlus is a separate install and is required. Point `ENERGYPLUS_DIR` at the directory
holding `energyplus` and `Energy+.idd`. Without it the API still starts, `/api/health`
reports `degraded`, and every simulation request returns 503 with an explanation. It will
not fabricate a result.

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

The machinery is complete and tested. The **data is not**, and the tool says so on every
screen rather than hiding it.

Running `scripts/build_data.py` generates a synthetic weather year from a sinusoid and a
clear sky model, and a material database of placeholder properties. Every record carries
`source_status: pending`. `/api/meta` returns a `provenance_gaps` list, the interface shows
it, and `SimulationResult.warnings` repeats it on every run. Nothing produced from these
inputs is a result.

What has to be replaced before a number is presentable:

1. **The weather file.** A real Leh EPW from climate.onebuilding.org, or one built from
   NASA POWER. The engine checks the pressure column against the site elevation and refuses
   to be quiet about a mismatch, because at 3,500 m a sea level pressure column makes every
   air related heat transfer roughly a third too large and EnergyPlus will not warn.
2. **The material database.** Real properties with real citations. The engine rejects any
   material with no source and no display colour rather than defaulting them.
3. **The comfort model coefficients.** `imac_2016_nv` carries the widely quoted IMAC
   relation marked pending, and `imac_r_2022_nv` is a placeholder holding the same numbers
   until the residential paper is read.
4. **The reference design citations.** All three are uncited. The current practice assembly
   is the single most important citation in the project, because every headline difference
   is measured against it.
5. **The fuel price**, which is deliberately zero so a missing procurement figure shows as
   a withheld number instead of a plausible invented one.
6. **Layer 1 evidence.** Standard 140 results, the ANSYS junction psi values and any
   calibration, committed as files in `data/validation/`.

---

## Tests

```bash
cd engine && ENERGYPLUS_DIR=... .venv/Scripts/python -m pytest -q   # 233
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
